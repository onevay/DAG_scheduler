"""HEFT и PEFT: ранги вверх, узел по раннему окончанию с учётом скорости, резерв занятого лучшего узла."""

from pathlib import Path

import pytest

from expsched.contract.io_models import ProblemIn, load
from expsched.pipeline import run
from expsched.policies.registry import implemented_policies

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def problem(tasks, workers, policy="heft"):
    return ProblemIn.model_validate(
        {"cluster": {"workers": workers}, "tasks": tasks, "policy": {"name": policy}}
    )


def placed(result):
    return {a["task"]: (a["worker"], a["start"], a["end"]) for a in result["attempts"]}


def test_policies_are_registered():
    assert {"heft", "peft"} <= set(implemented_policies())


def test_task_goes_to_the_node_where_it_finishes_first():
    workers = [{"id": "slow", "resources": {"gpu": 1}}, {"id": "fast", "resources": {"gpu": 1}, "speed": 3}]
    result = run(problem([{"id": "train", "duration": 12, "resources": {"gpu": 1}}], workers)).result
    assert placed(result) == {"train": ("fast", 0, 4)}
    backfill = run(
        problem([{"id": "train", "duration": 12, "resources": {"gpu": 1}}], workers, "priority_backfill")
    )
    assert (
        placed(backfill.result)["train"][0] == "fast"
    )  # равные узлы по ресурсам: выбирает по id (fast < slow)


def test_waiting_for_a_fast_node_beats_starting_on_a_slow_one_and_is_reserved():
    workers = [
        {"id": "a-fast", "resources": {"gpu": 1}, "speed": 4},
        {"id": "b-slow", "resources": {"gpu": 1}},
    ]
    tasks = [
        {"id": "first", "duration": 8, "resources": {"gpu": 1}},  # на быстром — 2
        {"id": "second", "duration": 8, "resources": {"gpu": 1}},  # подождать быстрый: 2 + 2 < 8 на медленном
    ]
    result = run(problem(tasks, workers)).result
    assert placed(result) == {"first": ("a-fast", 0, 2), "second": ("a-fast", 2, 4)}
    reserve = [r for r in run(problem(tasks, workers)).events if r["type"] == "RESERVE"]
    assert [(r["task"], r["worker"], r["t_res"]) for r in reserve] == [("second", "a-fast", 2)]
    assert result["metrics"]["makespan"] == 4 and result["violations"] == []


def test_upward_rank_puts_the_long_chain_first():
    workers = [{"id": "w", "resources": {"cpu": 1}}]
    tasks = [
        {"id": "short", "duration": 3, "resources": {"cpu": 1}},
        {"id": "head", "duration": 2, "resources": {"cpu": 1}},
        {"id": "tail", "duration": 6, "resources": {"cpu": 1}, "dependencies": ["head"]},
    ]
    events = run(problem(tasks, workers)).events
    first = next(r for r in events if r["type"] == "START")
    assert (first["task"], first["reason"]) == ("head", {"code": "HEFT", "rank": 8.0, "eft": 2})


def test_peft_looks_ahead_with_its_optimistic_cost_table():
    workers = [{"id": "w", "resources": {"cpu": 1}}]
    tasks = [
        {"id": "a", "duration": 2, "resources": {"cpu": 1}},
        {"id": "b", "duration": 5, "resources": {"cpu": 1}, "dependencies": ["a"]},
    ]
    first = next(r for r in run(problem(tasks, workers, "peft")).events if r["type"] == "START")
    assert first["reason"] == {
        "code": "PEFT",
        "rank": 5.0,
        "eft": 2,
    }  # OCT(a) — длина b, свой вклад не входит


def test_heterogeneous_example_finishes_much_sooner_than_with_speed_blind_policies():
    p = load((SCENARIOS / "ml_hetero.json").read_bytes())
    makespan = {}
    for name in ("priority_backfill", "heft", "peft"):
        outcome = run(p, p.policy.model_copy(update={"name": name}))
        assert outcome.result["status"] == "OK" and outcome.result["violations"] == []
        makespan[name] = outcome.result["metrics"]["makespan"]
    assert makespan["heft"] <= makespan["peft"] < makespan["priority_backfill"]
    lower = outcome.result["metrics"]["lower_bound"]
    assert lower <= makespan["heft"] < 1.2 * lower  # до границы — меньше 20 %
    again = run(p).result
    assert again["result_hash"] == run(p).result["result_hash"]  # детерминированно


@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.json")))
@pytest.mark.parametrize("policy", ["heft", "peft"])
def test_every_example_is_scheduled_without_violations(name, policy):
    p = load((SCENARIOS / f"{name}.json").read_bytes())
    result = run(p, p.policy.model_copy(update={"name": policy})).result
    assert result["violations"] == []
