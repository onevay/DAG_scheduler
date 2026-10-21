"""Очередь событий симулятора.

Ключ кучи — (time, класс, seq). Класс задаёт порядок внутри одного момента: сначала FINISH (освобождаем
ресурсы), затем отказы и возвраты узлов, затем изменения очереди; решение политики принимается после
батча. seq — порядковый номер добавления, поэтому порядок полный и детерминированный.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from expsched.domain.models import Event, EvType
from expsched.domain.states import EvClass


@dataclass(frozen=True)
class Finish:
    """Запланированное окончание попытки. Номер попытки нужен для ленивого удаления устаревших событий."""

    task: str
    attempt: int


@dataclass(frozen=True)
class Release:
    """Наступил release_time задачи: в этот момент нужна эпоха решения."""

    task: str


Payload = Finish | Release | Event

_EVENT_CLASS = {
    EvType.WORKER_DOWN: EvClass.WORKER_DOWN,
    EvType.WORKER_UP: EvClass.WORKER_UP,
    EvType.SUBMIT: EvClass.QUEUE,
}


def event_class(payload: Payload) -> EvClass:
    if isinstance(payload, Finish):
        return EvClass.FINISH
    if isinstance(payload, Release):
        return EvClass.QUEUE
    return _EVENT_CLASS[payload.type]


class EventQueue:
    def __init__(self) -> None:
        # seq уникален, поэтому сравнение доходит максимум до него и payload никогда не сравниваются
        self._heap: list[tuple[int, int, int, Payload]] = []
        self._seq = 0

    def push(self, time: int, payload: Payload) -> None:
        heapq.heappush(self._heap, (time, event_class(payload), self._seq, payload))
        self._seq += 1

    def pop_batch(self) -> tuple[int, list[Payload]]:
        """Все события ближайшего момента в порядке ключа. На пустой очереди — IndexError."""
        time = self._heap[0][0]
        batch: list[Payload] = []
        while self._heap and self._heap[0][0] == time:
            batch.append(heapq.heappop(self._heap)[3])
        return time, batch

    def __len__(self) -> int:
        return len(self._heap)
