"""Приоритетная политика: важные и срочные задачи раньше, при равенстве — длинные цепочки."""

from __future__ import annotations

import math

from expsched.domain.snapshot import Decision, Snapshot
from expsched.domain.states import PriorityClass
from expsched.policies.base import best_fit

QueueKey = tuple[int, float, int, int, str]


class PriorityPolicy:
    """Очередь по ключу (−класс, запас до дедлайна, −ранг, release, id): меньше — раньше.

    Класс важнее срочности, срочность важнее длины оставшейся цепочки. Aging поднимает класс задачи на 1
    за каждые `aging` единиц ожидания, но не выше HIGH: CRITICAL назначает только человек. Задача, которой
    сейчас нет места, не останавливает очередь.
    """

    name = "priority"

    def __init__(self, aging: int | None = None) -> None:
        self.aging = aging

    def key(self, snapshot: Snapshot, task_id: str) -> QueueKey:
        task = snapshot.tasks[task_id]
        cls = int(task.cls)
        if self.aging and cls < PriorityClass.CRITICAL:
            waited = snapshot.t - snapshot.ready_since[task_id]
            cls = min(int(PriorityClass.HIGH), cls + waited // self.aging)
        latest_start = snapshot.analysis.lst[task_id]
        slack = math.inf if latest_start is None else latest_start - snapshot.t
        return (-cls, slack, -snapshot.analysis.rank[task_id], task.release, task_id)

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        keys = {k: self.key(snapshot, k) for k in snapshot.ready}
        free = dict(snapshot.free)
        room = any(free.values())
        out: list[Decision] = []
        for task_id in sorted(keys, key=keys.__getitem__):
            need = snapshot.tasks[task_id].resources
            if need and not room:
                continue  # всё занято; задачам без ресурсов место есть всегда, поэтому не break
            worker = best_fit(snapshot, task_id, free)
            if worker is None:
                continue
            free[worker] = free[worker] - need
            room = any(free.values())
            out.append(Decision("START", task_id, worker, _reason(keys[task_id])))
        return out


def _reason(key: QueueKey) -> dict[str, object]:
    cls, slack, rank, _, _ = key
    # Бесконечный запас пишется как None: в журнале только конечные числа.
    return {
        "code": "TOP_OF_QUEUE",
        "class": -cls,
        "slack": None if slack == math.inf else slack,
        "rank": -rank,
    }
