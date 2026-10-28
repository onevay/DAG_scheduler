"""Политика для неизвестных длительностей: Tiresias 2D-LAS (Gu и др., NSDI 2019).

Длительности не используются вовсе. Приоритет — по полученному обслуживанию: ресурс × сделанная работа
(GPU, если они есть у задачи, иначе ядра). Меньше получила — раньше идёт: короткие задачи проходят быстро, а
длинные, не зная своей длины, всё равно продвигаются. Обслуживание дискретно (2D: ресурс и время) — очереди
по порогам; внутри очереди — по моменту готовности. Задача, прождавшая `aging` единиц, поднимается в первую
очередь: так не голодают задачи, получившие много обслуживания.
"""

from __future__ import annotations

from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Snapshot
from expsched.policies.base import best_fit

THRESHOLDS = (10, 100)  # границы очередей по обслуживанию: ресурс × единица работы


def units(resources: Resources) -> int:
    return max(1, resources.get("gpu") or resources.get("cpu") or 1)


class LasPolicy:
    name = "las"

    def __init__(self, aging: int | None = None, thresholds: tuple[int, ...] = THRESHOLDS) -> None:
        self.aging = aging
        self.thresholds = thresholds

    def queue(self, snapshot: Snapshot, task_id: str) -> tuple[int, int]:
        """Очередь задачи и её обслуживание; прождавшая `aging` — в первой очереди."""
        task = snapshot.tasks[task_id]
        service = units(task.resources) * (task.duration - snapshot.rem[task_id])  # сохранённая работа
        level = sum(service >= t for t in self.thresholds)
        if self.aging and snapshot.t - snapshot.ready_since[task_id] >= self.aging:
            level = 0
        return level, service

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        queues = {k: self.queue(snapshot, k) for k in snapshot.ready}
        order = sorted(queues, key=lambda k: (queues[k][0], snapshot.ready_since[k], k))
        free = dict(snapshot.free)
        out: list[Decision] = []
        for task_id in order:
            worker = best_fit(snapshot, task_id, free)
            if worker is None:
                continue
            free[worker] = free[worker] - snapshot.tasks[task_id].resources
            level, service = queues[task_id]
            out.append(
                Decision("START", task_id, worker, {"code": "LAS", "queue": level, "service": service})
            )
        return out
