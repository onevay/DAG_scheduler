"""Исполнитель попыток: откуда движок узнаёт, когда попытка закончится и сколько работы она сохранила.

В симуляции всё известно заранее: попытка идёт ровно остаток работы, сохранена работа до последнего
чекпоинта по интервалу задачи. При прогоне на агентах это факты извне: окончание и сбой подаются в движок
событиями (`Simulator.inject`), сохранённая работа — по чекпоинтам, о которых сообщил агент.
"""

from __future__ import annotations

from typing import Protocol

from expsched.domain.models import Task
from expsched.domain.snapshot import RunInfo


class Executor(Protocol):
    def finish_at(self, task: Task, run: RunInfo) -> int | None:
        """Когда попытка закончится; None — окончание придёт извне."""
        ...

    def saved(self, task: Task, key: str, run: RunInfo, now: int) -> int:
        """Сколько работы прерванная в `now` попытка сохранила (от начала самой попытки)."""
        ...


class Simulated:
    """Попытка идёт ровно остаток работы; сохранено всё до последнего чекпоинта по интервалу задачи."""

    def finish_at(self, task: Task, run: RunInfo) -> int | None:
        return run.end_pred

    def saved(self, task: Task, key: str, run: RunInfo, now: int) -> int:
        elapsed = now - run.start
        return task.ckpt * (elapsed // task.ckpt) if task.ckpt else 0


class External:
    """Окончание — факт извне; сохранено — то, о чём попытка сообщила последним чекпоинтом."""

    def __init__(self) -> None:
        self.checkpoints: dict[tuple[str, int], int] = {}

    def checkpoint(self, key: str, attempt: int, saved: int) -> None:
        """Попытка сохранила `saved` работы от своего начала; меньшее значение после большего не в счёт."""
        self.checkpoints[(key, attempt)] = max(saved, self.checkpoints.get((key, attempt), 0))

    def finish_at(self, task: Task, run: RunInfo) -> int | None:
        return None

    def saved(self, task: Task, key: str, run: RunInfo, now: int) -> int:
        return min(self.checkpoints.get((key, run.attempt), 0), run.end_pred - run.start)
