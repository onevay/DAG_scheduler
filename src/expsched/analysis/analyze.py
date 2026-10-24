"""Статический анализ графа: сроки, ранги и нижние границы makespan."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from expsched.analysis.bounds import energy_bounds, path_bound, threshold_bound
from expsched.analysis.ranks import backward_pass, forward_pass
from expsched.domain.analysis import Analysis
from expsched.domain.graph import TaskGraph
from expsched.domain.models import Problem


def critical_path(problem: Problem, analysis: Analysis) -> list[str]:
    """Задачи критического пути от начала к концу; последняя кончается в lb_path.

    Путь идёт назад от задачи с самым поздним ранним окончанием по зависимостям, окончание которых определило
    её ранний старт; при равенстве берётся меньший id. Обрывается на задаче без таких зависимостей.
    """
    if not analysis.eft:
        return []
    current = min(analysis.eft, key=lambda k: (-analysis.eft[k], k))
    path = [current]
    while True:
        deps = sorted(
            d for d in set(problem.tasks[current].deps) if analysis.eft.get(d) == analysis.est[current]
        )
        if not deps:
            return path[::-1]
        current = deps[0]
        path.append(current)


def analyze(
    problem: Problem,
    remaining: Mapping[str, int] | None = None,
    now: int = 0,
    done: Collection[str] = (),
) -> Analysis:
    """Анализ в момент `now` по остаткам работы `remaining` (по умолчанию — полные длительности).

    Задачи из `done` и ссылки на них не учитываются. Граф должен быть ацикличным, иначе ValueError.
    """
    tasks = {k: t for k, t in problem.tasks.items() if k not in done}
    rest = {k: (remaining or {}).get(k, t.duration) for k, t in tasks.items()}
    graph = TaskGraph(tasks)
    order = graph.topological_order()
    if len(order) != len(tasks):
        raise ValueError("task graph has a cycle; run validation and quarantine first")

    est, eft = forward_pass(tasks, graph, order, rest, now)
    lft, lst, rank = backward_pass(tasks, graph, order, rest)
    workers = list(problem.workers.values())
    lb_path = path_bound(eft, now)
    lb_energy = energy_bounds(tasks, rest, workers, now)
    lb_thr = threshold_bound(tasks, rest, workers, now)
    return Analysis(
        order=tuple(order),
        est=est,
        eft=eft,
        lft=lft,
        lst=lst,
        rank=rank,
        lb_path=lb_path,
        lb_energy=lb_energy,
        lb_thr=lb_thr,
        lb=max(lb_path, lb_thr, *lb_energy.values()),
    )
