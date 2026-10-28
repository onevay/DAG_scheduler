"""Агент: `python -m expsched.agent --scheduler http://127.0.0.1:8000 --id laptop` или `--config agent.toml`;
`python -m expsched.agent check …` — проверить среду, токен и связь, ничего не запуская.

Среда по умолчанию — Docker (`--runtime podman` для Podman, `gravesentinel` — снимки CRIU, `fake` — без
контейнеров, для проверок). Ёмкость по умолчанию — машина целиком за вычетом запаса для неё самой: ядра минус
одно, ОЗУ минус 2 ГиБ, GPU — по `nvidia-smi`, если он есть. Токен — из EXPSCHED_AGENT_TOKEN или `token_file`.
SIGTERM и SIGINT завершают работу после текущего обмена; контейнеры остаются и подхватываются при следующем
запуске (`--stop-on-exit` — остановить их).
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from expsched.agent.config import Backoff, Settings, configure_logging, settings
from expsched.agent.core import Agent
from expsched.agent.docker import WORKLOAD_IMAGE, DockerRuntime, Run, run_cli
from expsched.agent.gravesentinel import client as gs
from expsched.agent.gravesentinel.runtime import WORKLOAD_IMAGE as GS_WORKLOAD_IMAGE
from expsched.agent.gravesentinel.runtime import GravesentinelRuntime
from expsched.agent.runtime import FakeRuntime, Runtime
from expsched.contract.agent import EventsAck, HelloAck, PollReply

log = logging.getLogger("expsched.agent")
Reply = TypeVar("Reply", bound=BaseModel)


def total_ram_mb() -> int | None:
    if sys.platform == "win32":

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong)]
            _fields_ += [
                (n, ctypes.c_ulonglong) for n in ("free", "page", "page_free", "virt", "virt_free", "ext")
            ]

        status = Status()
        status.length = ctypes.sizeof(Status)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.total >> 20) if ok else None
    try:
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    return int(pages * size) >> 20


def gpu_count() -> int:
    if shutil.which("nvidia-smi") is None:
        return 0
    try:
        out = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("GPU "))


def machine_capacity(reserve_cpu: int = 1, reserve_ram_mb: int = 2048) -> dict[str, int]:
    capacity = {"cpu": max(1, (os.cpu_count() or 1) - reserve_cpu)}
    ram = total_ram_mb()
    if ram:
        capacity["ram_mb"] = max(512, ram - reserve_ram_mb)
    if gpus := gpu_count():
        capacity["gpu"] = gpus
    return capacity


class Scheduler:
    def __init__(self, url: str, agent_id: str, token: str | None) -> None:
        self.base = f"{url.rstrip('/')}/v1/agents/{agent_id}"
        self.token = token

    def call(self, path: str, body: BaseModel, reply: type[Reply], timeout: float = 60) -> Reply:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = body.model_dump_json().encode()
        request = urllib.request.Request(f"{self.base}/{path}", data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — адрес задаёт владелец
            return reply.model_validate(json.loads(response.read()))


class Calls(Protocol):
    def call(self, path: str, body: BaseModel, reply: type[Reply], timeout: float = 60) -> Reply: ...


def serve(
    agent: Agent, scheduler: Calls, stop: threading.Event, backoff: Backoff, stop_on_exit: bool = False
) -> None:
    """Hello, затем по кругу: опрос команд, слежение за контейнерами, события. Сбой связи — пауза (растущая)
    и заново с hello. `stop` — выйти после текущего обмена; журнал к этому моменту уже на диске."""
    agent.reconcile()
    clock = time.monotonic()
    while not stop.is_set():
        try:
            agent.welcome(scheduler.call("hello", agent.hello(), HelloAck))
            backoff.reset()
            while not stop.is_set():
                busy = any(not a.done for a in agent.attempts.values())
                wait = 1 if busy or agent.outbox else 25
                reply = scheduler.call("poll", agent.poll(wait), PollReply, timeout=wait + 30)
                if isinstance(agent.runtime, FakeRuntime):  # у подделки работа идёт по часам стены —
                    now = time.monotonic()  # только у уже запущенных: новые стартуют с нуля
                    agent.runtime.advance(round((now - clock) * 1000))
                    clock = now
                agent.handle(reply)
                agent.watch()
                if batch := agent.batch():
                    agent.acked(scheduler.call("events", batch, EventsAck))
        except (urllib.error.URLError, OSError) as error:  # HTTPError — тоже URLError
            delay = backoff.next()
            log.warning("scheduler: %s; retrying in %.1f s", describe(error), delay)
            stop.wait(delay)
    if stop_on_exit:
        agent.stop_all()
        if (
            batch := agent.batch()
        ):  # сообщить об остановленных, если сервер на связи; иначе — при следующем запуске
            with contextlib.suppress(urllib.error.URLError, OSError):
                agent.acked(scheduler.call("events", batch, EventsAck, timeout=10))
    log.info("agent %s stopped", agent.agent_id)


def describe(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        try:
            body = json.loads(error.read())["error"]
            return f"HTTP {error.code} {body['code']}: {body['message']}"
        except (ValueError, KeyError, TypeError, OSError):
            return f"HTTP {error.code}"
    return str(getattr(error, "reason", error))


def build(s: Settings) -> Agent:
    """Агент по настройкам: среда, ёмкость (заданное поверх автоопределения), журнал."""
    capacity = machine_capacity()
    for name, value in (("cpu", s.cpu), ("ram_mb", s.ram_mb), ("gpu", s.gpu)):
        if value is not None:
            capacity[name] = value
    max_containers = s.max_containers
    runtime: Runtime
    if s.runtime == "fake":
        runtime = FakeRuntime()
    elif s.runtime == "gravesentinel":
        # Пары CPU гипервизора: по контейнеру на пару, у контейнера — один CPU
        client = gs.Client(lambda: gs.unix_transport(s.socket, server_uid=s.hypervisor_uid))
        runtime = GravesentinelRuntime(client, s.state, s.workload_image or GS_WORKLOAD_IMAGE)
        capacity["cpu"] = min(capacity.get("cpu", len(gs.CPU_PAIRS)), len(gs.CPU_PAIRS))
        capacity.pop("gpu", None)
        max_containers = min(max_containers, len(gs.CPU_PAIRS))
    else:
        runtime = DockerRuntime(s.id, s.runtime, s.workload_image or WORKLOAD_IMAGE)
    return Agent(s.id, runtime, s.state, capacity, max_containers, s.allow_images)


def probe_runtime(s: Settings, run: Run = run_cli) -> str:
    """Среда готова принимать контейнеры: ответ — что о ней известно; нет — исключение."""
    if s.runtime == "fake":
        return "fake runtime, no containers"
    if s.runtime == "gravesentinel":
        client = gs.Client(lambda: gs.unix_transport(s.socket, server_uid=s.hypervisor_uid))
        try:
            health = client.health()
        finally:
            client.close()
        return f"gravesentinel at {s.socket}: {health['status']}, queue {health['queue']}"
    done = run([s.runtime, "info", "--format", "{{.ServerVersion}}"])
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[-300:] or f"{s.runtime} info failed")
    image = s.workload_image or WORKLOAD_IMAGE
    if run([s.runtime, "image", "inspect", image]).returncode != 0:
        raise RuntimeError(
            f"{s.runtime} {done.stdout.strip()} has no workload image {image}: make workload-image"
        )
    return f"{s.runtime} {done.stdout.strip()}, workload image {image}"


def check(s: Settings, agent: Agent, scheduler: Calls, probe: Callable[[], str], hello: bool = True) -> int:
    """Перед запуском службы: среда, каталог состояния, токен и связь с планировщиком. 0 — всё готово.
    `hello=False` — без связи: hello представил бы агента серверу, даже если его так и не запустят."""
    failed = 0

    def report(ok: bool, what: str) -> None:
        nonlocal failed
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {what}")

    try:
        report(True, f"runtime: {probe()}")
    except Exception as error:  # любая причина неготовности среды — в отчёт, не в трассировку
        report(False, f"runtime {s.runtime}: {error}")
    try:
        (s.state / ".check").write_text("ok", encoding="utf-8")
        (s.state / ".check").unlink()
        report(True, f"state directory {s.state.resolve()}")
    except OSError as error:
        report(False, f"state directory {s.state}: {error}")
    if not s.token():
        print(
            "warn no token: EXPSCHED_AGENT_TOKEN or token_file; a scheduler with agents enabled will refuse"
        )
    if not hello:
        return 1 if failed else 0
    try:
        ack = scheduler.call("hello", agent.hello(), HelloAck, timeout=10)
        report(True, f"scheduler {s.scheduler}: heartbeat {ack.heartbeat_timeout_s} s")
    except (urllib.error.URLError, OSError) as error:
        report(False, f"scheduler {s.scheduler}: {describe(error)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m expsched.agent", description="Агент expsched")
    parser.add_argument("command", nargs="?", choices=["run", "check"], default="run")
    parser.add_argument(
        "--no-hello", action="store_true", help="check: не проверять связь (сервер запускает агента сам)"
    )
    parser.add_argument("--config", type=Path, help="файл настроек agent.toml (таблица [agent])")
    parser.add_argument("--scheduler", help="адрес планировщика (http://127.0.0.1:8000)")
    parser.add_argument("--id", help="id агента, как в поле agent узлов")
    parser.add_argument("--runtime", choices=["docker", "podman", "fake", "gravesentinel"])
    parser.add_argument("--workload-image", help="образ встроенной нагрузки")
    parser.add_argument("--socket", help="сокет гипервизора gravesentinel")
    parser.add_argument("--hypervisor-uid", type=int, help="пользователь, от которого работает гипервизор")
    parser.add_argument("--state", help="журнал и чекпоинты (.expsched-agent)")
    parser.add_argument("--cpu", type=int, help="ядер под задачи (по умолчанию — машина минус одно)")
    parser.add_argument("--ram-mb", type=int, help="ОЗУ под задачи, МиБ (по умолчанию — машина минус 2 ГиБ)")
    parser.add_argument("--gpu", type=int, help="GPU под задачи (по умолчанию — по nvidia-smi)")
    parser.add_argument("--max-containers", type=int)
    parser.add_argument("--allow-image", dest="allow_images", action="append", help="разрешённый образ задач")
    parser.add_argument("--token-file", help="файл с токеном агента (если нет EXPSCHED_AGENT_TOKEN)")
    parser.add_argument("--log-format", choices=["text", "json"])
    parser.add_argument(
        "--stop-on-exit", action="store_true", default=None, help="остановить контейнеры при выходе"
    )
    args = parser.parse_args(argv)
    overrides = {k: v for k, v in vars(args).items() if k not in ("command", "config", "no_hello")}
    try:
        s = settings(args.config, overrides)
    except (ValueError, OSError) as error:  # TOMLDecodeError — тоже ValueError
        parser.error(str(error))
    configure_logging(s.log_format)
    agent = build(s)
    scheduler = Scheduler(s.scheduler, s.id, s.token())
    if args.command == "check":
        sys.exit(check(s, agent, scheduler, lambda: probe_runtime(s), hello=not args.no_hello))
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    log.info("agent %s: runtime %s, capacity %s", s.id, agent.runtime.name, agent.capacity)
    serve(agent, scheduler, stop, Backoff(s.retry_max_s), s.stop_on_exit)


if __name__ == "__main__":
    main()
