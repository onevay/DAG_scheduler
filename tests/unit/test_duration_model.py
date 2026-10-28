"""Модель длительности: оценки задач без длительности, законы ускорения, скорость узлов в движке."""

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from expsched.contract.io_models import ProblemIn, ScalingIn, TaskIn, load
from expsched.estimate import Estimate, key, rescale, resolve, time_factor
from expsched.pipeline import run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def problem(tasks, workers=None, mode="emulate", **fields):
    return ProblemIn.model_validate(
        {
            "mode": mode,
            "cluster": {"workers": workers or [{"id": "w", "resources": {"cpu": 8}, "agent": "a"}]},
            "tasks": tasks,
            **fields,
        }
    )


def test_lognormal_mean_quantile_and_residual_life():
    e = Estimate(median=20, spread=0.5, source="estimate")
    assert e.mean == pytest.approx(20 * math.exp(0.125))
    assert e.quantile(0.5) == pytest.approx(20) and e.quantile(0.8) > e.mean > e.median
    # уже сделано больше ожидаемого — ждать ещё, но меньше, чем с нуля
    assert e.mean < e.residual(10) < e.residual(30)  # условие «уже больше 10» поднимает ожидание
    assert 30 < e.residual(30) < 60 and e.residual(60) > 60
    assert e.residual(60) - 60 < e.mean  # остаток меньше полной работы
    known = Estimate(10, 0, "estimate")
    assert (known.mean, known.quantile(0.8), known.residual(12), known.work()) == (10, 10, 12, 10)
    assert Estimate(10, 0.5, "kind").to_dict() == {
        "median": 10, "spread": 0.5, "mean": 11.331, "p80": 15.232, "work": 12, "source": "kind"
    }  # fmt: skip


def test_speedup_laws_carry_work_between_core_counts():
    amdahl = ScalingIn(law="amdahl", serial=0.2)
    assert time_factor(amdahl, "train", 1) == 1 and time_factor(amdahl, "train", 4) == pytest.approx(0.4)
    assert time_factor(ScalingIn(law="power", alpha=1), "train", 4) == pytest.approx(0.25)
    assert time_factor(None, "train", 2) == pytest.approx(0.55)  # умолчание вида: доля 0,1
    task = TaskIn(id="t", kind="train", duration=1, resources={"gpu": 4}, scaling=amdahl)
    assert rescale(100, task, n_seen=1) == pytest.approx(40)  # на 4 GPU — 0,4 от времени на одном
    assert key(task) == "task:t:train"


def test_unknown_durations_are_estimated_in_order_of_sources():
    p = problem(
        [
            {"id": "given", "kind": "data", "estimate": 25},
            {"id": "ranged", "kind": "train", "estimate": {"median": 50, "spread": 0.2}},
            {"id": "known", "kind": "eval", "duration": 8},
            {"id": "like_known", "kind": "eval"},
            {"id": "seen", "kind": "infer"},
            {"id": "blank", "kind": "other"},
        ],
        events=[{"time": 3, "type": "submit_task", "task": {"id": "late", "kind": "data"}}],
    )
    history = {"task:seen:infer": Estimate(33, 0.1, "history")}
    resolved, estimates = resolve(p, history)
    sources = {k: (e.source, round(e.median)) for k, e in estimates.items()}
    assert sources == {
        "given": ("estimate", 25), "ranged": ("estimate", 50), "like_known": ("similar", 8),
        "seen": ("history", 33), "blank": ("kind", 10), "late": ("kind", 10),
    }  # fmt: skip
    assert estimates["given"].spread == 0.3  # разброс по виду, если не задан
    durations = {t.id: t.duration for t in resolved.all_tasks()}
    assert durations["known"] == 8 and durations["ranged"] == estimates["ranged"].work() == 52
    assert all(d is not None for d in durations.values())
    assert resolve(problem([{"id": "k", "duration": 5}]))[1] == {}


def test_simulation_still_needs_every_duration():
    with pytest.raises(ValidationError, match="tasks without duration: a; give a duration"):
        problem([{"id": "a"}], mode="simulate")


def test_result_carries_estimates_for_tasks_without_duration():
    p = problem([{"id": "a", "estimate": 12}, {"id": "b", "duration": 4, "dependencies": ["a"]}])
    result = run(p.model_copy(update={"mode": "simulate"}), p.policy).result
    assert result["status"] == "OK" and result["estimates"]["a"]["source"] == "estimate"
    assert "b" not in result["estimates"] and result["metrics"]["makespan"] == 14 + 4  # среднее 13,6 → 14


def two_speeds(**task):
    return problem(
        [{"id": "t", "duration": 10, "resources": {"cpu": 4}, **task}],
        workers=[{"id": "fast", "resources": {"cpu": 4}, "speed": 2.0}],
        mode="simulate",
    )


def test_fast_worker_finishes_sooner_and_metrics_stay_consistent():
    result = run(two_speeds()).result
    [attempt] = result["attempts"]
    assert (attempt["start"], attempt["end"], attempt["outcome"]) == (0, 5, "DONE")
    assert result["violations"] == []  # I1: 10 единиц работы на скорости 2 — это 5 единиц времени
    m = result["metrics"]
    assert m["makespan"] == 5 and m["utilization"]["cpu"] == 1.0 and m["mean_wait"] == 0
    assert m["lower_bound"] <= m["makespan"]  # граница остаётся границей и на быстром узле


def test_interrupt_on_a_fast_worker_saves_work_at_its_speed():
    p = two_speeds(checkpoint_interval=4)
    p = p.model_copy(
        update={
            "events": ProblemIn.model_validate(
                {
                    "cluster": p.cluster.model_dump(),
                    "tasks": [],
                    "events": [
                        {"time": 3, "type": "worker_down", "worker": "fast"},
                        {"time": 4, "type": "worker_up", "worker": "fast"},
                    ],
                }  # fmt: skip
            ).events
        }
    )
    result = run(p).result
    first, second = result["attempts"]
    assert (first["end"], first["saved"]) == (3, 4)  # за 3 единицы времени — 6 работы, чекпоинт на 4
    assert (second["start"], second["resume_from"], second["end"]) == (4, 4, 7)  # 6 работы — 3 времени
    assert result["violations"] == [] and result["status"] == "OK"


def test_examples_before_the_speed_model_keep_the_reference_speed():
    for path in sorted(SCENARIOS.glob("*.json")):
        p = load(path.read_bytes())
        assert all(w.speed == 1 for w in p.cluster.workers) or path.stem == "ml_hetero", path.stem
    # их журналы сверяются с эталонами в их собственных тестах; разные скорости — только у примера ml_hetero


def test_run_on_agents_with_unknown_durations_goes_to_the_end(tmp_path):
    from test_emulation import Loop, laptop

    tasks = [t.model_copy(update={"duration": None, "estimate": t.duration}) for t in laptop().tasks]
    emulation = Loop(tmp_path, laptop(tasks=tasks)).run()
    assert emulation.status == "done" and emulation.result.result["status"] == "OK"
    assert set(emulation.result.result["estimates"]) == {t.id for t in tasks}
    assert {e.source for e in emulation.prepared.estimates.values()} == {"estimate"}
