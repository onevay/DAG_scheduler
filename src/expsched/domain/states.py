"""Статусы задач, порядок обработки событий и классы приоритета."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INFEASIBLE = "INFEASIBLE"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


# Из финальных статусов переходов нет.
FINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INFEASIBLE,
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
    }
)


class EvClass(IntEnum):
    """Порядок обработки событий одного момента: меньше — раньше.

    Окончания идут первыми, поэтому задача, завершившаяся ровно в момент отказа узла, успешна.
    """

    FINISH = 0
    WORKER_DOWN = 1
    WORKER_UP = 2
    QUEUE = 3  # подача задачи, наступление release_time
    DECIDE = 9  # решение политики — после всех событий момента


class PriorityClass(IntEnum):
    LOW = 0  # приоритет 0-1
    NORMAL = 1  # 2-4
    HIGH = 2  # 5-7
    CRITICAL = 3  # 8-10


MAX_PRIORITY = 10


def priority_class(priority: int) -> PriorityClass:
    if not 0 <= priority <= MAX_PRIORITY:
        raise ValueError(f"priority must be in 0..{MAX_PRIORITY}, got {priority}")
    if priority >= 8:
        return PriorityClass.CRITICAL
    if priority >= 5:
        return PriorityClass.HIGH
    if priority >= 2:
        return PriorityClass.NORMAL
    return PriorityClass.LOW
