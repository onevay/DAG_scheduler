"""Проверки графа зависимостей: ссылки, циклы, дубликаты рёбер."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from expsched.domain.graph import TaskGraph
from expsched.domain.models import Problem, Task
from expsched.validation.diagnostics import Code, Diagnostic


def validate_graph(tasks: Mapping[str, Task]) -> list[Diagnostic]:
    """E010 неизвестная зависимость, E011 самоссылка, E012 цикл (по одному на компоненту), W060 дубликат."""
    return [
        *_reference_errors(tasks),
        *_cycle_errors(TaskGraph(tasks)),
        *_duplicate_edges(tasks),
    ]


def dedupe_dependencies(problem: Problem) -> Problem:
    """Убирает повторы в `deps`, сохраняя порядок; если повторов нет, возвращает тот же объект."""
    fixed = {k: _deduped(t) for k, t in problem.tasks.items()}
    if all(fixed[k] is problem.tasks[k] for k in fixed):
        return problem
    return replace(problem, tasks=fixed)


def _deduped(task: Task) -> Task:
    unique = tuple(dict.fromkeys(task.deps))
    return task if unique == task.deps else replace(task, deps=unique)


def _reference_errors(tasks: Mapping[str, Task]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for key in sorted(tasks):
        for dep in sorted(set(tasks[key].deps)):
            if dep == key:
                out.append(Diagnostic(Code.E011, {"task": key}, "Удалите зависимость задачи от самой себя"))
            elif dep not in tasks:
                out.append(
                    Diagnostic(
                        Code.E010, {"task": key, "dep": dep}, "Проверьте id зависимости: такой задачи нет"
                    )
                )
    return out


def _cycle_errors(graph: TaskGraph) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for component in graph.cyclic_components():
        cycle = graph.find_cycle(component)
        before, after = _edge_to_break(graph, cycle)
        path = " → ".join(cycle)
        out.append(
            Diagnostic(
                Code.E012,
                {"cycle": cycle, "component_size": len(component)},
                f"Цикл: {path}. Рекомендация (эвристика, не оптимум): уберите {before} из зависимостей "
                f"{after}: у неё останется больше всего других зависимостей",
            )
        )
    return out


def _edge_to_break(graph: TaskGraph, cycle: list[str]) -> tuple[str, str]:
    """Ребро цикла, входящее в задачу с наибольшим числом зависимостей: после разрыва ей останется что ждать.

    Это эвристика: минимальный набор рёбер, разрывающий все циклы, искать NP-трудно.
    """
    return min(zip(cycle, cycle[1:], strict=False), key=lambda e: (-len(graph.preds(e[1])), e))


def _duplicate_edges(tasks: Mapping[str, Task]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for key in sorted(tasks):
        deps = tasks[key].deps
        if len(set(deps)) == len(deps):  # быстрый путь: повторов нет
            continue
        for dep, count in sorted(Counter(deps).items()):
            if count > 1:
                out.append(
                    Diagnostic(Code.W060, {"task": key, "dep": dep, "count": count}, "Дубликат ребра удалён")
                )
    return out
