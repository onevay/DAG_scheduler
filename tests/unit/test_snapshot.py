import dataclasses

import pytest

from expsched.analysis import analyze
from expsched.domain.analysis import Analysis
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, RunInfo, Snapshot


def empty_snapshot(demo_problem) -> Snapshot:
    return Snapshot(
        t=0,
        tasks=demo_problem.tasks,
        ready=("prep_data",),
        running={},
        rem={k: t.duration for k, t in demo_problem.tasks.items()},
        free={w: demo_problem.workers[w].capacity for w in demo_problem.workers},
        up={w: True for w in demo_problem.workers},
        eligible={k: demo_problem.eligible(t) for k, t in demo_problem.tasks.items()},
        analysis=analyze(demo_problem),
        ready_since={"prep_data": 0},
        preempt_count={},
    )


def test_analyze_returns_the_domain_analysis_type(demo_problem):
    assert isinstance(analyze(demo_problem), Analysis)


def test_contract_objects_are_immutable(demo_problem):
    snapshot = empty_snapshot(demo_problem)
    decision = Decision("START", "prep_data", "node-C", {"code": "TEST"})
    for obj, field in [(snapshot, "t"), (decision, "worker"), (RunInfo("node-C", 0, 1, 2), "start")]:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, 1)


def test_decision_reason_defaults_to_an_independent_dict():
    a, b = Decision("START", "x", "w"), Decision("START", "y", "w")
    a.reason["code"] = "X"
    assert b.reason == {}


def test_policy_protocol_is_satisfied_by_a_plain_class(demo_problem):
    class FirstFit:
        name = "first-fit"

        def decide(self, snapshot: Snapshot) -> list[Decision]:
            task = snapshot.tasks[snapshot.ready[0]]
            worker = next(w for w in snapshot.free if task.resources.fits(snapshot.free[w]))
            return [Decision("START", task.id, worker, {"code": "FIRST_FIT"})]

    policy: Policy = FirstFit()
    (decision,) = policy.decide(empty_snapshot(demo_problem))
    assert (decision.action, decision.task, decision.worker) == ("START", "prep_data", "node-A")
    assert empty_snapshot(demo_problem).free["node-C"] == Resources({"gpu": 2})
