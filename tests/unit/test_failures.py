import pytest

from expsched.domain.models import Event, EvType
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator


def down(t, worker="n0"):
    return Event(t, EvType.WORKER_DOWN, worker=worker)


def up(t, worker="n0"):
    return Event(t, EvType.WORKER_UP, worker=worker)


def records(log, kind, **fields):
    return [r for r in log if r["type"] == kind and all(r.get(k) == v for k, v in fields.items())]


def run(problem, policy):
    sim = Simulator(problem, policy)
    return sim, sim.run_all()


def test_failure_keeps_work_up_to_the_last_checkpoint(make_task, make_problem, greedy):
    # 8 часов работы, чекпоинт каждые 2; узел падает через 5 часов: сохранено 4, потеряно 1
    problem = make_problem([make_task("a", 8, gpu=4, ckpt=2)], gpus=(4, 4), events=[down(5)])
    sim, log = run(problem, greedy)
    (interrupted,) = records(log, "INTERRUPTED")
    assert {k: interrupted[k] for k in ("t", "worker", "cause", "saved", "lost", "remaining")} == {
        "t": 5,
        "worker": "n0",
        "cause": "WORKER_DOWN",
        "saved": 4,
        "lost": 1,
        "remaining": 4,
    }
    second = records(log, "START", attempt=2)[0]
    assert (second["t"], second["worker"], second["resume_from"]) == (5, "n1", 4)
    assert [r["t"] for r in records(log, "FINISH")] == [9]  # окончание первой попытки (в 8) пропущено
    assert sim.state.status["a"] is TaskStatus.SUCCEEDED


def test_without_checkpoints_all_work_of_the_attempt_is_lost(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 8, gpu=4)], gpus=(4, 4), events=[down(5)])
    _, log = run(problem, greedy)
    assert records(log, "INTERRUPTED")[0]["saved"] == 0
    assert records(log, "START", attempt=2)[0]["resume_from"] == 0
    assert [r["t"] for r in records(log, "FINISH")] == [13]


def test_task_finishing_at_the_moment_of_failure_succeeds(make_task, make_problem, greedy):
    _, log = run(make_problem([make_task("a", 5)], events=[down(5)]), greedy)
    assert records(log, "INTERRUPTED") == [] and [r["t"] for r in records(log, "FINISH")] == [5]


def test_failure_of_an_idle_worker_and_repeated_events(make_task, make_problem, greedy):
    problem = make_problem(
        [make_task("a", 3)], gpus=(4, 4), events=[down(1, "n1"), down(2, "n1"), up(4, "n1"), up(5, "n1")]
    )
    _, log = run(problem, greedy)
    assert [(r["type"], r["t"]) for r in log if r["type"] in ("WORKER_DOWN", "WORKER_UP")] == [
        ("WORKER_DOWN", 1),
        ("WORKER_UP", 4),
    ]
    assert records(log, "INTERRUPTED") == []


def test_task_waits_while_its_only_worker_is_down(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 3)], events=[down(1), up(4)])
    sim, log = run(problem, greedy)
    assert [r["code"] for r in records(log, "WAIT", task="a")] == ["WAIT_WORKER_DOWN"]
    assert [(r["t"], r["attempt"]) for r in records(log, "START")] == [(0, 1), (4, 2)]
    assert records(log, "READY", task="a")[-1]["t"] == 1  # после прерывания задача снова готова
    assert sim.state.status["a"] is TaskStatus.SUCCEEDED and sim.state.free == {
        "n0": problem.workers["n0"].capacity
    }


def test_event_for_an_unknown_worker_is_rejected(make_task, make_problem, greedy):
    with pytest.raises(ValueError, match="unknown worker"):
        run(make_problem([make_task("a")], events=[down(1, "ghost")]), greedy)
