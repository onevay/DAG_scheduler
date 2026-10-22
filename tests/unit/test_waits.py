import itertools

import pytest

from expsched.engine.simulator import Simulator
from expsched.engine.state import SimState
from expsched.engine.waits import ready_wait
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy

READY_CODES = {"WAIT_WORKER_DOWN", "WAIT_RESOURCES", "WAIT_RESERVATION", "WAIT_PRIORITY"}
POLICIES = [FifoPolicy(), FifoPolicy(skip=True), PriorityPolicy(aging=3)]


def waits(log, task):
    return [(r["t"], r["code"], r["detail"]) for r in log if r["type"] == "WAIT" and r["task"] == task]


def assert_every_waiting_ready_task_has_a_reason(log):
    """В конце каждой эпохи у каждой готовой нестартовавшей задачи есть причина, записанная после READY."""
    ready: set[str] = set()
    reason: dict[str, str] = {}
    for t, records in itertools.groupby(log, key=lambda r: r["t"]):
        for r in records:
            if r["type"] == "READY":
                ready.add(r["task"])
                reason.pop(r["task"], None)
            elif r["type"] == "START":
                ready.discard(r["task"])
            elif r["type"] == "WAIT" and r["task"] in ready:
                assert r["code"] in READY_CODES, r
                reason[r["task"]] = r["code"]
        assert all(k in reason for k in ready), (t, sorted(ready - reason.keys()))


# ---- задачи в PENDING ----


def test_task_waiting_for_dependencies(make_task, make_problem, greedy):
    log = Simulator(make_problem([make_task("a", 2), make_task("b", 1, "a")]), greedy).run_all()
    assert waits(log, "b") == [(0, "WAIT_DEPS", {"pending": ["a"]})]  # стартует сразу после READY


def test_task_waiting_for_its_release_time(make_task, make_problem, greedy):
    tasks = [make_task("early", 2), make_task("late", 1, release=5), make_task("both", 1, "early", release=5)]
    log = Simulator(make_problem(tasks), greedy).run_all()
    assert waits(log, "late") == [(0, "WAIT_RELEASE", {"until": 5})]
    assert waits(log, "both") == [(0, "WAIT_DEPS", {"pending": ["early"]}), (2, "WAIT_RELEASE", {"until": 5})]


# ---- готовые задачи ----


def test_task_waiting_for_resources(make_task, make_problem):
    tasks = [make_task("a", 3, gpu=2), make_task("b", 1, gpu=2, release=1)]  # в t = 1 узел уже занят
    log = Simulator(make_problem(tasks, gpus=(2,)), FifoPolicy()).run_all()
    assert waits(log, "b") == [
        (0, "WAIT_RELEASE", {"until": 1}),
        (1, "WAIT_RESOURCES", {"need": {"gpu": 2}, "best": {"n0": {}}}),
    ]


def test_task_passed_over_by_the_policy(make_task, make_problem):
    tasks = [make_task("a", 3, gpu=2, priority=6), make_task("b", 1, gpu=2)]  # место было, но досталось a
    log = Simulator(make_problem(tasks, gpus=(2,)), PriorityPolicy()).run_all()
    assert waits(log, "b") == [(0, "WAIT_PRIORITY", {"started": ["a"]})]


def test_reason_is_written_only_when_it_changes(make_task, make_problem):
    # b ждёт ресурсы с t = 1 по t = 3; эпоха в t = 2 (release задачи c) ту же причину не повторяет
    tasks = [
        make_task("a", 3, gpu=2),
        make_task("b", 1, gpu=2, release=1),
        make_task("c", 1, gpu=2, release=2),
    ]
    log = Simulator(make_problem(tasks, gpus=(2,)), FifoPolicy()).run_all()
    assert [code for _, code, _ in waits(log, "b")] == ["WAIT_RELEASE", "WAIT_RESOURCES"]


def test_no_live_worker_is_reported_first(make_task, make_problem):
    state = SimState.from_problem(make_problem([make_task("a", 1, gpu=2)], gpus=(4, 2)))
    state.up = {"n0": False, "n1": False}
    assert ready_wait(state, "a", state.free, []) == ("WAIT_WORKER_DOWN", {"eligible": ["n0", "n1"]})


# ---- полнота причин ----


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_demo_every_waiting_ready_task_has_a_reason(demo_problem, policy):
    assert_every_waiting_ready_task_has_a_reason(Simulator(demo_problem, policy).run_all())


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(20))
def test_random_dags_every_waiting_ready_task_has_a_reason(random_problem, seed, policy):
    assert_every_waiting_ready_task_has_a_reason(Simulator(random_problem(seed), policy).run_all())


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(20))
def test_random_dags_with_failures_every_waiting_ready_task_has_a_reason(random_problem, seed, policy):
    problem = random_problem(seed, failures=True)
    assert_every_waiting_ready_task_has_a_reason(Simulator(problem, policy).run_all())
