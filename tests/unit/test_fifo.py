import dataclasses

import pytest

from expsched.analysis import analyze
from expsched.contract.io_models import PolicyIn
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator
from expsched.policies import registry
from expsched.policies.fifo import FifoPolicy
from expsched.policies.registry import make_policy


def starts(log):
    return {r["task"]: r["t"] for r in log if r["type"] == "START"}


def without_urgent(demo):
    return dataclasses.replace(demo, tasks={k: t for k, t in demo.tasks.items() if k != "urgent_eval"})


def test_strict_fifo_holds_the_queue_behind_a_task_that_does_not_fit(make_task, make_problem):
    # a занимает 2 GPU из 4; b просит 4 и ждёт до t = 5; c (1 GPU) поместилась бы сразу, но строгий FIFO
    # держит её за b, а в t = 5 все 4 GPU занимает b
    problem = make_problem([make_task("a", 5, gpu=2), make_task("b", 1, gpu=4), make_task("c", 1, gpu=1)])
    assert starts(Simulator(problem, FifoPolicy()).run_all()) == {"a": 0, "b": 5, "c": 6}


def test_fifo_skip_lets_smaller_tasks_pass(make_task, make_problem):
    problem = make_problem([make_task("a", 5, gpu=2), make_task("b", 1, gpu=4), make_task("c", 1, gpu=1)])
    assert starts(Simulator(problem, FifoPolicy(skip=True)).run_all()) == {"a": 0, "c": 0, "b": 5}


def test_queue_order_is_the_moment_of_readiness_not_the_id(make_task, make_problem):
    # на узле 1 GPU; b готова в t = 1, a — в t = 2, обе ждут z до t = 3; первой идёт b
    tasks = [make_task("z", 3), make_task("b", 1, release=1), make_task("a", 1, release=2)]
    log = Simulator(make_problem(tasks, gpus=(1,)), FifoPolicy()).run_all()
    assert starts(log) == {"z": 0, "b": 3, "a": 4}


def test_start_reason_names_the_rule(make_task, make_problem):
    problem = make_problem([make_task("a")])
    for policy, code in ((FifoPolicy(), "FIFO_HEAD"), (FifoPolicy(skip=True), "FIFO_FIRST_FIT")):
        (start,) = [r for r in Simulator(problem, policy).run_all() if r["type"] == "START"]
        assert start["reason"] == {"code": code}


@pytest.mark.parametrize("skip", [False, True])
def test_demo_reaches_the_lower_bound(demo_problem, check_capacity, skip):
    problem = without_urgent(demo_problem)
    sim = Simulator(problem, FifoPolicy(skip))
    log = sim.run_all()
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
    check_capacity(problem, log)
    assert max(r["t"] for r in log if r["type"] == "FINISH") == analyze(problem).lb == 21


def test_registry_builds_policies_by_name():
    assert make_policy(PolicyIn(name="fifo", preemption="never")).name == "fifo"
    assert make_policy(PolicyIn(name="fifo_skip", preemption="never")).name == "fifo_skip"


def test_registry_reports_policies_that_are_not_implemented(monkeypatch):
    monkeypatch.delitem(registry._FACTORIES, "cpsat", raising=False)  # как без ortools
    with pytest.raises(NotImplementedError, match="cpsat"):
        make_policy(PolicyIn(name="cpsat"))
