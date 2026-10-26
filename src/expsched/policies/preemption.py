"""Вытеснение ради дедлайна или по приоритету: надстройка над любой политикой."""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from typing import Literal

from expsched.domain.models import Task
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, Snapshot
from expsched.policies.backfill import reservation

MAX_CANDIDATES = 8  # жертвы на узле перебираются полностью: не больше 2^8 - 1 наборов

Trigger = Literal["deadline", "priority"]


class PreemptivePolicy:
    """Сначала решает базовая политика, затем готовые задачи, которые она не запустила, могут вытеснить самый
    дешёвый набор жертв на одном узле. С `trigger="deadline"` — задача с дедлайном, которая без вытеснения его
    пропустит, а сразу стартовав — успеет. С `trigger="priority"` — любая задача, которой сейчас нет места
    ни на одном узле; если место есть, а базовая политика её не запустила, это решение политики, а не нехватка
    места.

    Жертва: вытесняемая, класса строго ниже, вытеснялась меньше `max_preemptions` раз, проработала не меньше
    `min_runtime` и не предок задачи с дедлайном класса не ниже, чем у вытесняющей. Цена — работа, которая
    потеряется после последнего чекпоинта: время × доля доминирующего ресурса задачи на её узле. Задачи
    рассматриваются по убыванию класса, затем по дедлайну (задачи без дедлайна — после), затем по id.
    """

    def __init__(
        self, base: Policy, max_preemptions: int = 1, min_runtime: int = 0, trigger: Trigger = "deadline"
    ) -> None:
        self.base = base
        self.max_preemptions = max_preemptions
        self.min_runtime = min_runtime
        self.trigger = trigger
        self.name = f"{base.name}+preempt" if trigger == "deadline" else f"{base.name}+preempt_priority"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        decisions = self.base.decide(snapshot)
        if not any(snapshot.tasks[k].preemptible for k in snapshot.running):
            return decisions
        started = [(d.task, d.worker) for d in decisions if d.action == "START"]
        promised = {d.task: d.reason.get("t_res") for d in decisions if d.action == "RESERVE"}
        free = dict(snapshot.free)
        for key, worker in started:
            free[worker] = free[worker] - snapshot.tasks[key].resources

        preempts: list[Decision] = []
        rescued: list[Decision] = []
        view = snapshot
        done = {key for key, _ in started}
        urgent = sorted(
            (k for k in snapshot.ready if k not in done and self._may_ask(snapshot.tasks[k])),
            key=lambda k: (
                -snapshot.tasks[k].cls,
                snapshot.tasks[k].deadline is None,
                snapshot.tasks[k].deadline or 0,
                k,
            ),
        )
        for key in urgent:
            task = snapshot.tasks[key]
            if self.trigger == "priority":
                if _fits_somewhere(view, key, free):
                    continue
            elif not self._misses_deadline(view, key, free, started, promised.get(key)):
                continue
            plan = self.victims(view, key, free)
            if plan is None:
                continue
            worker, victims = plan
            for victim in victims:
                preempts.append(Decision("PREEMPT", victim, worker, {"code": "PREEMPTED_BY", "by": key}))
                free[worker] = free[worker] + view.tasks[victim].resources
            free[worker] = free[worker] - task.resources
            started.append((key, worker))
            rescued.append(Decision("START", key, worker, {"code": "PREEMPTION_START", "victims": victims}))
            # жертвы больше не выполняются: их не вытесняют повторно и не ждут их окончания
            view = replace(view, running={k: r for k, r in view.running.items() if k not in victims})
        if not rescued:
            return decisions
        moved = {d.task for d in rescued}
        kept = [d for d in decisions if not (d.action == "RESERVE" and d.task in moved)]
        return preempts + kept + rescued

    def _may_ask(self, task: Task) -> bool:
        return self.trigger == "priority" or task.deadline is not None

    @staticmethod
    def _misses_deadline(
        snapshot: Snapshot,
        key: str,
        free: Mapping[str, Resources],
        started: list[tuple[str, str]],
        promised: object = None,
    ) -> bool:
        """Задача успеет, только если стартует сейчас: по окончаниям идущих задач она опоздает. `promised` —
        резерв базовой политики (её план): если он успевает к дедлайну, задачу не спасают."""
        deadline = snapshot.tasks[key].deadline
        if deadline is None or snapshot.t + snapshot.rem[key] > deadline:
            return False  # не успеет даже сейчас: вытеснение ничего не даст
        if isinstance(promised, int) and promised + snapshot.rem[key] <= deadline:
            return False
        on_time = reservation(snapshot, key, free, started)
        return on_time is None or on_time.t + snapshot.rem[key] > deadline

    def victims(
        self, snapshot: Snapshot, key: str, free: Mapping[str, Resources]
    ) -> tuple[str, list[str]] | None:
        """Самый дешёвый набор жертв, после вытеснения которых задача помещается на узел; при равенстве цены —
        меньше жертв, затем меньший id узла. None — такого набора нет.
        """
        task = snapshot.tasks[key]
        best: tuple[tuple[Fraction, int, str, tuple[str, ...]], str, list[str]] | None = None
        successors: dict[str, list[str]] | None = None
        for worker in snapshot.eligible[key]:
            if not snapshot.up[worker]:
                continue
            candidates = []
            for victim, run in snapshot.running.items():
                if run.worker != worker or not self._may_preempt(snapshot, victim, task):
                    continue
                if successors is None:
                    successors = _successors(snapshot.tasks)
                if not _feeds(snapshot.tasks, successors, victim, task):
                    candidates.append(victim)
            candidates = sorted(candidates, key=lambda v: (_lost_work(snapshot, v), v))[:MAX_CANDIDATES]
            for n in range(1, len(candidates) + 1):
                for subset in itertools.combinations(candidates, n):
                    room = free[worker]
                    for victim in subset:
                        room = room + snapshot.tasks[victim].resources
                    if not task.resources.fits(room):
                        continue
                    rank = (sum((_lost_work(snapshot, v) for v in subset), Fraction(0)), n, worker, subset)
                    if best is None or rank < best[0]:
                        best = (rank, worker, sorted(subset))
        return None if best is None else (best[1], best[2])

    def _may_preempt(self, snapshot: Snapshot, victim: str, task: Task) -> bool:
        other = snapshot.tasks[victim]
        return (
            other.preemptible
            and other.cls < task.cls
            and snapshot.preempt_count.get(victim, 0) < self.max_preemptions
            and snapshot.t - snapshot.running[victim].start >= self.min_runtime
        )


def _fits_somewhere(snapshot: Snapshot, key: str, free: Mapping[str, Resources]) -> bool:
    need = snapshot.tasks[key].resources
    return any(snapshot.up[w] and need.fits(free[w]) for w in snapshot.eligible[key])


def _lost_work(snapshot: Snapshot, key: str) -> Fraction:
    """Сколько работы пропадёт, если прервать задачу сейчас: время после последнего чекпоинта × доля
    доминирующего ресурса задачи на её узле (задача на 4 GPU из 8 занимает половину узла)."""
    task = snapshot.tasks[key]
    elapsed = snapshot.t - snapshot.running[key].start
    saved = task.ckpt * (elapsed // task.ckpt) if task.ckpt else 0
    capacity = snapshot.capacity[snapshot.running[key].worker]
    share = max(
        (Fraction(n, capacity.get(r)) for r, n in task.resources.items() if capacity.get(r)), default=0
    )
    return (elapsed - saved) * Fraction(share)


def _successors(tasks: Mapping[str, Task]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key in sorted(tasks):
        for dep in set(tasks[key].deps):
            out.setdefault(dep, []).append(key)
    return out


def _feeds(tasks: Mapping[str, Task], successors: Mapping[str, list[str]], victim: str, task: Task) -> bool:
    """Жертва — предок задачи с дедлайном класса не ниже: её вытеснение отодвинуло бы равную по важности."""
    stack, seen = list(successors.get(victim, [])), set()
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        if tasks[key].deadline is not None and tasks[key].cls >= task.cls:
            return True
        stack.extend(successors.get(key, []))
    return False
