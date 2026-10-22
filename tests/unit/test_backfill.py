import random

import pytest

from expsched.contract.io_models import PolicyIn
from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.engine.simulator import Simulator
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.priority import PriorityPolicy
from expsched.policies.registry import make_policy
from expsched.verify.invariants import verify


def starts(log):
    return {r["task"]: r["t"] for r in log if r["type"] == "START"}


def reservations(log):
    return [(r["t"], r["task"], r["worker"], r["t_res"]) for r in log if r["type"] == "RESERVE"]


def start_reason(log, task):
    return next(r["reason"] for r in log if r["type"] == "START" and r["task"] == task)


@pytest.fixture
def hole(make_task, make_problem):
    """a держит половину узла до 4, big ждёт весь узел; short успевает до 4, long — нет."""
    tasks = [
        make_task("a", 4, gpu=2, priority=6),
        make_task("big", 2, gpu=4, priority=3),
        make_task("short", 3, gpu=1),
        make_task("long", 6, gpu=1),
    ]
    return make_problem(tasks, gpus=(4,))


def test_backfill_keeps_reservation(hole):
    log = Simulator(hole, BackfillPolicy()).run_all()
    assert starts(log) == {"a": 0, "short": 0, "big": 4, "long": 6}
    # в t = 3 резерв big пересчитан, но не изменился; в t = 4 big стартует, и головной становится long
    assert reservations(log) == [(0, "big", "n0", 4), (4, "long", "n0", 6)]
    assert start_reason(log, "short") == {"code": "BACKFILL", "head": "big", "t_res": 4}
    assert verify(hole, log) == []


def test_without_reservation_the_large_task_starves_longer(hole):
    # priority сразу занимает дыру длинной задачей, и big ждёт её окончания
    assert starts(Simulator(hole, PriorityPolicy()).run_all())["big"] == 6


def test_backfill_may_use_what_is_left_after_the_reserved_task(make_task, make_problem):
    tasks = [
        make_task("a", 4, gpu=2, priority=6),
        make_task("big", 2, gpu=3, priority=3),  # в t = 4 на узле будет 4 GPU: 1 останется лишним
        make_task("long1", 10, gpu=1),
        make_task("long2", 10, gpu=1),
    ]
    log = Simulator(make_problem(tasks, gpus=(4,)), BackfillPolicy()).run_all()
    assert starts(log) == {"a": 0, "long1": 0, "big": 4, "long2": 6}


def test_other_workers_are_free_to_fill(make_task, make_problem):
    tasks = [
        make_task("a", 3, gpu=4, priority=6),
        make_task("big", 1, gpu=4, priority=3),
        make_task("small", 10, gpu=2),
    ]
    log = Simulator(make_problem(tasks, gpus=(4, 2)), BackfillPolicy()).run_all()
    assert reservations(log) == [(0, "big", "n0", 3)]
    assert starts(log) == {"a": 0, "small": 0, "big": 3}


def test_reservation_counts_everything_released_at_the_same_moment(make_task, make_problem):
    tasks = [
        make_task("a", 2, gpu=2, priority=6),
        make_task("b", 2, gpu=2, priority=6),
        make_task("big", 1, gpu=3, priority=3),
        make_task("long", 5, gpu=1),
    ]
    log = Simulator(make_problem(tasks, gpus=(4,)), BackfillPolicy()).run_all()
    # в t = 2 освобождаются все 4 GPU: big берёт 3, а 1 остаётся для long уже сейчас
    assert starts(log) == {"a": 0, "b": 0, "big": 2, "long": 2}
    assert start_reason(log, "long")["code"] == "TOP_OF_QUEUE"  # в t = 0 места не было вовсе


def test_registry_builds_backfill_with_aging():
    policy = make_policy(PolicyIn())  # имя по умолчанию
    assert isinstance(policy, BackfillPolicy) and policy.aging == 360


def _independent_tasks(seed):
    rnd = random.Random(seed)
    tasks = {
        f"t{i}": Task(f"t{i}", rnd.randint(1, 6), Resources({"gpu": rnd.choice([1, 2, 3, 4])}))
        for i in range(rnd.randint(5, 15))
    }
    workers = {w: Worker(w, Resources({"gpu": 4})) for w in ("a", "b")}
    return Problem(tasks, workers)


@pytest.mark.parametrize("seed", range(30))
def test_reserved_task_starts_no_later_than_promised(seed):
    """Порядок очереди не меняется (задачи независимы, готовы сразу, без дедлайнов): резерв выполняется."""
    problem = _independent_tasks(seed)
    log = Simulator(problem, BackfillPolicy()).run_all()
    first = starts(log)
    assert all(first[task] <= t_res for _, task, _, t_res in reservations(log))
    assert verify(problem, log) == []
