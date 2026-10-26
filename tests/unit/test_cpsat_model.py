"""Модель CP-SAT: допустимость плана, цели и оптимальность против полного перебора на малых входах."""

import itertools
import random

import pytest

pytest.importorskip("ortools")

from expsched.domain.resources import Resources  # noqa: E402
from expsched.policies.cpsat.model import CLASSES, Busy, PlanInput, PlanTask, solve  # noqa: E402

R = Resources


def score(plan, starts, objective):
    """Оценка плана тем же правилом, что у модели: makespan с идущими задачами, дедлайны — у планируемых."""
    ends = {k: s + plan.tasks[k].duration for k, (_, s) in starts.items()}
    makespan = max([plan.now, *ends.values(), *(b.end for b in plan.busy.values())])
    tardy = {k: max(0, ends[k] - t.deadline) for k, t in plan.tasks.items() if t.deadline is not None}
    weighted = sum(2 ** plan.tasks[k].cls * v for k, v in tardy.items())
    if objective == "weighted":
        return [weighted + makespan]
    misses = [sum(1 for k, v in tardy.items() if v and plan.tasks[k].cls == c) for c in CLASSES]
    return [*misses, weighted, makespan]


def usage(plan, starts, worker, t):
    used = R()
    for b in plan.busy.values():
        if b.worker == worker and plan.now <= t < b.end:
            used = used + b.resources
    for k, (w, s) in starts.items():
        if w == worker and s <= t < s + plan.tasks[k].duration:
            used = used + plan.tasks[k].resources
    return used


def valid(plan, starts):
    for k, (w, s) in starts.items():
        task = plan.tasks[k]
        ready = max(
            [task.release, *(starts[d][1] + plan.tasks[d].duration for d in task.deps if d in starts)]
        )
        ready = max([ready, *(plan.busy[d].end for d in task.deps if d in plan.busy)])
        if w not in task.workers or s < ready:
            return False
    horizon = max([plan.now + 1, *(s + plan.tasks[k].duration for k, (_, s) in starts.items())])
    return all(
        usage(plan, starts, w, t).fits(plan.capacity[w])
        for w in plan.capacity
        for t in range(plan.now, horizon)
    )


def brute_force(plan, objective):
    """Лучшая оценка среди активных расписаний: все порядки и назначения, старт — самый ранний допустимый."""
    best = None
    keys = sorted(plan.tasks)
    for order in itertools.permutations(keys):
        if any(d in keys and order.index(d) > order.index(k) for k in keys for d in plan.tasks[k].deps):
            continue
        for choice in itertools.product(*(plan.tasks[k].workers for k in order)):
            starts = {}
            for k, w in zip(order, choice, strict=True):
                task = plan.tasks[k]
                t = max(
                    [task.release, *(starts[d][1] + plan.tasks[d].duration for d in task.deps if d in starts)]
                )
                t = max([t, *(plan.busy[d].end for d in task.deps if d in plan.busy)])
                while not all(
                    (usage(plan, starts, w, x) + task.resources).fits(plan.capacity[w])
                    for x in range(t, t + task.duration)
                ):
                    t += 1
                starts[k] = (w, t)
            value = score(plan, starts, objective)
            best = value if best is None or value < best else best
    return best


def random_plan(seed):
    rnd = random.Random(seed)
    capacity = {"a": R({"cpu": 4, "ram_mb": 8192}), "b": R({"cpu": 2, "ram_mb": 8192, "gpu": 1})}
    tasks = {}
    for i in range(rnd.randint(3, 5)):
        need = {"cpu": rnd.choice([1, 2, 3]), "ram_mb": rnd.choice([2048, 4096, 6144])}
        if rnd.random() < 0.3:
            need.update(gpu=1, cpu=min(need["cpu"], 2))  # GPU есть только у узла на 2 ядра
        workers = tuple(w for w in sorted(capacity) if R(need).fits(capacity[w]))
        tasks[f"t{i}"] = PlanTask(
            duration=rnd.randint(1, 4),
            resources=R(need),
            workers=workers,
            release=rnd.choice([0, 0, 1, 2]),
            deps=(f"t{rnd.randrange(i)}",) if i and rnd.random() < 0.4 else (),
            deadline=rnd.randint(2, 7) if rnd.random() < 0.6 else None,
            cls=rnd.choice(CLASSES),
        )
    busy = {"bg": Busy("a", rnd.randint(1, 3), R({"cpu": 2, "ram_mb": 2048}))} if rnd.random() < 0.5 else {}
    return PlanInput(0, tasks, busy, capacity)


@pytest.mark.parametrize("objective", ["lexicographic", "weighted"])
@pytest.mark.parametrize("seed", range(15))
def test_plan_is_valid_and_as_good_as_brute_force(seed, objective):
    plan = random_plan(seed)
    result = solve(plan, objective)
    assert result is not None and result.optimal
    assert valid(plan, result.starts)
    assert result.key == score(plan, result.starts, objective) == brute_force(plan, objective)


def test_memory_keeps_tasks_apart_even_when_cores_are_free():
    tasks = {k: PlanTask(3, R({"cpu": 2, "ram_mb": 10240}), ("laptop",), 0) for k in ("x", "y")}
    plan = PlanInput(0, tasks, {}, {"laptop": R({"cpu": 8, "ram_mb": 16384})})
    result = solve(plan)
    assert result is not None and sorted(s for _, s in result.starts.values()) == [0, 3]


def test_running_attempt_holds_its_resources_and_dependants_wait_for_it():
    tasks = {
        "next": PlanTask(1, R({"gpu": 1}), ("n",), 0, deps=("bg",)),
        "other": PlanTask(1, R({"gpu": 2}), ("n",), 0),
    }
    plan = PlanInput(0, tasks, {"bg": Busy("n", 4, R({"gpu": 1}))}, {"n": R({"gpu": 2})})
    result = solve(plan)
    assert result is not None and result.starts["next"] == ("n", 4) and result.starts["other"][1] >= 4


def test_objectives_disagree_on_one_miss_versus_smaller_tardiness():
    """Один узел: lexicographic держит high-задачу Y в срок ценой большого опоздания critical Z,
    weighted пропускает и Y, зато сумма 2^класс × опоздание меньше."""
    tasks = {
        "x": PlanTask(3, R({"gpu": 1}), ("n",), 0, deadline=3, cls=3),
        "y": PlanTask(3, R({"gpu": 1}), ("n",), 0, deadline=6, cls=2),
        "z": PlanTask(3, R({"gpu": 1}), ("n",), 0, deadline=4, cls=3),
    }
    plan = PlanInput(0, tasks, {}, {"n": R({"gpu": 1})})
    lexicographic, weighted = solve(plan), solve(plan, "weighted")
    assert lexicographic is not None and weighted is not None
    order = lambda p: sorted(p.starts, key=lambda k: p.starts[k][1])  # noqa: E731
    assert order(lexicographic) == ["x", "y", "z"] and lexicographic.key == [1, 0, 0, 0, 40, 9]
    assert order(weighted) == ["x", "z", "y"] and weighted.key == [37]


def test_same_input_gives_the_same_plan():
    plan = random_plan(3)
    assert solve(plan, seed=7) == solve(plan, seed=7)


def test_no_solution_within_the_capacity_is_none():
    tasks = {"big": PlanTask(1, R({"gpu": 4}), ("n",), 0)}
    assert solve(PlanInput(0, tasks, {}, {"n": R({"gpu": 2})})) is None
