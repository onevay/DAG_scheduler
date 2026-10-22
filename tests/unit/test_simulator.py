import pytest

from expsched.domain.models import Event, EvType
from expsched.domain.snapshot import Decision
from expsched.domain.states import TaskStatus
from expsched.engine.errors import InvalidDecision
from expsched.engine.events import Finish
from expsched.engine.simulator import Simulator


def brief(log, *types):
    """(type, t, task, worker) для записей нужных типов — удобно сравнивать трассы."""
    return [
        (r["type"], r["t"], r.get("task"), r.get("worker")) for r in log if not types or r["type"] in types
    ]


class Always:
    """Политика, которая каждую эпоху выдаёт одно и то же решение (для проверки guard)."""

    name = "always"

    def __init__(self, *decisions):
        self.decisions = list(decisions)

    def decide(self, snapshot):
        return self.decisions


# ---- основной цикл ----


def test_chain_runs_in_dependency_order(make_task, make_problem, greedy):
    log = Simulator(make_problem([make_task("a", 2), make_task("b", 3, "a")]), greedy).run_all()
    assert brief(log) == [
        ("WAIT", 0, "b", None),  # b ждёт a
        ("READY", 0, "a", None),
        ("START", 0, "a", "n0"),
        ("FINISH", 2, "a", "n0"),
        ("READY", 2, "b", None),
        ("START", 2, "b", "n0"),
        ("FINISH", 5, "b", "n0"),
    ]
    assert [r["seq"] for r in log] == list(range(len(log)))


def test_start_record_carries_attempt_resume_point_and_a_copy_of_the_reason(make_task, make_problem):
    decision = Decision("START", "a", "n0", {"code": "TEST"})
    sim = Simulator(make_problem([make_task("a", 2)]), Always(decision))
    sim.step()
    decision.reason["code"] = "CHANGED"
    (start,) = [r for r in sim.journal.records if r["type"] == "START"]
    assert (start["attempt"], start["resume_from"], start["reason"]) == (1, 0, {"code": "TEST"})


def test_capacity_is_respected_and_freed_on_finish(make_task, make_problem, greedy):
    tasks = [make_task(k, 2, gpu=2) for k in ("a", "b", "c")]
    sim = Simulator(make_problem(tasks), greedy)
    log = sim.run_all()
    assert brief(log, "START") == [("START", 0, "a", "n0"), ("START", 0, "b", "n0"), ("START", 2, "c", "n0")]
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
    assert sim.state.free == {"n0": make_problem([]).workers["n0"].capacity}


def test_step_returns_epoch_records_and_none_at_the_end(make_task, make_problem, greedy):
    sim = Simulator(make_problem([make_task("a", 2)]), greedy)
    assert [r["type"] for r in sim.step()] == ["READY", "START"]
    assert [r["type"] for r in sim.step()] == ["FINISH"]
    assert sim.step() is None


def test_stale_finish_is_ignored(make_task, make_problem, greedy):
    sim = Simulator(make_problem([make_task("a", 3)]), greedy)
    sim.step()
    sim.queue.push(1, Finish("a", attempt=99))  # как после прерванной попытки
    assert sim.step() == []  # эпоха без записей
    assert brief(sim.run_all(), "FINISH") == [("FINISH", 3, "a", "n0")]


def test_run_all_is_idempotent(make_task, make_problem, greedy):
    sim = Simulator(make_problem([make_task("a", 1, "ghost")]), greedy)
    first = sim.run_all()
    assert sim.run_all() == first


def test_empty_problem(make_problem, greedy):
    assert Simulator(make_problem([]), greedy).run_all() == []


# ---- release_time ----


@pytest.mark.parametrize(
    ("dep_duration", "ready_at"),
    [(None, 5), (2, 5), (7, 7)],  # без зависимостей; зависимость раньше release; зависимость позже release
)
def test_task_becomes_ready_exactly_at_max_of_release_and_dependencies(
    make_task, make_problem, greedy, dep_duration, ready_at
):
    tasks = [make_task("late", 1, *(["dep"] if dep_duration else []), release=5)]
    if dep_duration:
        tasks.append(make_task("dep", dep_duration))
    log = Simulator(make_problem(tasks), greedy).run_all()
    assert ("READY", ready_at, "late", None) in brief(log, "READY")
    assert ("START", ready_at, "late", "n0") in brief(log, "START")


# ---- некорректный вход и диагностика ----


def test_cycle_in_input_ends_with_stuck_diagnostics(make_task, make_problem, greedy):
    log = Simulator(make_problem([make_task("a", 1, "b"), make_task("b", 1, "a")]), greedy).run_all()
    assert [(r["type"], r["code"], r["task"]) for r in log] == [
        ("WAIT", "WAIT_DEPS", "a"),
        ("WAIT", "WAIT_DEPS", "b"),
        ("DIAGNOSTIC", "STUCK", "a"),
        ("DIAGNOSTIC", "STUCK", "b"),
    ]


def test_worker_events_are_not_supported_yet(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 5)], events=[Event(2, EvType.WORKER_DOWN, worker="n0")])
    with pytest.raises(NotImplementedError, match="worker_down"):
        Simulator(problem, greedy).run_all()


# ---- guard ----


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (Decision("START", "b", "n0"), "not READY"),  # b ждёт a
        (Decision("START", "ghost", "n0"), "not READY"),
        (Decision("START", "a", "nowhere"), "unknown or down"),
        (Decision("START", "big", "n1"), "not eligible"),  # на n1 всего 2 GPU
        (Decision("JUMP", "a", "n0"), "unknown action"),
    ],
)
def test_guard_rejects_invalid_decisions(make_task, make_problem, decision, message):
    problem = make_problem([make_task("a"), make_task("b", 1, "a"), make_task("big", 1, gpu=4)], gpus=(4, 2))
    with pytest.raises(InvalidDecision, match=message):
        Simulator(problem, Always(decision)).run_all()


def test_guard_rejects_overbooking_a_busy_node(make_task, make_problem):
    problem = make_problem([make_task("a", 2, gpu=4), make_task("b", 2, gpu=4)])
    policy = Always(Decision("START", "a", "n0"), Decision("START", "b", "n0"))
    with pytest.raises(InvalidDecision, match="not enough free resources"):
        Simulator(problem, policy).run_all()


def test_preemption_and_reservation_are_not_supported_yet(make_task, make_problem):
    with pytest.raises(NotImplementedError, match="PREEMPT"):
        Simulator(make_problem([make_task("a")]), Always(Decision("PREEMPT", "a", "n0"))).run_all()
