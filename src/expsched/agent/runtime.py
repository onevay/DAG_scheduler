"""Среда запуска контейнеров для агента и её подделка для тестов."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from expsched.contract.agent import StartCommand

Status = Literal["running", "exited", "oom", "missing"]


@dataclass(frozen=True)
class State:
    status: Status
    exit_code: int | None = None


class Runtime(Protocol):
    """Контейнеры агента помечены ключом попытки `run:task:attempt` — по нему их находят снова."""

    @property
    def name(self) -> Literal["docker", "podman", "fake", "gravesentinel"]: ...

    def start(self, command: StartCommand, checkpoint_dir: Path) -> str: ...  # id контейнера

    def stop(self, key: str) -> None: ...

    def checkpoint(self, key: str) -> None: ...  # попросить задачу сохраниться сейчас

    def inspect(self, key: str) -> State: ...

    def keys(self) -> list[str]: ...  # попытки, контейнеры которых есть в среде


def read_progress(checkpoint_dir: Path) -> int | None:
    """Сохранённая работа из `checkpoint.json` задачи; None — чекпоинта нет или он недописан."""
    try:
        return int(json.loads((checkpoint_dir / "checkpoint.json").read_text(encoding="utf-8"))["progress"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def write_progress(checkpoint_dir: Path, progress: int) -> None:
    """Атомарно: читатель видит либо прежний чекпоинт, либо новый."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_dir / "checkpoint.json.tmp"
    tmp.write_text(json.dumps({"progress": progress}), encoding="utf-8")
    tmp.replace(checkpoint_dir / "checkpoint.json")


@dataclass
class _FakeContainer:
    command: StartCommand
    checkpoint_dir: Path
    done: float  # сделано работы, в единицах модели
    state: State


class FakeRuntime:
    """Контейнеры без контейнеров: работа идёт по часам `advance(ms)` с масштабом из команды, чекпоинты
    пишутся в каталог задачи, как у встроенной нагрузки. `fail`, `oom`, `vanish` — сбои для тестов."""

    name: Literal["fake"] = "fake"

    def __init__(self) -> None:
        self.containers: dict[str, _FakeContainer] = {}
        self.started = 0

    def start(self, command: StartCommand, checkpoint_dir: Path) -> str:
        self.started += 1
        self.containers[command.key] = _FakeContainer(
            command, checkpoint_dir, command.resume_from, State("running")
        )
        return f"fake-{self.started}"

    def stop(self, key: str) -> None:
        self.containers.pop(key, None)

    def checkpoint(self, key: str) -> None:
        c = self.containers.get(key)
        if c and c.state.status == "running":
            write_progress(c.checkpoint_dir, int(c.done))

    def inspect(self, key: str) -> State:
        c = self.containers.get(key)
        return c.state if c else State("missing")

    def keys(self) -> list[str]:
        return sorted(self.containers)

    def advance(self, ms: int) -> None:
        for c in self.containers.values():
            if c.state.status != "running":
                continue
            total = c.command.resume_from + c.command.work
            before = c.done
            c.done = min(total, c.done + ms / c.command.time_scale_ms)
            step = c.command.checkpoint_interval
            if step:  # последний пройденный чекпоинт; в момент окончания чекпоинт уже не нужен
                mark = (min(int(c.done), total - 1)) // step * step
                if mark > int(before) // step * step:
                    write_progress(c.checkpoint_dir, mark)
            if c.done >= total:
                c.state = State("exited", 0)

    def fail(self, key: str, exit_code: int = 1) -> None:
        self.containers[key].state = State("exited", exit_code)

    def oom(self, key: str) -> None:
        self.containers[key].state = State("oom")

    def vanish(self, key: str) -> None:
        del self.containers[key]
