import dataclasses
from pathlib import Path

import pytest

from expsched.contract.io_models import load
from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.explain.chain import critical_chain, decompose
from expsched.metrics.metrics import metrics
from expsched.pipeline import run
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.preemption import PreemptivePolicy
from expsched.policies.priority import PriorityPolicy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def explain(name):
    return run(load((SCENARIOS / f"{name}.json").read_bytes())).result["explain"]


def links(chain):
    return [(c["task"], c["attempt"], c["cause"], c.get("by")) for c in chain]


def test_critical_chain_decomposition():
    """failure: весь зазор до нижней границы 21 — час работы pretrain_vit, потерянный при отказе node-A."""
    e = explain("failure")
    assert links(e["chain"]) == [
        ("prep_data", 1, "origin", None),
        ("pretrain_vit", 1, "dep", "prep_data"),
        ("pretrain_vit", 2, "fail", "pretrain_vit"),
        ("ft_det", 1, "dep", "pretrain_vit"),
        ("distill", 1, "dep", "ft_det"),
        ("infer_bench", 1, "dep", "distill"),
    ]
    assert e["decomposition"] == {"dep": 21, "res": 0, "lost": 1, "release": 0, "wait": 0}
    interrupted = e["chain"][1]
    assert (interrupted["start"], interrupted["end"], interrupted["work"], interrupted["lost"]) == (
        2,
        5,
        2,
        1,
    )


def test_chain_through_freed_resources():
    e = explain("backfill")  # big ждёт, пока run освободит узел; long — пока освободит big
    assert links(e["chain"]) == [
        ("run", 1, "origin", None),
        ("big", 1, "res", "run"),
        ("long", 1, "res", "big"),
    ]
    assert e["decomposition"] == {"dep": 4, "res": 11, "lost": 0, "release": 0, "wait": 0}


def test_chain_through_a_preemption():
    e = explain("preemption")
    assert links(e["chain"]) == [
        ("background", 1, "origin", None),
        ("urgent", 1, "res", "background"),  # место освободило вытеснение
        ("background", 2, "res", "urgent"),
    ]
    assert e["decomposition"]["lost"] == 1 and sum(e["decomposition"].values()) == 13


def test_chain_that_starts_at_a_release(make_task, make_problem):
    tasks = [make_task("late", 3, release=5)]
    log = Simulator(make_problem(tasks), PriorityPolicy()).run_all()
    chain = critical_chain({t.id: t for t in tasks}, log)
    assert links(chain) == [("late", 1, "rel", None)]
    assert decompose(chain) == {"dep": 3, "res": 0, "lost": 0, "release": 5, "wait": 0}


def test_chain_that_starts_when_a_worker_returns(make_task, make_problem):
    tasks = [make_task("a", 2)]
    events = [Event(1, EvType.WORKER_DOWN, worker="n0"), Event(3, EvType.WORKER_UP, worker="n0")]
    log = Simulator(make_problem(tasks, events=events), PriorityPolicy()).run_all()
    chain = critical_chain({"a": tasks[0]}, log)
    assert links(chain) == [("a", 2, "up", None)]  # прерванная попытка кончилась в 1, а не в 3
    assert decompose(chain) == {"dep": 2, "res": 0, "lost": 0, "release": 0, "wait": 3}


def test_no_finished_attempts_give_an_empty_chain():
    assert critical_chain({}, []) == [] and decompose([]) == {
        "dep": 0,
        "res": 0,
        "lost": 0,
        "release": 0,
        "wait": 0,
    }


@pytest.mark.parametrize("seed", range(30))
def test_decomposition_sums_to_makespan(random_problem, seed):
    problem = random_problem(seed, failures=True)
    problem = dataclasses.replace(
        problem, tasks={k: dataclasses.replace(t, preemptible=True) for k, t in problem.tasks.items()}
    )
    log = Simulator(problem, PreemptivePolicy(BackfillPolicy(), min_runtime=0)).run_all()
    chain = critical_chain(problem.tasks, log)
    makespan = metrics(problem, log, None)["makespan"]
    assert sum(decompose(chain).values()) == makespan
    assert all(a["end"] == b["start"] for a, b in zip(chain, chain[1:], strict=False))  # звенья без зазоров
    assert not chain or chain[-1]["end"] == makespan
