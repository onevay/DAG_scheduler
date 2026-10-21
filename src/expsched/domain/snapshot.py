"""Контракт между движком и политикой: что политика видит и что она может решить.

Политика получает неизменяемый Snapshot и возвращает решения; движок применяет их с проверкой (guard).
Политика не импортирует движок (контракт import-linter), поэтому весь этот словарь живёт в domain.
Изменение любого типа здесь — contract-change.
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
    """Состояние на момент решения. Политика его не изменяет и не видит движок."""

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
    reason: dict[str, Any] = field(default_factory=dict)  # попадает в журнал; обязательное поле `code`


class Policy(Protocol):
    """Чистая функция снимка: без побочных эффектов, детерминированная."""

    name: str

    def decide(self, snapshot: Snapshot) -> list[Decision]: ...
