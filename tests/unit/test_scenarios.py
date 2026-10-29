"""Эталонные сценарии из scenarios/: числа посчитаны вручную по демо-DAG и закреплены здесь."""

from pathlib import Path

import pytest

from expsched.contract.io_models import load
from expsched.pipeline import run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def outcome(name, policy="priority", **settings):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    return run(problem, problem.policy.model_copy(update={"name": policy, **settings}))


def spans(result):
    return {k: (t["start"], t["finish"]) for k, t in result["tasks"].items()}


def pretrain_attempts(result):
    return [
        (a["attempt"], a["worker"], a["start"], a["end"], a["outcome"], a["saved"])
        for a in result["attempts"]
        if a["task"] == "pretrain_vit"
    ]


def test_failure_of_node_a():
    """node-A лежит с 5 до 15: pretrain_vit теряет час после чекпоинта и продолжает на node-B."""
    out = outcome("failure")
    result = out.result
    assert result["status"] == "OK" and result["violations"] == []
    assert result["metrics"]["makespan"] == 22 and result["metrics"]["lost_work"]["gpu"] == 4  # 1 ч × 4 GPU
    assert pretrain_attempts(result) == [(1, "node-A", 2, 5, "INT", 2), (2, "node-B", 5, 11, "DONE", 0)]
    (interrupted,) = [r for r in out.events if r["type"] == "INTERRUPTED"]
    assert (interrupted["saved"], interrupted["lost"], interrupted["remaining"]) == (2, 1, 6)


def test_urgent_task_during_a_failure():
    out = outcome("urgent_failure")
    result = out.result
    assert result["status"] == "OK" and result["violations"] == []
    assert result["metrics"]["makespan"] == 22
    assert result["metrics"]["utilization"]["gpu"] == round(87 / 180, 4)  # node-A не считается, пока лежит
    urgent = result["tasks"]["urgent_eval"]
    assert (urgent["start"], urgent["finish"], urgent["tardiness"]) == (10, 13, 0)
    assert [(w["code"], w["from"], w["to"], w["detail"]) for w in urgent["waits"]] == [
        ("WAIT_DEPS", 9, 10, {"pending": ["eval_asr"]})
    ]
    starts = [(r["t"], r["task"], r["worker"]) for r in out.events if r["type"] == "START" and r["t"] >= 11]
    assert starts == [
        (11, "ft_det", "node-B"),
        (11, "ft_seg", "node-B"),
        (15, "eval_det", "node-B"),  # normal раньше low, хотя distill на критическом пути
        (15, "distill", "node-C"),
        (16, "eval_seg", "node-B"),
        (19, "infer_bench", "node-C"),
    ]


def test_backfill_keeps_the_reservation_of_the_large_task():
    """big ждёт весь узел до окончания run в 4; short (до 3) заполняет дыру, long (9 ч) ждёт big."""
    result = outcome("backfill", "priority_backfill").result
    assert spans(result) == {"run": (0, 4), "short": (1, 3), "big": (4, 6), "long": (6, 15)}
    assert spans(outcome("backfill").result)["big"] == (9, 11)  # priority занимает дыру длинной задачей


def test_urgent_task_preempts_the_background_one():
    result = outcome("preemption").result
    assert result["status"] == "OK" and result["violations"] == []
    assert spans(result) == {"background": (0, 13), "urgent": (3, 5)}
    assert result["metrics"]["lost_work"]["gpu"] == 2  # час после чекпоинта в 2, на 2 GPU
    late = outcome("preemption", preemption="never").result
    assert spans(late)["urgent"] == (10, 12) and late["metrics"]["deadline_misses"]["critical"] == 1


@pytest.mark.parametrize("name", ["demo", "failure", "urgent_failure", "backfill", "preemption"])
@pytest.mark.parametrize("policy", ["fifo", "fifo_skip", "priority", "priority_backfill"])
def test_every_policy_produces_a_valid_schedule(name, policy):
    result = outcome(name, policy).result
    assert result["status"] == "OK" and result["violations"] == []


def test_cycle_scenario_runs_the_rest_and_reports_the_errors():
    result = outcome("cycle").result
    assert result["status"] == "PARTIAL" and result["violations"] == []
    assert [d["code"] for d in result["diagnostics"]] == ["E010", "E012", "W060"]
