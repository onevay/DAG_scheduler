"""Агент: `python -m expsched.agent --scheduler http://127.0.0.1:8000 --id laptop`.

Ёмкость по умолчанию — машина целиком за вычетом запаса для неё самой: ядра минус одно, ОЗУ минус 2 ГиБ,
GPU — по `nvidia-smi`, если он есть. Токен — из EXPSCHED_AGENT_TOKEN.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from expsched.agent.core import Agent
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


def serve(agent: Agent, scheduler: Scheduler) -> None:
    """Hello, затем по кругу: опрос команд, слежение за контейнерами, события. Сбой связи — заново."""
    agent.reconcile()
    while True:
        try:
            agent.welcome(scheduler.call("hello", agent.hello(), HelloAck))
            while True:
                busy = any(not a.done for a in agent.attempts.values())
                wait = 1 if busy or agent.outbox else 25
                agent.handle(scheduler.call("poll", agent.poll(wait), PollReply, timeout=wait + 30))
                agent.watch()
                if batch := agent.batch():
                    agent.acked(scheduler.call("events", batch, EventsAck))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            log.warning("scheduler is unreachable: %s; retrying in 5 s", error)
            time.sleep(5)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m expsched.agent", description="Агент expsched")
    parser.add_argument("--scheduler", default="http://127.0.0.1:8000")
    parser.add_argument("--id", required=True, help="id агента, как в поле agent узлов")
    parser.add_argument("--runtime", choices=["fake"], default="fake")
    parser.add_argument("--state", type=Path, default=Path(".expsched-agent"), help="журнал и чекпоинты")
    parser.add_argument("--cpu", type=int, help="ядер под задачи (по умолчанию — машина минус одно)")
    parser.add_argument("--ram-mb", type=int, help="ОЗУ под задачи, МиБ (по умолчанию — машина минус 2 ГиБ)")
    parser.add_argument("--gpu", type=int, help="GPU под задачи (по умолчанию — по nvidia-smi)")
    parser.add_argument("--max-containers", type=int, default=4)
    parser.add_argument("--allow-image", action="append", default=[], help="разрешённый образ задач")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    capacity = machine_capacity()
    for name, value in (("cpu", args.cpu), ("ram_mb", args.ram_mb), ("gpu", args.gpu)):
        if value is not None:
            capacity[name] = value
    runtime: Runtime = FakeRuntime()
    agent = Agent(args.id, runtime, args.state, capacity, args.max_containers, args.allow_image)
    log.info("agent %s: runtime %s, capacity %s", args.id, runtime.name, capacity)
    serve(agent, Scheduler(args.scheduler, args.id, os.environ.get("EXPSCHED_AGENT_TOKEN")))


if __name__ == "__main__":
    main()
