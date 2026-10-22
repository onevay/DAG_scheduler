"""FIFO: базовая политика для сравнения. Задачи стартуют в порядке готовности."""

from __future__ import annotations

from expsched.domain.snapshot import Decision, Snapshot
from expsched.policies.base import best_fit


class FifoPolicy:
    """Очередь по моменту готовности, затем по release и id; узел выбирается best-fit.

    Строгий FIFO останавливается на первой задаче, которой сейчас нет места: следующие ждут её.
    С `skip=True` такая задача пропускается, и очередь продолжается.
    """

    def __init__(self, skip: bool = False) -> None:
        self.skip = skip
        self.name = "fifo_skip" if skip else "fifo"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        free = dict(snapshot.free)
        code = "FIFO_FIRST_FIT" if self.skip else "FIFO_HEAD"
        queue = sorted(snapshot.ready, key=lambda k: (snapshot.ready_since[k], snapshot.tasks[k].release, k))
        room = any(free.values())
        out: list[Decision] = []
        for key in queue:
            need = snapshot.tasks[key].resources
            # Когда свободных ресурсов не осталось, подбирать узел бессмысленно (важно при длинной очереди).
            worker = None if need and not room else best_fit(snapshot, key, free)
            if worker is None:
                if self.skip:
                    continue
                break
            free[worker] = free[worker] - need
            room = any(free.values())
            out.append(Decision("START", key, worker, {"code": code}))
        return out
