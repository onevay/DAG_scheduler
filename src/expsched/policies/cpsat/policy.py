"""Политика cpsat: план остатка прогона от решателя CP-SAT и запуск задач по нему."""

from __future__ import annotations

from expsched.domain.snapshot import Decision, Snapshot
from expsched.domain.states import TaskStatus
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.cpsat.model import Busy, Plan, PlanInput, PlanTask, solve

# Больше незавершённых задач — решения принимает priority_backfill: на ~120 задачах решение с бюджетом 5
# занимает пару секунд, а на каждом внешнем событии план строится заново, и прогон ограничен таймаутом.
MAX_TASKS = 150

World = tuple[frozenset[str], frozenset[str]]  # задачи и работающие узлы, для которых построен план


class CpSatPolicy:
    """План строится на первой эпохе и заново, когда мир изменился: подана задача, упал или вернулся узел,
    задача, которой пора стартовать по плану, стартовать не может (её прервали, она не помещается).
    Будущие события входа политика не видит — только текущее состояние.

    Между перепланированиями: готовая задача, чей плановый старт наступил, стартует на плановом узле
    (причина PLAN), остальные готовые получают резерв на плановый момент. Если задач больше MAX_TASKS или
    решатель не нашёл план за бюджет, эпоху решает priority_backfill — у его стартов его же причины.
    Состояние политики — только кеш плана: при одинаковой последовательности снимков решения одинаковы.
    """

    name = "cpsat"

    def __init__(self, objective: str = "lexicographic", time_limit: float = 5.0, seed: int = 0) -> None:
        self.objective = objective
        self.time_limit = time_limit
        self.seed = seed
        self.fallback = BackfillPolicy()
        self._plan: Plan | None = None
        self._world: World | None = None

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        world: World = (frozenset(snapshot.tasks), frozenset(w for w, up in snapshot.up.items() if up))
        if self._plan is None or world != self._world or self._stale(snapshot, self._plan):
            self._world = world
            planned = plan_input(snapshot)
            self._plan = (
                solve(planned, self.objective, self.time_limit, self.seed)
                if len(planned.tasks) <= MAX_TASKS
                else None
            )
        if self._plan is None:
            return self.fallback.decide(snapshot)
        free = dict(snapshot.free)
        out: list[Decision] = []
        queue = sorted((self._plan.starts[k][1], k) for k in snapshot.ready if k in self._plan.starts)
        for _, key in queue:
            worker, start = self._plan.starts[key]
            need = snapshot.tasks[key].resources
            if start <= snapshot.t and need.fits(free[worker]):
                free[worker] = free[worker] - need
                out.append(Decision("START", key, worker, {"code": "PLAN", "planned": start}))
            elif start > snapshot.t:
                out.append(Decision("RESERVE", key, worker, {"code": "PLAN", "t_res": start}))
        return out

    @staticmethod
    def _stale(snapshot: Snapshot, plan: Plan) -> bool:
        """Готовая задача вне плана, которую есть где запустить (например, её вытеснили), или застрявшая: её
        плановый старт наступил, а на плановом узле места нет. Задача без работающего допустимого узла ждёт
        смены мира — она и так вызовет перепланирование."""
        for key in snapshot.ready:
            if key not in plan.starts:
                if any(snapshot.up.get(w, False) for w in snapshot.eligible[key]):
                    return True
                continue
            worker, start = plan.starts[key]
            room = snapshot.up.get(worker, False) and snapshot.tasks[key].resources.fits(
                snapshot.free[worker]
            )
            if start <= snapshot.t and not room:
                return True
        return False


def plan_input(snapshot: Snapshot) -> PlanInput:
    """Что планировать: незавершённые задачи, которые могут выполниться, — с остатком работы, работающими
    допустимыми узлами и зависимостями, которые сами выполнятся (идут или тоже планируются)."""
    up = {w for w, ok in snapshot.up.items() if ok}
    busy = {
        # попытка на агенте, идущая дольше прогноза, закончится не раньше «сейчас»
        k: Busy(run.worker, max(run.end_pred, snapshot.t), snapshot.tasks[k].resources)
        for k, run in snapshot.running.items()
    }
    waiting = {
        k
        for k, s in snapshot.status.items()
        if s in (TaskStatus.PENDING, TaskStatus.READY) and any(w in up for w in snapshot.eligible.get(k, ()))
    }
    # Задача, чья зависимость не выполнится (упала, заблокирована, ждёт узла, неизвестна), не планируется

    def will_finish(dep: str) -> bool:
        return snapshot.status.get(dep) is TaskStatus.SUCCEEDED or dep in waiting or dep in busy

    changed = True
    while changed:
        changed = False
        for k in sorted(waiting):
            if not all(will_finish(d) for d in snapshot.tasks[k].deps):
                waiting.discard(k)
                changed = True
    tasks = {
        k: PlanTask(
            duration=snapshot.rem[k],
            resources=snapshot.tasks[k].resources,
            workers=tuple(w for w in snapshot.eligible[k] if w in up),
            release=max(snapshot.t, snapshot.tasks[k].release),
            deps=tuple(d for d in snapshot.tasks[k].deps if d in waiting or d in busy),
            deadline=snapshot.tasks[k].deadline,
            cls=int(snapshot.tasks[k].cls),
        )
        for k in sorted(waiting)
    }
    capacity = {w: snapshot.capacity[w] for w in sorted(up)}
    return PlanInput(snapshot.t, tasks, busy, capacity)
