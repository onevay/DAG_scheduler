"""Построение снимка состояния для политики."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from expsched.analysis import analyze
from expsched.domain.snapshot import Snapshot
from expsched.domain.states import TaskStatus
from expsched.engine.state import SimState


def build_snapshot(state: SimState, now: int) -> Snapshot:
    """Снимок из представлений только для чтения: без копий, политика не может изменить состояние.

    Анализ берётся из кеша состояния и пересчитывается, только если кеш сброшен.
    """
    if state.analysis is None:
        done = {k for k, s in state.status.items() if s is TaskStatus.SUCCEEDED}
        problem = replace(state.problem, tasks=state.tasks)
        state.analysis = analyze(problem, remaining=state.rem, now=now, done=done)
    return Snapshot(
        t=now,
        tasks=MappingProxyType(state.tasks),
        ready=tuple(sorted(state.ready)),
        status=MappingProxyType(state.status),
        running=MappingProxyType(state.run),
        rem=MappingProxyType(state.rem),
        free=MappingProxyType(state.free),
        capacity=MappingProxyType({k: w.capacity for k, w in state.problem.workers.items()}),
        up=MappingProxyType(state.up),
        eligible=MappingProxyType(state.eligible),
        analysis=state.analysis,
        ready_since=MappingProxyType(state.ready_since),
        preempt_count=MappingProxyType(state.preempt_count),
    )
