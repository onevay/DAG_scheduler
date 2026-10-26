"""EASY backfilling поверх приоритетной очереди: крупная задача получает резерв, мелкие заполняют дыры."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Snapshot
from expsched.policies.base import best_fit
from expsched.policies.priority import PriorityPolicy, queue_reason


@dataclass
class Reservation:
    task: str
    worker: str
    t: int  # t_res: задача стартует на узле не позже этого момента
    extra: Resources  # что останется свободным на узле в t_res после старта задачи


class BackfillPolicy(PriorityPolicy):
    """Очередь — как у priority. Первая задача, которой сейчас нет места (головная), получает резерв: самый
    ранний момент и узел, где для неё освободится место по прогнозным окончаниям. Остальные задачи стартуют,
    только если не отодвигают резерв: идут на другом узле, заканчиваются к t_res или помещаются в `extra`.

    Резерв есть только у одной задачи. Без отказов и без новых задач выше в очереди головная задача
    стартует не позже t_res.
    """

    name = "priority_backfill"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        keys = {k: self.key(snapshot, k) for k in snapshot.ready}
        free = dict(snapshot.free)
        room = any(free.values())
        started: list[tuple[str, str]] = []
        held: Reservation | None = None
        out: list[Decision] = []
        for task_id in sorted(keys, key=keys.__getitem__):
            need = snapshot.tasks[task_id].resources
            if need and not room and held is not None:
                break  # места нет ни для кого, резерв уже выдан
            if held is None:
                worker = best_fit(snapshot, task_id, free)
                if worker is None:
                    held = reservation(snapshot, task_id, free, started)
                    if held is not None:
                        out.append(Decision("RESERVE", task_id, held.worker, {"t_res": held.t}))
                    continue  # у задачи без живых узлов резерва нет: головной станет следующая
                reason = queue_reason(keys[task_id])
            else:
                worker = best_fit(snapshot, task_id, _not_delaying(snapshot, task_id, free, held))
                if worker is None:
                    continue
                if worker == held.worker and snapshot.t + snapshot.rem[task_id] > held.t:
                    held.extra = held.extra - need
                reason = {"code": "BACKFILL", "head": held.task, "t_res": held.t}
            free[worker] = free[worker] - need
            room = any(free.values())
            started.append((task_id, worker))
            out.append(Decision("START", task_id, worker, reason))
        return out


def release_profile(
    snapshot: Snapshot, worker: str, started: Sequence[tuple[str, str]]
) -> list[tuple[int, Resources]]:
    """Когда и сколько освободится на узле: выполняющиеся задачи и стартовавшие в эту эпоху, по времени."""
    # Попытка на агенте может идти дольше прогноза: раньше «сейчас» она уже не освободит узел
    profile = [
        (max(run.end_pred, snapshot.t), snapshot.tasks[k].resources)
        for k, run in snapshot.running.items()
        if run.worker == worker
    ]
    profile += [
        (snapshot.t + snapshot.rem[k], snapshot.tasks[k].resources) for k, w in started if w == worker
    ]
    return sorted(profile, key=lambda p: p[0])


def reservation(
    snapshot: Snapshot, task_id: str, free: Mapping[str, Resources], started: Sequence[tuple[str, str]]
) -> Reservation | None:
    """Самый ранний момент, когда задача поместится на допустимый работающий узел; при равенстве — узел
    с меньшим id. None — допустимых работающих узлов нет или задача не поместится, даже когда они опустеют.
    """
    need = snapshot.tasks[task_id].resources
    best: Reservation | None = None
    for worker in snapshot.eligible[task_id]:
        if not snapshot.up[worker]:
            continue
        available = free[worker]
        profile = release_profile(snapshot, worker, started)
        for i, (t, released) in enumerate(profile):
            available = available + released
            if i + 1 < len(profile) and profile[i + 1][0] == t:
                continue  # всё, что освобождается в один момент, учитывается вместе
            if need.fits(available):
                if best is None or t < best.t:
                    best = Reservation(task_id, worker, t, available - need)
                break
    return best


def _not_delaying(
    snapshot: Snapshot, task_id: str, free: Mapping[str, Resources], held: Reservation
) -> dict[str, Resources]:
    """Свободные ресурсы, где узел резерва обнулён, если задача на нём отодвинула бы резерв."""
    need = snapshot.tasks[task_id].resources
    ends_in_time = snapshot.t + snapshot.rem[task_id] <= held.t
    allowed = dict(free)
    if not (ends_in_time or need.fits(held.extra)):
        allowed[held.worker] = Resources()
    return allowed
