"""Контракт между движком и политикой: снимок состояния, решение и протокол политики.

Политика получает Snapshot и возвращает решения, движок проверяет и применяет их. Типы лежат в domain,
чтобы политики не зависели от движка.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from expsched.domain.analysis import Analysis
from expsched.domain.models import Task
from expsched.domain.resources import Resources
from expsched.domain.states import TaskStatus


@dataclass(frozen=True)
class RunInfo:
    """Выполняющаяся попытка."""

    worker: str
    start: int
    attempt: int
    end_pred: int  # прогнозное окончание: start + время остатка; у прогона на агентах может быть в прошлом
    work: int = 0  # остаток работы к началу попытки; 0 — не задан (тогда end_pred - start)
    speed: float = 1.0  # скорость узла попытки


@dataclass(frozen=True)
class Snapshot:
    """Состояние на момент решения, только для чтения.

    Словари — представления состояния движка, а не копии: снимок действителен только во время `decide`.
    """

    t: int
    tasks: Mapping[str, Task]
    ready: tuple[str, ...]  # задачи READY, отсортированные по id
    status: Mapping[str, TaskStatus]
    running: Mapping[str, RunInfo]
    rem: Mapping[str, int]  # остаток работы по задаче
    free: Mapping[str, Resources]  # свободные ресурсы узла (у недоступного узла пусто)
    capacity: Mapping[str, Resources]  # ёмкость узла
    up: Mapping[str, bool]
    eligible: Mapping[str, tuple[str, ...]]  # допустимые узлы задачи
    analysis: Analysis  # ранние сроки и границы — на момент последнего пересчёта
    ready_since: Mapping[str, int]
    preempt_count: Mapping[str, int]
    speed: Mapping[str, float] = field(default_factory=dict)  # скорость узла; нет — эталонная, 1


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
