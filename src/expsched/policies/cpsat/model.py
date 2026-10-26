"""Расписание остатка прогона решателем CP-SAT: на какой узел и когда стартует каждая задача.

Модель: у задачи один старт и по необязательному интервалу на каждый допустимый узел, выбран ровно один;
ёмкость узла — по каждому ресурсу отдельно (идущие попытки занимают её до прогнозного окончания);
задача стартует не раньше release и окончания зависимостей; дедлайны мягкие. Цель — как `metrics.score`:
lexicographic решается стадиями (каждая следующая — при лучшем значении предыдущих), weighted — одной.
Решатель детерминирован: один поток, `seed` и бюджет детерминированного времени вместо секунд.

Найденный план сдвигается влево: старт каждой задачи совпадает с release, окончанием зависимости или
окончанием задачи на узле — с моментом, когда в симуляции есть эпоха. Без сдвига неоптимальный план мог бы
назначить старт на момент, когда решений не принимают, и задача ушла бы со своего места.
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
    plan: PlanInput, objective: str = "lexicographic", time_limit: float = 5.0, seed: int = 0
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
    for key, task in sorted(plan.tasks.items()):
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
    starts = compact(plan, best)
    return Plan(starts, score(plan, starts, objective), optimal and len(key_values) == len(stages))


def compact(plan: PlanInput, starts: Mapping[str, tuple[str, int]]) -> dict[str, tuple[str, int]]:
    """Сдвиг влево: в порядке плановых стартов каждая задача на своём узле встаёт на самый ранний момент,
    где хватает ресурсов. Задачам с более ранним плановым стартом сдвиг только освобождает место, поэтому
    плановый старт всегда остаётся допустимым и сдвиг не ухудшает ни окончаний, ни цели."""
    placed: dict[str, tuple[str, int]] = {}
    for key in sorted(starts, key=lambda k: (starts[k][1], k)):
        worker, planned = starts[key]
        task = plan.tasks[key]
        ready = max(
            [
                task.release,
                *(placed[d][1] + plan.tasks[d].duration for d in task.deps if d in placed),
                *(plan.busy[d].end for d in task.deps if d in plan.busy),
            ]
        )
        load = [
            (s, s + plan.tasks[k].duration, plan.tasks[k].resources)
            for k, (w, s) in placed.items()
            if w == worker
        ]
        load += [(plan.now, b.end, b.resources) for b in plan.busy.values() if b.worker == worker]
        candidates = sorted({ready, *(end for _, end, _ in load if end > ready)})
        start = next(
            (t for t in candidates if t <= planned and _room(plan.capacity[worker], load, task, t)), planned
        )
        placed[key] = (worker, start)
    return placed


def _room(capacity: Resources, load: list[tuple[int, int, Resources]], task: PlanTask, t: int) -> bool:
    """Хватает ли ресурсов на [t, t + duration): загрузка растёт только в стартах, их и проверяем."""
    for x in [t, *(s for s, _, _ in load if t < s < t + task.duration)]:
        used = task.resources
        for s, e, need in load:
            if s <= x < e:
                used = used + need
        if not used.fits(capacity):
            return False
    return True


def score(plan: PlanInput, starts: Mapping[str, tuple[str, int]], objective: str) -> list[int]:
    """Оценка плана по цели, как `metrics.score`; дедлайны идущих задач в неё не входят."""
    ends = {k: s + plan.tasks[k].duration for k, (_, s) in starts.items()}
    makespan = max([plan.now, *ends.values(), *(b.end for b in plan.busy.values())])
    tardy = {k: max(0, ends[k] - t.deadline) for k, t in plan.tasks.items() if t.deadline is not None}
    weighted = sum(2 ** plan.tasks[k].cls * v for k, v in tardy.items())
    if objective == "weighted":
        return [weighted + makespan]
    misses = [sum(1 for k, v in tardy.items() if v and plan.tasks[k].cls == c) for c in CLASSES]
    return [*misses, weighted, makespan]
