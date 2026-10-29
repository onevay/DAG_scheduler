"""Очередь событий симулятора.

События упорядочены по (время, класс, номер добавления): класс задаёт порядок внутри момента (см. EvClass),
номер добавления делает порядок полным и детерминированным.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Literal

from expsched.domain.models import Event, EvType
from expsched.domain.states import EvClass


@dataclass(frozen=True)
class Finish:
    """Запланированное окончание попытки. По номеру попытки распознаётся окончание уже прерванной попытки."""

    task: str
    attempt: int


@dataclass(frozen=True)
class Crash:
    """Попытка оборвалась не по решению движка: контейнер упал или пропал (FAILED), превысил память (OOM).
    Как отказ узла: работа до сохранённого остаётся, ретрай расходуется."""

    task: str
    attempt: int
    cause: Literal["FAILED", "OOM"]
    detail: str | None = None


@dataclass(frozen=True)
class Reestimate:
    """Новая оценка работы идущей попытки (от её начала) и разброс того, что осталось неизвестным."""

    task: str
    attempt: int
    work: int
    spread: float


@dataclass(frozen=True)
class Release:
    """Наступил release_time задачи: в этот момент она может стать готовой."""

    task: str


Payload = Finish | Crash | Reestimate | Release | Event

_EVENT_CLASS = {
    EvType.WORKER_DOWN: EvClass.WORKER_DOWN,
    EvType.WORKER_UP: EvClass.WORKER_UP,
    EvType.SUBMIT: EvClass.QUEUE,
}


def event_class(payload: Payload) -> EvClass:
    if isinstance(payload, Finish | Crash | Reestimate):
        return EvClass.FINISH
    if isinstance(payload, Release):
        return EvClass.QUEUE
    return _EVENT_CLASS[payload.type]


class EventQueue:
    def __init__(self) -> None:
        # Номер добавления уникален, поэтому до сравнения payload дело не доходит.
        self._heap: list[tuple[int, int, int, Payload]] = []
        self._seq = 0

    def push(self, time: int, payload: Payload) -> None:
        heapq.heappush(self._heap, (time, event_class(payload), self._seq, payload))
        self._seq += 1

    def peek(self) -> int | None:
        """Момент ближайшего события; None — очередь пуста."""
        return self._heap[0][0] if self._heap else None

    def pop_batch(self) -> tuple[int, list[Payload]]:
        """Извлекает все события ближайшего момента по порядку. На пустой очереди — IndexError."""
        time = self._heap[0][0]
        batch: list[Payload] = []
        while self._heap and self._heap[0][0] == time:
            batch.append(heapq.heappop(self._heap)[3])
        return time, batch

    def drop_external(self) -> None:
        """Убирает внешние события (отказы, возвраты, подачу задач): прогноз «если ничего не случится»."""
        self._heap = [entry for entry in self._heap if not isinstance(entry[3], Event)]
        heapq.heapify(self._heap)

    def __len__(self) -> int:
        return len(self._heap)
