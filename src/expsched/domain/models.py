"""Доменные сущности: узел, задача, внешнее событие, экземпляр задачи планирования.

Все объекты неизменяемы. Изменяемое состояние симуляции живёт в engine и наружу
передаётся снимком. Структурные ошибки входа (циклы, неизвестные зависимости,
самоссылки) сюда не относятся: их выявляет валидация графа и сообщает диагностиками.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from expsched.domain.resources import Resources
from expsched.domain.states import PriorityClass, priority_class

Labels = tuple[tuple[str, str], ...]  # отсортированные пары ключ-значение


class EvType(StrEnum):
    """Внешние события входа. Значения совпадают с полем `type` в problem.json."""

    WORKER_DOWN = "worker_down"
    WORKER_UP = "worker_up"
    SUBMIT = "submit_task"


@dataclass(frozen=True)
class Task:
    id: str
    duration: int
    resources: Resources = field(default_factory=Resources)
    kind: str = "other"
    priority: int = 1
    deadline: int | None = None
    release: int = 0
    deps: tuple[str, ...] = ()
    preemptible: bool = False
    ckpt: int | None = None  # интервал чекпоинта; None — чекпоинтов нет
    max_retries: int = 2
    constraints: Labels = ()

    def __post_init__(self) -> None:
        if self.duration < 1:  # E070: иначе нет завершимости симуляции
            raise ValueError(f"task {self.id!r}: duration must be >= 1, got {self.duration}")
        if self.release < 0:
            raise ValueError(f"task {self.id!r}: release must be >= 0, got {self.release}")
        if self.deadline is not None and self.deadline < 0:
            raise ValueError(f"task {self.id!r}: deadline must be >= 0, got {self.deadline}")
        if self.ckpt is not None and self.ckpt < 1:
            raise ValueError(f"task {self.id!r}: ckpt must be >= 1, got {self.ckpt}")
        if self.max_retries < 0:
            raise ValueError(f"task {self.id!r}: max_retries must be >= 0, got {self.max_retries}")
        priority_class(self.priority)  # проверка диапазона

    @property
    def cls(self) -> PriorityClass:
        return priority_class(self.priority)


@dataclass(frozen=True)
class Worker:
    id: str
    capacity: Resources
    labels: Labels = ()


@dataclass(frozen=True)
class Event:
    time: int
    type: EvType
    worker: str | None = None
    task: Task | None = None


@dataclass(frozen=True)
class Problem:
    tasks: dict[str, Task]
    workers: dict[str, Worker]
    events: list[Event] = field(default_factory=list)
    seed: int = 0

    def eligible(self, task: Task) -> tuple[str, ...]:
        """Допустимые узлы задачи: хватает ёмкости и все constraints есть среди меток узла."""
        return tuple(
            sorted(
                w.id
                for w in self.workers.values()
                if task.resources.fits(w.capacity) and set(task.constraints) <= set(w.labels)
            )
        )
