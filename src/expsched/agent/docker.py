"""Среда запуска Docker или Podman через их CLI: у обоих одинаковые команды run, inspect, ps, kill, rm."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from expsched.agent.runtime import State
from expsched.contract.agent import StartCommand

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
WORKLOAD_IMAGE = "expsched-workload:local"


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
        env = {
            "EXPSCHED_CHECKPOINT_DIR": "/checkpoint",
            "EXPSCHED_RESUME_FROM": str(command.resume_from),
            "EXPSCHED_WORK": str(command.work),
            "EXPSCHED_TIME_SCALE_MS": str(command.time_scale_ms),
            "EXPSCHED_CHECKPOINT_INTERVAL": str(command.checkpoint_interval or 0),
        }
        if command.container:
            env.update(command.container.env)
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


def _name(key: str) -> str:
    return "expsched-" + key.replace(":", "-")
