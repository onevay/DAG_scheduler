"""Среда запуска контейнеров для агента и её подделка для тестов."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from expsched.contract.agent import StartCommand, Usage

Status = Literal["running", "exited", "oom", "missing"]
WORKLOAD_IMAGE = "expsched-workload:local"  # образ встроенной нагрузки: `make workload-image`


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

    def kill(self, key: str) -> None: ...  # оборвать, как сбой: контейнер остаётся упавшим или пропадает

    def checkpoint(self, key: str) -> None: ...  # попросить задачу сохраниться сейчас

    def inspect(self, key: str) -> State: ...

    def keys(self) -> list[str]: ...  # попытки, контейнеры которых есть в среде

    def usage(self, keys: list[str]) -> dict[str, Usage]: ...  # чего среда не знает — того нет в ответе

    def logs(self, key: str) -> list[str]: ...  # новые строки журнала контейнера с прошлого вызова


def read_progress(checkpoint_dir: Path) -> int | None:
    """Сохранённая работа из `checkpoint.json` задачи; None — чекпоинта нет или он недописан."""
    try:
        return int(json.loads((checkpoint_dir / "checkpoint.json").read_text(encoding="utf-8"))["progress"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def read_total(checkpoint_dir: Path) -> int | None:
    """Вся работа задачи из `checkpoint.json`, если задача её сообщает (`total`); иначе None."""
    try:
        total = json.loads((checkpoint_dir / "checkpoint.json").read_text(encoding="utf-8")).get("total")
    except (FileNotFoundError, ValueError, AttributeError):
        return None
    return int(total) if isinstance(total, int) and total > 0 else None


def write_progress(checkpoint_dir: Path, progress: int, total: int | None = None) -> None:
    """Атомарно: читатель видит либо прежний чекпоинт, либо новый."""
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    tmp = checkpoint_dir / "checkpoint.json.tmp"
    body = {"progress": progress} if total is None else {"progress": progress, "total": total}
    tmp.write_text(json.dumps(body), encoding="utf-8")
    tmp.replace(checkpoint_dir / "checkpoint.json")


def true_total(command: StartCommand) -> int:
    """Вся работа встроенной нагрузки: `EXPSCHED_TOTAL` из окружения задачи — её настоящая работа, которой не
    знает планировщик (так задаётся задача с неизвестной длительностью); иначе — то, что он прислал."""
    env = command.container.env if command.container else {}
    return (
        int(env["EXPSCHED_TOTAL"])
        if env.get("EXPSCHED_TOTAL", "").isdigit()
        else command.resume_from + command.work
    )


@dataclass
class _FakeContainer:
    command: StartCommand
    checkpoint_dir: Path
    done: float  # сделано работы, в единицах модели
    state: State
    lines: list[str]  # журнал контейнера, ещё не отданный агенту


class FakeRuntime:
    """Контейнеры без контейнеров: работа идёт по часам `advance(ms)` с масштабом и скоростью узла из
    команды, чекпоинты пишутся в каталог задачи, как у встроенной нагрузки. `fail`, `oom`, `vanish` —
    сбои для тестов."""

    name: Literal["fake"] = "fake"
    workload_image = WORKLOAD_IMAGE  # «запускает» встроенную нагрузку и с этим образом

    def __init__(self) -> None:
        self.containers: dict[str, _FakeContainer] = {}
        self.started = 0

    def start(self, command: StartCommand, checkpoint_dir: Path) -> str:
        self.started += 1
        total = true_total(command)
        self.containers[command.key] = _FakeContainer(
            command,
            checkpoint_dir,
            command.resume_from,
            State("running"),
            [f"start: {command.resume_from} of {total} units"],
        )
        return f"fake-{self.started}"

    def stop(self, key: str) -> None:
        self.containers.pop(key, None)

    def kill(self, key: str) -> None:
        c = self.containers.get(key)
        if c and c.state.status == "running":
            c.state = State("exited", 137)
            c.lines.append("killed: SIGKILL")

    def checkpoint(self, key: str) -> None:
        c = self.containers.get(key)
        if c and c.state.status == "running":
            write_progress(c.checkpoint_dir, int(c.done), true_total(c.command))

    def inspect(self, key: str) -> State:
        c = self.containers.get(key)
        return c.state if c else State("missing")

    def keys(self) -> list[str]:
        return sorted(self.containers)

    def advance(self, ms: int) -> None:
        for c in self.containers.values():
            if c.state.status != "running":
                continue
            total = true_total(c.command)
            before = c.done
            c.done = min(total, c.done + ms / c.command.time_scale_ms * c.command.speed)
            step = c.command.checkpoint_interval
            if step:  # последний пройденный чекпоинт; в момент окончания чекпоинт уже не нужен
                mark = (min(int(c.done), total - 1)) // step * step
                if mark > int(before) // step * step:
                    write_progress(c.checkpoint_dir, mark, total)
                    c.lines.append(f"checkpoint: {mark} units")
            if c.done >= total:
                c.state = State("exited", 0)
                c.lines.append(f"done: {total} units")

    def usage(self, keys: list[str]) -> dict[str, Usage]:
        """Идущий контейнер занимает девять десятых своих ядер и половину своей памяти."""
        out = {}
        for key in keys:
            c = self.containers.get(key)
            if c and c.state.status == "running":
                limits = c.command.limits
                out[key] = Usage(cpu_milli=limits.get("cpu", 1) * 900, ram_mb=limits.get("ram_mb", 128) // 2)
        return out

    def logs(self, key: str) -> list[str]:
        c = self.containers.get(key)
        if c is None:
            return []
        lines, c.lines = c.lines, []
        return lines

    def fail(self, key: str, exit_code: int = 1) -> None:
        self.containers[key].state = State("exited", exit_code)
        self.containers[key].lines.append(f"error: exit code {exit_code}")

    def oom(self, key: str) -> None:
        self.containers[key].state = State("oom")
        self.containers[key].lines.append("killed: out of memory")

    def vanish(self, key: str) -> None:
        del self.containers[key]
