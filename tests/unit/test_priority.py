import dataclasses

import pytest

from expsched.analysis import analyze
from expsched.contract.io_models import PolicyIn
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator
from expsched.engine.snapshot import build_snapshot
from expsched.engine.state import SimState
from expsched.policies.priority import PriorityPolicy
from expsched.policies.registry import make_policy


def starts(log):
    return {r["task"]: r["t"] for r in log if r["type"] == "START"}


def finishes(log):
    return {r["task"]: r["t"] for r in log if r["type"] == "FINISH"}


def run(problem, policy=None):
    return Simulator(problem, policy or PriorityPolicy()).run_all()


# ---- порядок очереди ----


def test_class_goes_first(make_task, make_problem):
    tasks = [make_task("a", 1, priority=1), make_task("b", 1, priority=6)]
    assert starts(run(make_problem(tasks, gpus=(1,)))) == {"b": 0, "a": 1}


def test_within_a_class_the_smaller_slack_goes_first(make_task, make_problem):
    tasks = [make_task("a", 1, deadline=10), make_task("b", 1, deadline=3)]
    assert starts(run(make_problem(tasks, gpus=(1,)))) == {"b": 0, "a": 1}


def test_without_deadlines_the_longer_remaining_chain_goes_first(make_task, make_problem):
    tasks = [make_task("a", 1), make_task("b", 1), make_task("c", 5, "b")]  # ранг b = 6, ранг a = 1
    assert starts(run(make_problem(tasks, gpus=(1,))))["b"] == 0


def test_a_task_without_room_does_not_hold_the_queue(make_task, make_problem):
    tasks = [
        make_task("run", 5, gpu=4, priority=10),
        make_task("big", 1, gpu=4, priority=6),  # места нет до t = 5
        make_task("small", 1, gpu=1, priority=1),
    ]
    assert starts(run(make_problem(tasks, gpus=(4, 2)))) == {"run": 0, "small": 0, "big": 5}


# ---- aging ----


@pytest.mark.parametrize(
    ("priority", "aging", "expected_class"),
    [
        (1, None, 0),  # без aging класс не меняется
        (1, 3, 2),  # LOW + 10 // 3, но не выше HIGH
        (1, 6, 1),  # LOW + 1
        (10, 3, 3),  # CRITICAL остаётся CRITICAL
    ],
)
def test_aging_raises_the_class_up_to_high(make_task, make_problem, priority, aging, expected_class):
    state = SimState.from_problem(make_problem([make_task("a", 1, priority=priority)]))
    state.ready.add("a")
    state.ready_since["a"] = 0
    key = PriorityPolicy(aging).key(build_snapshot(state, 10), "a")
    assert -key[0] == expected_class


# ---- причина старта ----


def test_start_reason_shows_class_slack_and_rank(make_task, make_problem):
    log = run(make_problem([make_task("a", 2, priority=6, deadline=5), make_task("b", 1)], gpus=(1,)))
    reasons = {r["task"]: r["reason"] for r in log if r["type"] == "START"}
    assert reasons["a"] == {"code": "TOP_OF_QUEUE", "class": 2, "slack": 3, "rank": 2}
    assert reasons["b"]["slack"] is None  # без дедлайна запас не ограничен


# ---- демо ----


def test_demo_without_the_urgent_task_reaches_the_lower_bound(demo_problem, check_capacity):
    problem = dataclasses.replace(
        demo_problem, tasks={k: t for k, t in demo_problem.tasks.items() if k != "urgent_eval"}
    )
    sim = Simulator(problem, PriorityPolicy())
    log = sim.run_all()
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
    check_capacity(problem, log)
    assert max(finishes(log).values()) == analyze(problem).lb == 21


def test_demo_urgent_task_meets_its_deadline(demo_problem, check_capacity):
    log = run(demo_problem)
    check_capacity(demo_problem, log)
    assert finishes(log)["urgent_eval"] <= 14


def test_registry_passes_aging_from_the_settings():
    policy = make_policy(PolicyIn(name="priority", aging=5, preemption="never"))
    assert isinstance(policy, PriorityPolicy) and policy.aging == 5
