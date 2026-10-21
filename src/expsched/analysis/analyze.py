"""Статический анализ: структура Analysis для политик и сертификатов дедлайнов."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from expsched.analysis.bounds import energy_bounds, path_bound, threshold_bound
from expsched.analysis.ranks import backward_pass, forward_pass
from expsched.domain.graph import TaskGraph
from expsched.domain.models import Problem


@dataclass(frozen=True)
class Analysis:
    order: tuple[str, ...]  # топологический порядок задач, вошедших в анализ
    est: dict[str, int]  # самый ранний старт
    eft: dict[str, int]  # самое раннее окончание
    lft: dict[str, int | None]  # самое позднее окончание; None — дедлайнов ниже по графу нет
    lst: dict[str, int | None]  # самый поздний старт; запас задачи = lst - est
    rank: dict[str, int]  # длина критического пути до стока
    lb_path: int
    lb_energy: dict[str, int]
    lb_thr: int
    lb: int  # нижняя граница makespan: максимум из трёх


def analyze(
    problem: Problem,
    remaining: Mapping[str, int] | None = None,
    now: int = 0,
    done: Collection[str] = (),
) -> Analysis:
    """Анализ графа над остатками работы. Завершённые задачи (`done`) исключаются, ссылки на них игнорируются.

    Предполагает корректный граф: циклы должна отсечь валидация (validate_graph + quarantine).
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
