"""Построение неизменяемого снимка состояния для политики."""

from __future__ import annotations

from dataclasses import replace

from expsched.analysis import analyze
from expsched.domain.snapshot import Snapshot
from expsched.domain.states import TaskStatus
from expsched.engine.state import SimState


def build_snapshot(state: SimState, now: int) -> Snapshot:
    """Снимок с копиями словарей: политика, изменив их, не испортит состояние движка.

    Анализ пересчитывается целиком над остатками работы, завершённые задачи исключаются (как в эталоне;
    инкрементальный пересчёт — задача промышленной версии).
    """
    done = {k for k, s in state.status.items() if s is TaskStatus.SUCCEEDED}
    analysis = analyze(replace(state.problem, tasks=state.tasks), remaining=state.rem, now=now, done=done)
    return Snapshot(
        t=now,
        tasks=dict(state.tasks),
        ready=tuple(sorted(k for k, s in state.status.items() if s is TaskStatus.READY)),
        running=dict(state.run),
        rem=dict(state.rem),
        free=dict(state.free),
        up=dict(state.up),
        eligible=dict(state.eligible),
        analysis=analysis,
        ready_since=dict(state.ready_since),
        preempt_count=dict(state.preempt_count),
    )
