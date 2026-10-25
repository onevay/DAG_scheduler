"""Сценарии на отдельные случаи: разнородный кластер, фрагментация, развилка, дедлайны, каскад отказов, поток
срочных задач, данные по расписанию, ошибки входа и крупная платформа. Каждый тест закрепляет то, ради чего
сценарий собран."""

from pathlib import Path

import pytest

from expsched.contract.io_models import load
from expsched.pipeline import run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
POLICIES = ["fifo", "fifo_skip", "priority", "priority_backfill"]


def outcome(name, policy=None, **settings):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    return run(problem, problem.policy.model_copy(update={"name": policy or problem.policy.name, **settings}))


def idle_codes(result):
    return {code for code, value in result["explain"]["idle"]["by_code"].items() if value}


def statuses(result):
    return {k: t["status"] for k, t in result["tasks"].items()}


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("hetero_gpus", "OK"),
        ("fragmentation", "OK"),
        ("sweep", "OK"),
        ("deadline_crunch", "OK"),
        ("cascade", "PARTIAL"),
        ("rush_hour", "OK"),
        ("staggered", "OK"),
        ("invalid_mix", "PARTIAL"),
        ("ml_platform", "OK"),
    ],
)
@pytest.mark.parametrize("policy", POLICIES)
def test_every_policy_produces_a_valid_schedule(name, status, policy):
    result = outcome(name, policy).result
    assert result["status"] == status and result["violations"] == []


def test_constraints_and_resources_route_tasks_to_their_nodes():
    """Метки и ресурсы помимо GPU: данные — на CPU-узел, большая модель — на A100, зона us — на свой V100."""
    result = outcome("hetero_gpus").result
    placed = {a["task"]: a["worker"] for a in result["attempts"]}
    assert placed["ingest"] == "cpu-1"  # 600 ГБ памяти есть только у него
    assert placed["pretrain_llm"] == "a100-1"
    assert placed["finetune_code"] == "v100-us"
    assert placed["distill_small"].startswith("v100") and placed["serve_bench"].startswith("v100")
    assert {task for task, worker in placed.items() if worker == "cpu-1"} == {"ingest", "metrics_export"}


def test_each_policy_leaves_its_own_kind_of_idle_time():
    """FIFO стоит за широкой задачей в голове очереди, жадные политики дробят место, backfill — резерв."""
    by_policy = {p: outcome("fragmentation", p).result for p in POLICIES}
    assert "IDLE_POLICY" in idle_codes(by_policy["fifo"])
    assert "IDLE_FRAGMENTATION" in idle_codes(by_policy["priority"])
    assert "IDLE_RESERVED" in idle_codes(by_policy["priority_backfill"])
    starts = {p: r["tasks"]["wide_a"]["start"] for p, r in by_policy.items()}
    assert starts == {"fifo": 5, "fifo_skip": 6, "priority": 6, "priority_backfill": 3}


def test_fan_in_waits_for_the_slowest_trial():
    result = outcome("sweep").result
    trials = [t for k, t in result["tasks"].items() if k.startswith("trial_")]
    assert len(trials) == 12
    assert result["tasks"]["select_best"]["start"] == max(t["finish"] for t in trials)
    assert result["tasks"]["final_train"]["start"] == result["tasks"]["select_best"]["finish"]


def test_priority_preempts_the_nightly_job_to_save_the_demo():
    """Критическая демо-модель успевает только ценой вытеснения фоновой задачи; FIFO её опаздывает."""
    fifo = outcome("deadline_crunch", "fifo").result
    out = outcome("deadline_crunch")
    assert fifo["metrics"]["deadline_misses"]["critical"] == 2
    assert out.result["metrics"]["deadline_misses"]["critical"] == 1  # остался только недостижимый отчёт
    assert out.result["tasks"]["demo_model"]["tardiness"] == 0
    (preempted,) = [r for r in out.events if r["type"] == "INTERRUPTED"]
    assert (preempted["task"], preempted["cause"], preempted["reason"]["by"]) == (
        "nightly_retrain",
        "PREEMPT",
        "demo_model",
    )
    assert [d["code"] for d in out.result["diagnostics"]] == ["E030"]


def test_cascade_of_failures():
    """Два отказа подряд у train_main, хрупкая задача без ретраев падает, узел B не возвращается."""
    out = outcome("cascade")
    result = out.result
    assert statuses(result) == {
        "prep": "SUCCEEDED",
        "train_main": "SUCCEEDED",
        "train_aux": "SUCCEEDED",
        "fragile_train": "FAILED",
        "fragile_report": "BLOCKED",
        "eval_main": "SUCCEEDED",
        "eval_aux": "SUCCEEDED",
        "rack_b_smoke": "READY",
    }
    attempts = [a for a in result["attempts"] if a["task"] == "train_main"]
    main = [(a["worker"], a["resume_from"], a["outcome"]) for a in attempts]
    assert main[:2] == [("node-A", 0, "INT"), ("node-C", 2, "INT")] and main[2][2] == "DONE"
    (stuck,) = [r for r in out.events if r["type"] == "DIAGNOSTIC"]
    assert (stuck["task"], stuck["code"], stuck["detail"]["eligible"]) == ("rack_b_smoke", "E050", ["node-B"])
    assert result["metrics"]["lost_work_gpu"] == 12  # по 3 ч у двух задач на 2 GPU без чекпоинтов


def test_urgent_tasks_preempt_background_work_once():
    fifo = outcome("rush_hour", "fifo").result
    result = outcome("rush_hour").result
    assert fifo["metrics"]["deadline_misses"]["critical"] == 2
    assert result["metrics"]["deadline_misses"] == {"critical": 0, "high": 0, "normal": 0, "low": 0}
    preempted = [a["task"] for a in result["attempts"] if a["outcome"] == "PRE"]
    assert preempted == ["bg_pretrain"]  # max_preemptions_per_task = 1: дальше фон не трогают


def test_data_arriving_later_shows_up_in_the_explanation():
    result = outcome("staggered").result
    chain = result["explain"]["chain"]
    assert (chain[0]["task"], chain[0]["cause"], chain[0]["start"]) == ("batch_wed", "rel", 8)
    assert result["explain"]["decomposition"]["release"] == 8
    report = result["tasks"]["weekly_report"]
    assert (report["finish"], report["tardiness"]) == (22, 0)
    assert result["metrics"]["makespan"] == result["metrics"]["lower_bound"] == 22


def test_invalid_parts_are_skipped_and_the_rest_runs():
    result = outcome("invalid_mix").result
    assert [d["code"] for d in result["diagnostics"]] == ["E020", "E021", "E040", "E030"]
    assert statuses(result) == {
        "prep": "SUCCEEDED",
        "train": "SUCCEEDED",
        "eval": "SUCCEEDED",
        "too_big": "INFEASIBLE",
        "after_too_big": "BLOCKED",
        "wants_h100": "INFEASIBLE",
        "rushed_report": "SUCCEEDED",
    }
    assert result["tasks"]["rushed_report"]["tardiness"] > 0


def test_failure_on_the_large_platform_costs_the_executive_demo():
    """Отказ a100-2 прерывает vision_pretrain, поданная на 14 демо ждёт его и опаздывает на 6 ч."""
    result = outcome("ml_platform").result
    assert len(result["tasks"]) == 41 and set(statuses(result).values()) == {"SUCCEEDED"}
    interrupted = {(a["task"], a["worker"]) for a in result["attempts"] if a["outcome"] == "INT"}
    assert interrupted == {("vision_pretrain", "a100-2"), ("nlp_ft1", "v100-1")}
    demo = result["tasks"]["exec_demo"]
    assert (demo["start"], demo["tardiness"]) == (24, 6)
    assert result["tasks"]["quarterly_report"]["tardiness"] == 0
