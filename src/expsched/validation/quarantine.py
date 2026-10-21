"""Режим lenient: задачи с ошибками убираются из плана, остальные планируются как обычно."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from expsched.domain.graph import TaskGraph
from expsched.domain.models import Problem
from expsched.domain.states import TaskStatus
from expsched.validation.diagnostics import Code, Diagnostic

# Диагностики, после которых сама задача не может быть выполнена.
_TASK_CODES = frozenset({Code.E010, Code.E011, Code.E020, Code.E021})


@dataclass(frozen=True)
class Quarantine:
    problem: Problem  # задачи, которые можно планировать
    excluded: dict[str, TaskStatus]  # INFEASIBLE — задачи с ошибками, BLOCKED — их потомки


def quarantine(problem: Problem, diagnostics: Iterable[Diagnostic]) -> Quarantine:
    """Задачи с ошибками (E010, E011, E012, E020, E021) получают INFEASIBLE, все их потомки — BLOCKED.

    Оставшиеся задачи образуют ацикличный граф, в котором у каждой задачи есть все её предшественники.
    """
    graph = TaskGraph(problem.tasks)
    diagnostics = list(diagnostics)
    guilty = {d.detail["task"] for d in diagnostics if d.code in _TASK_CODES}
    if any(d.code is Code.E012 for d in diagnostics):
        # E012 называет один цикл, а убрать нужно всю компоненту сильной связности.
        guilty |= {key for component in graph.cyclic_components() for key in component}

    blocked = graph.descendants(guilty) - guilty
    excluded = {key: TaskStatus.BLOCKED for key in sorted(blocked)}
    excluded.update({key: TaskStatus.INFEASIBLE for key in sorted(guilty)})
    runnable = {k: t for k, t in problem.tasks.items() if k not in excluded}
    return Quarantine(replace(problem, tasks=runnable), excluded)
