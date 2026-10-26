"""Расписание остатка прогона решателем CP-SAT: на какой узел и когда стартует каждая задача.

Модель: у задачи один старт и по необязательному интервалу на каждый допустимый узел, выбран ровно один;
ёмкость узла — по каждому ресурсу отдельно (идущие попытки занимают её до прогнозного окончания);
задача стартует не раньше release и окончания зависимостей; дедлайны мягкие. Цель — как `metrics.score`:
lexicographic решается стадиями (каждая следующая — при лучшем значении предыдущих), weighted — одной.
Решатель детерминирован: один поток, `seed` и бюджет детерминированного времени вместо секунд.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ortools.sat.python import cp_model

from expsched.domain.resources import Resources

CLASSES = (3, 2, 1, 0)  # critical, high, normal, low — порядок стадий lexicographic


@dataclass(frozen=True)
class PlanTask:
    """Задача, которую нужно спланировать: остаток работы и где и когда она может идти."""

    duration: int
    resources: Resources
    workers: tuple[str, ...]  # допустимые доступные узлы
    release: int  # раньше не стартует; не меньше текущего момента
    deps: tuple[str, ...] = ()  # незавершённые зависимости: планируемые или идущие задачи
    deadline: int | None = None
    cls: int = 1


@dataclass(frozen=True)
class Busy:
    """Идущая попытка: занимает ресурсы узла с текущего момента до прогнозного окончания."""

    worker: str
    end: int
    resources: Resources


@dataclass(frozen=True)
class PlanInput:
    now: int
    tasks: Mapping[str, PlanTask]
    busy: Mapping[str, Busy]
    capacity: Mapping[str, Resources]  # доступные узлы


@dataclass(frozen=True)
class Plan:
    starts: dict[str, tuple[str, int]]  # задача → (узел, старт)
    key: list[int]  # оценка плана по цели; дедлайны идущих задач в неё не входят
    optimal: bool  # каждая стадия доказана оптимальной


def solve(
    plan: PlanInput, objective: str = "lexicographic", time_limit: float = 10.0, seed: int = 0
) -> Plan | None:
    """План остатка прогона или None, если за бюджет решение не найдено (или его нет)."""
    model = cp_model.CpModel()
    busy_end = [b.end for b in plan.busy.values()]
    horizon = max([plan.now, *busy_end, *(t.release for t in plan.tasks.values())]) + sum(
        t.duration for t in plan.tasks.values()
    )
    start: dict[str, cp_model.IntVar] = {}
    end: dict[str, cp_model.IntVar] = {}
    picks: dict[str, list[tuple[str, cp_model.IntVar]]] = {}
    on_worker: dict[str, list[tuple[cp_model.IntervalVar, Resources]]] = {w: [] for w in plan.capacity}

    for key, b in sorted(plan.busy.items()):
        if b.end > plan.now and b.worker in on_worker:
            interval = model.new_fixed_size_interval_var(plan.now, b.end - plan.now, f"busy_{key}")
            on_worker[b.worker].append((interval, b.resources))
    for key, task in sorted(plan.tasks.items()):
        start[key] = model.new_int_var(task.release, horizon, f"start_{key}")
        end[key] = model.new_int_var(task.release + task.duration, horizon, f"end_{key}")
        model.add(end[key] == start[key] + task.duration)
        picks[key] = []
        for worker in sorted(task.workers):
            picked = model.new_bool_var(f"on_{key}_{worker}")
            interval = model.new_optional_fixed_size_interval_var(
                start[key], task.duration, picked, f"run_{key}_{worker}"
            )
            picks[key].append((worker, picked))
            on_worker[worker].append((interval, task.resources))
        model.add_exactly_one(p for _, p in picks[key])
        for dep in sorted(set(task.deps)):
            if dep in plan.tasks:
                model.add(start[key] >= end[dep])
            elif dep in plan.busy:
                model.add(start[key] >= plan.busy[dep].end)

    for worker, items in sorted(on_worker.items()):
        capacity = plan.capacity[worker]
        for resource in sorted({r for _, need in items for r in need}):
            used = [(interval, need.get(resource)) for interval, need in items if need.get(resource)]
            model.add_cumulative([i for i, _ in used], [n for _, n in used], capacity.get(resource))

    makespan = model.new_int_var(plan.now, horizon, "makespan")
    model.add_max_equality(makespan, [*end.values(), *busy_end, plan.now])
    misses: dict[int, list[cp_model.IntVar]] = {c: [] for c in CLASSES}
    tardy: list[cp_model.IntVar] = []
    weights: list[int] = []
    for key, task in sorted(plan.tasks.items()):
        if task.deadline is None:
            continue
        late = model.new_bool_var(f"late_{key}")
        model.add(end[key] > task.deadline).only_enforce_if(late)
        model.add(end[key] <= task.deadline).only_enforce_if(late.Not())
        tardiness = model.new_int_var(0, horizon, f"tardiness_{key}")
        model.add(tardiness >= end[key] - task.deadline)
        misses[task.cls].append(late)
        tardy.append(tardiness)
        weights.append(2**task.cls)
    tardiness_sum = cp_model.LinearExpr.weighted_sum(tardy, weights)
    if objective == "weighted":
        stages = [tardiness_sum + makespan]
    else:
        stages = [
            *(cp_model.LinearExpr.sum(misses[c]) for c in CLASSES),
            tardiness_sum,
            makespan,
        ]

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.max_deterministic_time = time_limit / len(stages)
    variables = [*start.values(), *(p for ps in picks.values() for _, p in ps)]
    best: dict[str, tuple[str, int]] | None = None
    key_values: list[int] = []
    optimal = True
    for stage in stages:
        model.minimize(stage)
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        optimal = optimal and status == cp_model.OPTIMAL
        value = int(solver.value(stage))
        key_values.append(value)
        best = {
            k: (next(w for w, p in picks[k] if solver.boolean_value(p)), solver.value(start[k]))
            for k in start
        }
        model.add(stage <= value)  # следующие стадии не ухудшают эту
        model.clear_hints()  # type: ignore[no-untyped-call]  # в заглушках ortools метод без аннотаций
        for var in variables:
            model.add_hint(var, solver.value(var))
    if best is None:
        return None
    return Plan(best, key_values, optimal and len(key_values) == len(stages))
