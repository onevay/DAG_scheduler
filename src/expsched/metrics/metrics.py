"""Метрики качества расписания, посчитанные по журналу прогона."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from expsched.domain.models import Problem
from expsched.domain.states import PriorityClass
from expsched.verify.attempts import attempts_from_log, down_intervals

Objective = Literal["lexicographic", "weighted"]


def metrics(
    problem: Problem,
    log: Sequence[Mapping[str, Any]],
    lower_bound: int | None,
    objective: Objective = "lexicographic",
) -> dict[str, Any]:
    """Сводка прогона: время, дедлайны, загрузка, потери, ожидание и оценка по цели (`score`).

    Дедлайн задачи, которая так и не завершилась, считается пропущенным. Загрузка и потери — по каждому
    ресурсу кластера: загрузка — полезная работа завершённых задач, делённая на ёмкость работающих узлов за
    время прогона (None, если ёмкости нет); потери — то, что прерванные попытки сделали после последнего
    чекпоинта, в единицах ресурса × время.
    """
    tasks = problem.all_tasks()
    attempts = attempts_from_log(log)
    finish = {a.task: a.end for a in attempts if a.outcome == "DONE" and a.end is not None}
    ready: dict[str, int] = {}  # первый момент готовности
    for r in log:
        if r["type"] == "READY":
            ready.setdefault(r["task"], r["t"])
    makespan = max(finish.values(), default=0)

    misses = {cls.name.lower(): 0 for cls in reversed(PriorityClass)}
    weighted_tardiness = 0
    for key, task in tasks.items():
        if task.deadline is None:
            continue
        end = finish.get(key)
        if end is None or end > task.deadline:
            misses[task.cls.name.lower()] += 1
        if end is not None:
            weighted_tardiness += 2 ** int(task.cls) * max(0, end - task.deadline)

    outages = down_intervals(log)
    alive = {w.id: makespan - _downtime(outages.get(w.id, []), makespan) for w in problem.workers.values()}
    lost_time = [
        (a.end - a.start - a.saved, tasks[a.task].resources)
        for a in attempts
        if a.outcome in ("INT", "PRE") and a.end is not None and a.task in tasks
    ]
    utilization: dict[str, float | None] = {}
    lost: dict[str, int] = {}
    for resource in sorted({r for w in problem.workers.values() for r in w.capacity}):
        useful = sum(tasks[k].duration * tasks[k].resources.get(resource) for k in finish)
        capacity = sum(w.capacity.get(resource) * alive[w.id] for w in problem.workers.values())
        utilization[resource] = round(useful / capacity, 4) if capacity else None
        lost[resource] = sum(time * need.get(resource) for time, need in lost_time)
    waits = [finish[k] - ready[k] - tasks[k].duration for k in finish if k in ready]
    return {
        "makespan": makespan,
        "lower_bound": lower_bound,
        "gap_to_lb": round((makespan - lower_bound) / lower_bound, 4) if lower_bound else None,
        "deadline_misses": misses,
        "weighted_tardiness": weighted_tardiness,
        "utilization": utilization,
        "lost_work": lost,
        "mean_wait": round(sum(waits) / len(waits), 3) if waits else 0,
        "score": score(misses, weighted_tardiness, makespan, objective),
    }


def score(
    misses: Mapping[str, int], weighted_tardiness: int, makespan: int, objective: Objective
) -> dict[str, Any]:
    """Оценка прогона по цели: `key` сравнивается поэлементно, меньше — лучше.

    lexicographic — пропуски дедлайнов по классам от critical до low, затем взвешенное опоздание, затем
    makespan: один пропуск старшего класса хуже любых потерь младших. weighted — одно число: взвешенное
    опоздание плюс makespan, где час опоздания critical-задачи стоит 8 часов makespan.
    """
    if objective == "weighted":
        key = [weighted_tardiness + makespan]
    else:
        key = [*(misses[c.name.lower()] for c in reversed(PriorityClass)), weighted_tardiness, makespan]
    return {"objective": objective, "key": key}


def _downtime(outages: Sequence[tuple[int, int | None]], horizon: int) -> int:
    """Сколько времени из [0, horizon) узел был недоступен."""
    return sum(max(0, min(horizon, horizon if hi is None else hi) - lo) for lo, hi in outages if lo < horizon)
