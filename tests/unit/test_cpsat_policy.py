"""Политика cpsat: прогоны примеров, перепланирование, откат на priority_backfill, реестр."""

import time
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from expsched.contract.io_models import PolicyIn, load, to_domain  # noqa: E402
from expsched.domain.models import Event, EvType, Problem, Task, Worker  # noqa: E402
from expsched.domain.resources import Resources  # noqa: E402
from expsched.engine.simulator import Simulator  # noqa: E402
from expsched.pipeline import run  # noqa: E402
from expsched.policies.cpsat import policy as cpsat  # noqa: E402
from expsched.policies.preemption import PreemptivePolicy  # noqa: E402
from expsched.policies.registry import make_policy  # noqa: E402
from expsched.verify.invariants import verify  # noqa: E402

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def outcome(name, **settings):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    return run(problem, problem.policy.model_copy(update={"name": "cpsat", **settings}))


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.json")))
def test_every_scenario_gets_a_valid_schedule_and_the_same_status(name):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    heuristic = run(problem, problem.policy.model_copy(update={"name": "priority_backfill"})).result
    result = outcome(name).result
    assert result["violations"] == [] and result["status"] == heuristic["status"]


@pytest.mark.parametrize("name", ["fragmentation", "hetero_gpus", "failure"])
def test_plan_reaches_the_lower_bound_where_heuristics_do_not(name):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    heuristic = run(problem, problem.policy.model_copy(update={"name": "priority_backfill"})).result[
        "metrics"
    ]
    metrics = outcome(name).result["metrics"]
    assert metrics["makespan"] == metrics["lower_bound"] < heuristic["makespan"]


def test_starts_follow_the_plan_and_say_so():
    events = outcome("fragmentation").events
    starts = [r for r in events if r["type"] == "START"]
    assert all(r["reason"]["code"] == "PLAN" and r["reason"]["planned"] == r["t"] for r in starts)


def test_same_input_gives_the_same_journal():
    assert outcome("ml_platform").result["result_hash"] == outcome("ml_platform").result["result_hash"]


def test_submitted_task_and_failures_make_it_replan(monkeypatch):
    solved = []
    real = cpsat.solve
    monkeypatch.setattr(cpsat, "solve", lambda plan, *a: solved.append(plan.now) or real(plan, *a))
    result = outcome("urgent_failure").result
    # старт, отказ, подача задачи, возврат узла; копии симулятора для прогноза перепланирования решают сами
    assert result["violations"] == [] and sorted(set(solved)) == [0, 5, 10, 15]


def test_preempted_task_is_planned_again():
    result = outcome("preemption").result
    assert result["status"] == "OK" and result["tasks"]["background"]["finish"] == 13


def test_too_many_tasks_fall_back_to_priority_backfill(monkeypatch):
    monkeypatch.setattr(cpsat, "MAX_TASKS", 1)
    starts = [r for r in outcome("fragmentation").events if r["type"] == "START"]
    assert starts and all(r["reason"]["code"] == "TOP_OF_QUEUE" for r in starts)


def test_weighted_objective_also_plans():
    result = outcome("deadline_crunch", objective="weighted").result
    assert result["violations"] == [] and result["metrics"]["score"]["objective"] == "weighted"


def test_registry_builds_cpsat_with_the_input_seed():
    policy = make_policy(PolicyIn(name="cpsat", objective="weighted", time_limit=3), seed=7)
    assert isinstance(policy, PreemptivePolicy) and isinstance(policy.base, cpsat.CpSatPolicy)
    assert (policy.base.objective, policy.base.time_limit, policy.base.seed) == ("weighted", 3, 7)
    assert isinstance(make_policy(PolicyIn(name="cpsat", preemption="never")), cpsat.CpSatPolicy)


def test_task_without_a_working_node_waits_without_replanning_every_epoch(monkeypatch):
    """Узел b, единственный для задачи y, лежит: y ждёт, а план не пересчитывается на каждом окончании."""
    solved = []
    real = cpsat.solve
    monkeypatch.setattr(cpsat, "solve", lambda plan, *a: solved.append(plan.now) or real(plan, *a))
    tasks = {
        "x1": Task("x1", 2, Resources({"gpu": 1})),
        "x2": Task("x2", 2, Resources({"gpu": 1}), deps=("x1",)),
        "y": Task("y", 1, Resources({"gpu": 1}), constraints=(("zone", "b"),)),
    }
    workers = {
        "a": Worker("a", Resources({"gpu": 1})),
        "b": Worker("b", Resources({"gpu": 1}), (("zone", "b"),)),
    }
    events = [Event(0, EvType.WORKER_DOWN, worker="b"), Event(5, EvType.WORKER_UP, worker="b")]
    problem = Problem(tasks, workers, events)
    log = Simulator(problem, cpsat.CpSatPolicy()).run_all()
    assert verify(problem, log) == [] and solved == [0, 5]


@pytest.mark.slow
def test_hundred_tasks_plan_in_seconds():
    problem = to_domain(load((SCENARIOS / "ml_platform.json").read_bytes()))
    tasks = {}
    for copy in range(3):  # 3 копии платформы: 117 задач
        for key, task in problem.tasks.items():
            deps = tuple(f"{d}_{copy}" for d in task.deps)
            tasks[f"{key}_{copy}"] = Task(f"{key}_{copy}", task.duration, task.resources, deps=deps)
    big = Problem(tasks, problem.workers, [])
    started = time.perf_counter()
    log = Simulator(big, cpsat.CpSatPolicy()).run_all()
    assert verify(big, log) == [] and time.perf_counter() - started < 20
