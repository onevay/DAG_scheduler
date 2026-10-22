import copy
import dataclasses

import pytest

from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy
from expsched.verify.attempts import attempts_from_log, down_intervals
from expsched.verify.invariants import verify

POLICIES = [FifoPolicy(), FifoPolicy(skip=True), PriorityPolicy(aging=3)]


def demo_log(demo_problem):
    return Simulator(demo_problem, PriorityPolicy()).run_all()


def changed(log, kind, key, **fields):
    """Копия журнала, в которой у первой записи `kind` задачи `key` изменены поля."""
    out = copy.deepcopy(log)
    record = next(r for r in out if r["type"] == kind and r.get("task") == key)
    record.update(fields)
    return out


def codes(violations, task=None):
    return {v.split()[0] for v in violations if task is None or v.split()[1].startswith(task)}


# ---- корректные расписания ----


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_schedules_of_every_policy_are_valid(demo_problem, policy):
    assert verify(demo_problem, Simulator(demo_problem, policy).run_all()) == []


def test_schedule_with_a_submitted_task_is_valid(demo_problem):
    urgent = demo_problem.tasks["urgent_eval"]
    online = dataclasses.replace(
        demo_problem,
        tasks={k: t for k, t in demo_problem.tasks.items() if k != "urgent_eval"},
        events=[Event(9, EvType.SUBMIT, task=urgent)],
    )
    assert verify(online, Simulator(online, PriorityPolicy()).run_all()) == []


# ---- испорченные журналы ----


def test_start_before_a_dependency_finishes(demo_problem):
    log = changed(demo_log(demo_problem), "START", "ft_det", t=5)
    log = changed(log, "FINISH", "ft_det", t=9)  # работа та же, но pretrain_vit кончается в 10
    assert "I2" in codes(verify(demo_problem, log), "ft_det")


def test_task_moved_to_a_worker_that_is_too_small(demo_problem):
    log = changed(demo_log(demo_problem), "START", "pretrain_vit", worker="node-C")
    log = changed(log, "FINISH", "pretrain_vit", worker="node-C")
    violations = verify(demo_problem, log)
    assert {"I3", "I6"} <= codes(violations)
    assert any(v.startswith("I3 node-C") for v in violations)


def test_wrong_amount_of_work(demo_problem):
    log = changed(demo_log(demo_problem), "FINISH", "prep_data", t=3)
    assert "I1" in codes(verify(demo_problem, log), "prep_data")


def test_start_before_release(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 1, release=5)])
    log = changed(Simulator(problem, greedy).run_all(), "START", "a", t=0)
    log = changed(log, "FINISH", "a", t=1)
    assert codes(verify(problem, log)) == {"I5"}


def test_second_attempt_overlapping_a_successful_one(demo_problem):
    log = demo_log(demo_problem)
    log += [
        {
            "seq": -1,
            "t": 1,
            "type": "START",
            "task": "prep_data",
            "worker": "node-A",
            "attempt": 2,
            "resume_from": 0,
        },
        {"seq": -1, "t": 3, "type": "FINISH", "task": "prep_data", "worker": "node-A", "attempt": 2},
    ]
    assert {"I7", "I12"} <= codes(verify(demo_problem, log), "prep_data")


def test_task_missing_from_the_input(demo_problem):
    log = changed(demo_log(demo_problem), "START", "prep_data", task="ghost")
    assert "I0" in codes(verify(demo_problem, log))


# ---- попытки ----


def test_attempts_are_rebuilt_from_starts_and_finishes():
    log = [
        {"t": 0, "type": "START", "task": "a", "worker": "w", "attempt": 1, "resume_from": 0},
        {"t": 1, "type": "FINISH", "task": "ghost", "worker": "w", "attempt": 1},  # без старта — пропуск
        {"t": 2, "type": "START", "task": "b", "worker": "w", "attempt": 1, "resume_from": 0},
        {"t": 4, "type": "FINISH", "task": "a", "worker": "w", "attempt": 1},
    ]
    a, b = attempts_from_log(log)
    assert (a.task, a.start, a.end, a.outcome) == ("a", 0, 4, "DONE")
    assert (b.task, b.start, b.end, b.outcome) == ("b", 2, None, None)


# ---- отказы узлов ----


def with_failure(demo_problem):
    """Демо, в котором node-A падает в t = 5 и возвращается в t = 15."""
    events = [Event(5, EvType.WORKER_DOWN, worker="node-A"), Event(15, EvType.WORKER_UP, worker="node-A")]
    return dataclasses.replace(demo_problem, events=events)


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_schedules_with_a_failure_are_valid(demo_problem, policy):
    problem = with_failure(demo_problem)
    assert verify(problem, Simulator(problem, policy).run_all()) == []


@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(30))
def test_random_schedules_with_failures_are_valid(random_problem, seed, policy):
    problem = random_problem(seed, failures=True)
    assert verify(problem, Simulator(problem, policy).run_all()) == []


def test_interrupted_attempt_is_rebuilt_with_its_saved_work(demo_problem):
    attempts = [
        a for a in attempts_from_log(demo_log(with_failure(demo_problem))) if a.task == "pretrain_vit"
    ]
    assert [(a.attempt, a.worker, a.start, a.end, a.outcome, a.saved) for a in attempts] == [
        (1, "node-A", 2, 5, "INT", 2),
        (2, "node-B", 5, 11, "DONE", 0),
    ]


def test_attempt_running_on_a_worker_that_is_down(demo_problem):
    problem = with_failure(demo_problem)
    log = Simulator(problem, PriorityPolicy()).run_all()
    log = changed(log, "START", "eval_asr", worker="node-A")  # eval_asr шла в 8–10, а node-A лежит с 5 до 15
    log = changed(log, "FINISH", "eval_asr", worker="node-A")
    assert "I4" in codes(verify(problem, log), "eval_asr")


def test_resume_point_must_match_the_saved_work(demo_problem):
    problem = with_failure(demo_problem)
    log = changed(Simulator(problem, PriorityPolicy()).run_all(), "INTERRUPTED", "pretrain_vit", saved=1)
    assert "I11" in codes(verify(problem, log), "pretrain_vit")


def test_down_intervals_from_the_journal():
    log = [
        {"t": 2, "type": "WORKER_DOWN", "worker": "a"},
        {"t": 5, "type": "WORKER_UP", "worker": "a"},
        {"t": 7, "type": "WORKER_DOWN", "worker": "b"},
    ]
    assert down_intervals(log) == {"a": [(2, 5)], "b": [(7, None)]}
