from pathlib import Path

from expsched.contract.hashing import canonical_json
from expsched.contract.io_models import PolicyIn, ProblemIn, load
from expsched.pipeline import run

DEMO = Path(__file__).resolve().parents[2] / "scenarios" / "demo.json"


def problem(tasks, mode="lenient"):
    return ProblemIn.model_validate(
        {
            "cluster": {"workers": [{"id": "n", "resources": {"gpu": 4}}]},
            "tasks": tasks,
            "policy": {"name": "fifo", "validation_mode": mode},
        }
    )


def test_demo_scenario_runs_cleanly():
    result = run(load(DEMO.read_bytes())).result
    assert (result["status"], result["policy"]) == ("OK", "priority")
    assert result["metrics"]["makespan"] == result["metrics"]["lower_bound"] == 21
    assert result["violations"] == [] and result["diagnostics"] == []
    urgent = result["tasks"]["urgent_eval"]
    assert urgent["finish"] <= urgent["deadline"] and urgent["tardiness"] == 0
    assert len(result["attempts"]) == 11
    canonical_json(result)  # результат сериализуется без ошибок


def test_runs_of_the_same_input_are_identical():
    first, second = run(load(DEMO.read_bytes())), run(load(DEMO.read_bytes()))
    assert first.result == second.result and first.events == second.events
    assert first.result["result_hash"].startswith("sha256:")


def test_policy_override_changes_the_run_and_its_input_hash():
    demo = load(DEMO.read_bytes())
    default, fifo = run(demo).result, run(demo, PolicyIn(name="fifo")).result
    assert fifo["policy"] == "fifo"
    assert fifo["input_hash"] != default["input_hash"]


def test_lenient_mode_runs_the_rest_and_reports_partial():
    tasks = [
        {"id": "a", "duration": 1, "dependencies": ["b"]},
        {"id": "b", "duration": 1, "dependencies": ["a"]},
        {"id": "after", "duration": 1, "dependencies": ["a"]},
        {"id": "fine", "duration": 2},
    ]
    result = run(problem(tasks)).result
    assert result["status"] == "PARTIAL"
    assert {k: v["status"] for k, v in result["tasks"].items()} == {
        "a": "INFEASIBLE",
        "b": "INFEASIBLE",
        "after": "BLOCKED",
        "fine": "SUCCEEDED",
    }
    assert [d["code"] for d in result["diagnostics"]] == ["E012"]
    assert result["metrics"]["makespan"] == 2 and result["violations"] == []


def test_strict_mode_with_errors_does_not_run():
    out = run(problem([{"id": "a", "duration": 1, "dependencies": ["ghost"]}], mode="strict"))
    assert out.result["status"] == "INFEASIBLE" and out.events == []
    assert out.result["attempts"] == [] and out.result["metrics"] is None
    assert [d["code"] for d in out.result["diagnostics"]] == ["E010"]


def test_task_summary_shows_waits():
    tasks = [
        {"id": "a", "duration": 3, "resources": {"gpu": 4}},
        {"id": "b", "duration": 1, "resources": {"gpu": 4}},
    ]
    summary = run(problem(tasks)).result["tasks"]["b"]
    assert (summary["start"], summary["finish"]) == (3, 4)
    assert [(w["code"], w["from"], w["to"]) for w in summary["waits"]] == [("WAIT_PRIORITY", 0, 3)]
