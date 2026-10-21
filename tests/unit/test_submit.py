import pytest

from expsched.domain.models import Event, EvType
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator


def submit(time, task):
    return Event(time, EvType.SUBMIT, task=task)


def run(problem, policy):
    sim = Simulator(problem, policy)
    return sim, sim.run_all()


def records(log, kind, task):
    return [r for r in log if r["type"] == kind and r.get("task") == task]


def test_submitted_task_is_journaled_and_starts_at_once(make_task, make_problem, greedy):
    urgent = make_task("urgent", 2, priority=10, deadline=9)
    _, log = run(make_problem([make_task("a", 5)], events=[submit(3, urgent)]), greedy)
    (record,) = records(log, "SUBMIT", "urgent")
    assert (record["t"], record["priority"], record["deadline"]) == (3, 10, 9)
    assert [r["t"] for r in records(log, "START", "urgent")] == [3]


def test_dependency_finished_earlier_does_not_hold_the_task(make_task, make_problem, greedy):
    _, log = run(make_problem([make_task("a", 2)], events=[submit(5, make_task("late", 1, "a"))]), greedy)
    assert [r["t"] for r in records(log, "READY", "late")] == [5]


def test_dependency_finishing_in_the_same_moment_is_already_done(make_task, make_problem, greedy):
    # окончание обрабатывается раньше подачи, поэтому задача готова сразу
    _, log = run(make_problem([make_task("a", 4)], events=[submit(4, make_task("late", 1, "a"))]), greedy)
    assert [r["t"] for r in records(log, "READY", "late")] == [4]


def test_task_waits_for_a_running_dependency(make_task, make_problem, greedy):
    _, log = run(make_problem([make_task("a", 6)], events=[submit(2, make_task("late", 1, "a"))]), greedy)
    assert [r["t"] for r in records(log, "READY", "late")] == [6]


def test_release_later_than_submission_delays_readiness(make_task, make_problem, greedy):
    late = make_task("late", 1, release=7)
    _, log = run(make_problem([make_task("a", 1)], events=[submit(2, late)]), greedy)
    assert [r["t"] for r in records(log, "READY", "late")] == [7]


def test_unknown_dependency_makes_the_task_infeasible(make_task, make_problem, greedy):
    sim, log = run(make_problem([make_task("a")], events=[submit(1, make_task("late", 1, "ghost"))]), greedy)
    (diagnostic,) = records(log, "DIAGNOSTIC", "late")
    assert (diagnostic["code"], diagnostic["detail"]) == ("E010", {"task": "late", "dep": "ghost"})
    assert sim.state.status["late"] is TaskStatus.INFEASIBLE
    assert records(log, "START", "late") == []


def test_task_that_fits_no_worker_is_infeasible(make_task, make_problem, greedy):
    sim, log = run(make_problem([make_task("a")], events=[submit(1, make_task("huge", 1, gpu=8))]), greedy)
    (diagnostic,) = records(log, "DIAGNOSTIC", "huge")
    assert diagnostic["code"] == "E020" and diagnostic["detail"]["need"] == 8
    assert sim.state.status["huge"] is TaskStatus.INFEASIBLE


def test_duplicate_dependency_is_not_an_error(make_task, make_problem, greedy):
    sim, _ = run(make_problem([make_task("a")], events=[submit(2, make_task("late", 1, "a", "a"))]), greedy)
    assert sim.state.status["late"] is TaskStatus.SUCCEEDED


def test_duplicate_id_is_reported_and_ignored(make_task, make_problem, greedy):
    original = make_task("a", 3)
    sim, log = run(make_problem([original], events=[submit(1, make_task("a", 9))]), greedy)
    assert [r["code"] for r in records(log, "DIAGNOSTIC", "a")] == ["DUPLICATE_TASK_ID"]
    assert sim.state.tasks["a"] is original and records(log, "FINISH", "a")[0]["t"] == 3


def test_submit_without_a_task_is_rejected(make_task, make_problem, greedy):
    problem = make_problem([make_task("a")], events=[Event(1, EvType.SUBMIT)])
    with pytest.raises(ValueError, match="without a task"):
        Simulator(problem, greedy).run_all()
