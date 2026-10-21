"""Контракт между движком и политикой: снимок состояния, решение и протокол политики.

Политика получает Snapshot и возвращает решения, движок проверяет и применяет их. Типы лежат в domain,
чтобы политики не зависели от движка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from expsched.domain.analysis import Analysis
from expsched.domain.models import Task
from expsched.domain.resources import Resources


@dataclass(frozen=True)
class RunInfo:
    """Выполняющаяся попытка."""

    worker: str
    start: int
    attempt: int
    end_pred: int  # прогнозное окончание: start + остаток работы


@dataclass(frozen=True)
class Snapshot:
    """Неизменяемое состояние на момент решения."""

    t: int
    tasks: dict[str, Task]
    ready: tuple[str, ...]  # задачи READY, отсортированные по id
    running: dict[str, RunInfo]
    rem: dict[str, int]  # остаток работы по задаче
    free: dict[str, Resources]  # свободные ресурсы узла (у недоступного узла пусто)
    up: dict[str, bool]
    eligible: dict[str, tuple[str, ...]]  # допустимые узлы задачи
    analysis: Analysis
    ready_since: dict[str, int]
    preempt_count: dict[str, int]


Action = Literal["START", "PREEMPT", "RESERVE"]


@dataclass(frozen=True)
class Decision:
    action: Action
    task: str
    worker: str
    reason: dict[str, Any] = field(default_factory=dict)  # причина для журнала; `code` — код причины


class Policy(Protocol):
    """Решает только по снимку: без побочных эффектов и детерминированно."""

    name: str

    def decide(self, snapshot: Snapshot) -> list[Decision]: ...
