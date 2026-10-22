import copy
import dataclasses

import pytest

from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy
from expsched.verify.attempts import attempts_from_log
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
    assert (a.task, a.start, a.end) == ("a", 0, 4)
    assert (b.task, b.start, b.end) == ("b", 2, None)
