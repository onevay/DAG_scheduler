"""Среда запуска Docker или Podman через их CLI: у обоих одинаковые команды run, inspect, ps, kill, rm."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from expsched.agent.runtime import WORKLOAD_IMAGE, State
from expsched.contract.agent import StartCommand, Usage

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def run_cli(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=120, check=False)


class DockerRuntime:
    """Контейнер попытки помечен метками агента и попытки; имя — `expsched-<run>-<task>-<attempt>`.

    Лимиты — из ресурсов задачи, без подкачки (иначе нехватка памяти не превратится в OOM), без привилегий
    и capabilities. Каталог чекпоинтов задачи смонтирован в /checkpoint; на Linux контейнер работает от
    пользователя агента, чтобы писать в этот каталог. Встроенная нагрузка — без сети.
    """

    def __init__(
        self,
        agent_id: str,
        binary: Literal["docker", "podman"] = "docker",
        workload_image: str = WORKLOAD_IMAGE,
        run: Run = run_cli,
    ) -> None:
        self.agent_id = agent_id
        self.name: Literal["docker", "podman"] = binary
        self.workload_image = workload_image
        self.run = run
        self.seen: dict[str, str] = {}  # время последней отданной строки журнала, по попытке

    def start(self, command: StartCommand, checkpoint_dir: Path) -> str:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        limits = command.limits
        args = [self.name, "run", "-d", "--name", _name(command.key)]
        args += ["--label", f"expsched.agent={self.agent_id}", "--label", f"expsched.attempt={command.key}"]
        args += ["--security-opt", "no-new-privileges", "--cap-drop", "ALL"]
        if limits.get("cpu"):
            args += ["--cpus", str(limits["cpu"])]
        if limits.get("ram_mb"):
            args += ["--memory", f"{limits['ram_mb']}m", "--memory-swap", f"{limits['ram_mb']}m"]
        if limits.get("gpu"):
            args += ["--gpus", str(limits["gpu"])]
        if sys.platform != "win32":
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        args += ["-v", f"{checkpoint_dir.resolve()}:/checkpoint"]
        own = {  # что агент сообщает нагрузке; окружение задачи этого не перекрывает
            "EXPSCHED_CHECKPOINT_DIR": "/checkpoint",
            "EXPSCHED_RESUME_FROM": str(command.resume_from),
            "EXPSCHED_WORK": str(command.work),
            "EXPSCHED_TIME_SCALE_MS": str(command.time_scale_ms),
            "EXPSCHED_CHECKPOINT_INTERVAL": str(command.checkpoint_interval or 0),
        }
        env = {**(command.container.env if command.container else {}), **own}
        if command.container and command.container.image != self.workload_image:
            image, tail = command.container.image, list(command.container.command)
        else:
            args += ["--network", "none"]
            image, tail = self.workload_image, []
        for key, value in sorted(env.items()):
            args += ["-e", f"{key}={value}"]
        done = self.run([*args, image, *tail])
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip()[-500:] or f"{self.name} run failed")
        return done.stdout.strip()[:12]

    def stop(self, key: str) -> None:
        self.run([self.name, "rm", "-f", _name(key)])  # остановить и убрать; нет контейнера — не ошибка
        self.seen.pop(key, None)

    def kill(self, key: str) -> None:
        self.run(
            [self.name, "kill", _name(key)]
        )  # SIGKILL: контейнер остаётся с кодом 137, его увидит inspect

    def checkpoint(self, key: str) -> None:
        self.run([self.name, "kill", "--signal", "USR1", _name(key)])

    def inspect(self, key: str) -> State:
        done = self.run([self.name, "inspect", "--format", "{{json .State}}", _name(key)])
        if done.returncode != 0:
            return State("missing")
        state = json.loads(done.stdout)
        if state.get("OOMKilled"):
            return State("oom")
        if state.get("Status") in ("exited", "dead", "stopped"):
            return State("exited", int(state.get("ExitCode", 1)))
        return State("running")  # created, running, paused, restarting — контейнер ещё жив

    def keys(self) -> list[str]:
        done = self.run(
            [
                self.name,
                "ps",
                "-a",
                "--filter",
                f"label=expsched.agent={self.agent_id}",
                "--format",
                '{{.Label "expsched.attempt"}}',
            ]  # fmt: skip
        )
        return sorted(line for line in done.stdout.split() if line) if done.returncode == 0 else []

    def usage(self, keys: list[str]) -> dict[str, Usage]:
        """Одним `stats --no-stream` на все: он ждёт замера CPU около секунды."""
        if not keys:
            return {}
        names = {_name(key): key for key in keys}
        done = self.run([self.name, "stats", "--no-stream", "--format", "{{json .}}", *names])
        out = {}
        for line in done.stdout.splitlines():
            try:
                row = json.loads(line)
                key = names[row["Name"].lstrip("/")]
                cpu = float(row["CPUPerc"].rstrip("%") or 0)
                out[key] = Usage(cpu_milli=round(cpu * 10), ram_mb=_mebibytes(row["MemUsage"].split("/")[0]))
            except (ValueError, KeyError, AttributeError):
                continue  # контейнер уже ушёл или строка не по формату — без замера
        return out

    def logs(self, key: str) -> list[str]:
        """Строки после последней отданной: `--since` по её времени, повтор этой же секунды отбрасывается."""
        args = [self.name, "logs", "--timestamps"]
        if key in self.seen:
            args += ["--since", self.seen[key]]
        done = self.run([*args, _name(key)])
        if done.returncode != 0:
            return []
        out = []
        for line in (done.stdout + done.stderr).splitlines():
            stamp, _, text = line.partition(" ")
            if key in self.seen and stamp <= self.seen[key]:
                continue
            self.seen[key] = max(stamp, self.seen.get(key, ""))
            out.append(text)
        return out


UNITS = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000**2, "mib": 1024**2, "gb": 1000**3, "gib": 1024**3}


def _mebibytes(text: str) -> int:
    """`12.5MiB`, `1.2GB`, `512kB` → МиБ."""
    match = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]+)\s*", text)
    if not match:
        raise ValueError(f"unknown size {text!r}")
    return round(float(match[1]) * UNITS[match[2].lower()] / 1024**2)


def _name(key: str) -> str:
    return "expsched-" + key.replace(":", "-")
