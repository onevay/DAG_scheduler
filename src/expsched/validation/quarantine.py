"""Режим lenient: задачи с ошибками выводятся из плана, остальная лаборатория продолжает работать."""

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
    problem: Problem  # то, что можно планировать: ацикличный подграф, замкнутый по предшественникам
    excluded: dict[str, TaskStatus]  # INFEASIBLE — виновники, BLOCKED — их потомки


def quarantine(problem: Problem, diagnostics: Iterable[Diagnostic]) -> Quarantine:
    """Виновники (E010, E011, E012, E020, E021) получают INFEASIBLE, все их потомки — BLOCKED.

    Оставшийся подграф ацикличен и содержит вместе с задачей всех её предшественников: любой цикл
    целиком лежит в удалённой компоненте сильной связности, а потомок удалённой задачи удалён сам.
    """
    graph = TaskGraph(problem.tasks)
    diagnostics = list(diagnostics)
    guilty = {d.detail["task"] for d in diagnostics if d.code in _TASK_CODES}
    if any(d.code is Code.E012 for d in diagnostics):
        # Диагностика называет один цикл компоненты, а выводить из плана нужно всю компоненту.
        guilty |= {key for component in graph.cyclic_components() for key in component}

    blocked = graph.descendants(guilty) - guilty
    excluded = {key: TaskStatus.BLOCKED for key in sorted(blocked)}
    excluded.update({key: TaskStatus.INFEASIBLE for key in sorted(guilty)})
    runnable = {k: t for k, t in problem.tasks.items() if k not in excluded}
    return Quarantine(replace(problem, tasks=runnable), excluded)
