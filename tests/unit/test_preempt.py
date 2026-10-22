import dataclasses

import pytest

from expsched.domain.snapshot import Decision, Snapshot
from expsched.engine.errors import InvalidDecision
from expsched.engine.simulator import Simulator
from expsched.metrics.metrics import metrics
from expsched.verify.attempts import attempts_from_log
from expsched.verify.invariants import verify


class Preempting:
    """Готовые задачи по id на n0, пока помещаются. Задача из `victims` вытесняет свою жертву, если та идёт.

    С `preempt_last=True` вытеснение стоит в списке после старта: движок всё равно применяет его первым.
    """

    name = "preempting-test"

    def __init__(self, victims: dict[str, str], preempt_last: bool = False) -> None:
        self.victims = victims
        self.preempt_last = preempt_last

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        free = snapshot.free["n0"]
        preempts, starts = [], []
        for key in snapshot.ready:
            victim = self.victims.get(key)
            if victim in snapshot.running:
                free = free + snapshot.tasks[victim].resources
                preempts.append(Decision("PREEMPT", victim, "n0", {"code": "PREEMPTED_BY", "by": key}))
            if snapshot.tasks[key].resources.fits(free):
                free = free - snapshot.tasks[key].resources
                starts.append(Decision("START", key, "n0"))
        return starts + preempts if self.preempt_last else preempts + starts


class Fixed:
    name = "fixed-test"

    def __init__(self, *decisions: Decision) -> None:
        self.decisions = list(decisions)

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        return self.decisions if snapshot.t == 1 else [Decision("START", k, "n0") for k in snapshot.ready]


def records(log, kind):
    return [r for r in log if r["type"] == kind]


@pytest.fixture
def urgent(make_task, make_problem):
    """low занимает весь узел с 0; в t = 3 появляется urgent и вытесняет её."""
    tasks = [
        make_task("low", 6, gpu=4, preemptible=True, ckpt=2, max_retries=0),
        make_task("urgent", 2, gpu=4, release=3),
    ]
    return make_problem(tasks, gpus=(4,))


@pytest.mark.parametrize("preempt_last", [False, True])
def test_preemption_frees_the_worker_before_starts(urgent, preempt_last):
    log = Simulator(urgent, Preempting({"urgent": "low"}, preempt_last)).run_all()
    (interrupted,) = records(log, "INTERRUPTED")
    assert interrupted == {
        "seq": interrupted["seq"],
        "t": 3,
        "type": "INTERRUPTED",
        "task": "low",
        "attempt": 1,
        "worker": "n0",
        "cause": "PREEMPT",
        "saved": 2,
        "lost": 1,
        "remaining": 4,
        "reason": {"code": "PREEMPTED_BY", "by": "urgent"},
    }
    assert [(r["t"], r["task"], r["resume_from"]) for r in records(log, "START")] == [
        (0, "low", 0),
        (3, "urgent", 0),
        (5, "low", 2),
    ]


def test_preemption_does_not_use_up_retries(urgent):
    sim = Simulator(urgent, Preempting({"urgent": "low"}))
    sim.run_all()
    assert str(sim.state.status["low"]) == "SUCCEEDED"  # max_retries = 0, но это не отказ
    assert sim.state.preempt_count == {"low": 1} and sim.state.failures == {}


def test_preempted_task_waits_for_the_priority_of_its_preemptor(urgent):
    log = Simulator(urgent, Preempting({"urgent": "low"})).run_all()
    waits = [(r["t"], r["code"], r["detail"]) for r in records(log, "WAIT") if r["task"] == "low"]
    assert waits == [(3, "WAIT_PRIORITY", {"started": ["urgent"]})]


def test_preempted_attempt_is_checked_and_counted_as_lost_work(urgent):
    log = Simulator(urgent, Preempting({"urgent": "low"})).run_all()
    assert [(a.attempt, a.outcome, a.saved) for a in attempts_from_log(log) if a.task == "low"] == [
        (1, "PRE", 2),
        (2, "DONE", 0),
    ]
    assert verify(urgent, log, max_preemptions=1) == []
    assert metrics(urgent, log, None)["lost_work_gpu"] == 4  # час работы на 4 GPU после чекпоинта
    assert verify(urgent, log, max_preemptions=0) == ["C10 low: 1 preemptions, limit is 0"]
    not_preemptible = dataclasses.replace(
        urgent, tasks={**urgent.tasks, "low": dataclasses.replace(urgent.tasks["low"], preemptible=False)}
    )
    assert verify(not_preemptible, log) == ["C9 low#1: preempted, but the task is not preemptible"]


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (Decision("PREEMPT", "b", "n0"), "not RUNNING"),  # b ещё не стартовала
        (Decision("PREEMPT", "a", "n1"), "runs on n0"),
        (Decision("PREEMPT", "fixed", "n0"), "not preemptible"),
    ],
)
def test_invalid_preemptions_are_rejected(make_task, make_problem, decision, message):
    tasks = [
        make_task("a", 5, gpu=1, preemptible=True),
        make_task("fixed", 5, gpu=1),
        make_task("b", 1, gpu=1, release=1),
    ]
    with pytest.raises(InvalidDecision, match=message):
        Simulator(make_problem(tasks, gpus=(2, 2)), Fixed(decision)).run_all()
