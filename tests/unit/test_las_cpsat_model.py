"""las (Tiresias 2D-LAS) и cpsat в модели длительности: скорости узлов, осторожная работа."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from expsched.contract.io_models import ProblemIn, load
from expsched.domain.models import Task
from expsched.domain.resources import Resources
from expsched.engine.snapshot import build_snapshot
from expsched.pipeline import prepare, run
from expsched.policies.las import LasPolicy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def snapshot(tasks, rem, t=0, ready_since=None):
    return SimpleNamespace(
        t=t,
        tasks={k: Task(k, duration=d, resources=Resources({"gpu": n})) for k, (d, n) in tasks.items()},
        rem=rem,
        ready=tuple(sorted(tasks)),
        ready_since=ready_since or dict.fromkeys(tasks, 0),
        free={"w": Resources({"gpu": 4})},
        eligible=dict.fromkeys(tasks, ("w",)),
        up={"w": True},
    )


def test_least_attained_service_goes_first_without_knowing_any_duration():
    # resumed уже получил 2 GPU × 30 = 60 обслуживания, fresh и big — ничего
    rem = {"resumed": 10, "fresh": 40, "big": 5}
    snap = snapshot({"resumed": (40, 2), "fresh": (40, 2), "big": (5, 4)}, rem)
    las = LasPolicy()
    assert [las.queue(snap, k) for k in ("fresh", "big", "resumed")] == [(0, 0), (0, 0), (1, 60)]
    # 4 GPU: первая очередь по id — big забирает всё
    assert [(d.task, d.reason["queue"]) for d in las.decide(snap)] == [("big", 0)]
    snap.free = {"w": Resources({"gpu": 6})}  # 6 GPU: big и fresh из первой очереди, resumed ждёт
    assert [d.task for d in las.decide(snap)] == ["big", "fresh"]


def test_long_wait_lifts_a_task_back_to_the_first_queue():
    snap = snapshot({"old": (200, 1)}, {"old": 50}, t=400, ready_since={"old": 0})
    assert LasPolicy(aging=None).queue(snap, "old") == (2, 150)
    assert LasPolicy(aging=360).queue(snap, "old") == (0, 150)


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.json")))
def test_las_schedules_every_example_without_violations(name):
    p = load((SCENARIOS / f"{name}.json").read_bytes())
    assert run(p, p.policy.model_copy(update={"name": "las"})).result["violations"] == []


cpsat = pytest.importorskip("ortools", reason="cpsat needs the optional ortools")


def test_cpsat_plans_with_node_speeds():
    p = load((SCENARIOS / "ml_hetero.json").read_bytes())
    results = {
        n: run(p, p.policy.model_copy(update={"name": n})).result
        for n in ("cpsat", "heft", "priority_backfill")
    }
    assert all(r["violations"] == [] for r in results.values())
    assert results["cpsat"]["metrics"]["makespan"] <= results["heft"]["metrics"]["makespan"]
    assert results["cpsat"]["metrics"]["makespan"] < results["priority_backfill"]["metrics"]["makespan"]


def test_robust_cpsat_plans_unknown_work_by_its_80th_percentile():
    from expsched.policies.cpsat.policy import plan_input

    p = ProblemIn.model_validate(
        {
            "mode": "emulate",
            "cluster": {
                "workers": [
                    {"id": "fast", "resources": {"gpu": 1}, "speed": 2, "agent": "a"},
                    {"id": "slow", "resources": {"gpu": 1}, "agent": "a"},
                ]
            },
            "tasks": [
                {
                    "id": "train",
                    "kind": "train",
                    "resources": {"gpu": 1},
                    "estimate": {"median": 20, "spread": 0.5},
                }
            ],
            "policy": {"name": "cpsat"},
        }  # fmt: skip
    )
    prepared = prepare(p)
    snap = build_snapshot(prepared.simulator().state, 0)
    mean, cautious = plan_input(snap).tasks["train"], plan_input(snap, robust=True).tasks["train"]
    assert (mean.duration, cautious.duration) == (23, 31)  # среднее 22,7 → 23; квантиль 0,8 → 31
    assert (mean.time("fast"), mean.time("slow"), cautious.time("fast")) == (12, 23, 16)
