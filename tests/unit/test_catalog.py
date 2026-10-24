import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from expsched.analysis import analyze, critical_path
from expsched.api.app import create_app
from expsched.api.store import RunStore

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(create_app(store=RunStore(tmp_path_factory.mktemp("runs")))) as client:
        yield client


def test_scenarios_are_listed_with_their_size(client):
    listed = {s["name"]: s for s in client.get("/v1/scenarios").json()["scenarios"]}
    assert set(listed) == {p.stem for p in SCENARIOS.glob("*.json")}
    assert listed["demo"] == {
        "name": "demo",
        "tasks": 11,
        "workers": 3,
        "events": 1,
        "policy": "priority",
        "time_unit": "hour",
    }


def test_scenario_is_returned_with_its_name(client):
    body = client.get("/v1/scenarios/failure").json()
    assert body["name"] == "failure" and len(body["tasks"]) == 10


@pytest.mark.parametrize(
    ("name", "statuses"), [("ghost", {404}), ("..%2Fpyproject", {404, 422}), ("Demo", {422})]
)
def test_unknown_or_unsafe_scenario_names(client, name, statuses):
    response = client.get(f"/v1/scenarios/{name}")
    assert response.status_code in statuses and "error" in response.json()  # до файлов вне каталога не дойти


def test_broken_scenario_files_are_skipped(tmp_path):
    (tmp_path / "good.json").write_text(
        (SCENARIOS / "demo.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "bad.json").write_text("{}", encoding="utf-8")
    with TestClient(create_app(store=RunStore(tmp_path / "runs"), scenarios_dir=tmp_path)) as client:
        assert [s["name"] for s in client.get("/v1/scenarios").json()["scenarios"]] == ["good"]


def test_runs_are_listed_newest_first(client):
    names = ["backfill", "preemption"]
    ids = [
        client.post("/v1/runs", json={"problem": client.get(f"/v1/scenarios/{n}").json()}).json()["run_id"]
        for n in names
    ]
    runs = client.get("/v1/runs").json()["runs"]
    assert [r["run_id"] for r in runs[:2]] == ids[::-1]
    newest = runs[0]
    assert (newest["name"], newest["policy"], newest["status"], newest["tasks"], newest["makespan"]) == (
        "preemption",
        "priority",
        "OK",
        2,
        13,
    )
    assert len(client.get("/v1/runs", params={"limit": 1}).json()["runs"]) == 1


def test_validation_analysis_includes_submitted_tasks(client):
    demo = json.loads((SCENARIOS / "demo.json").read_text(encoding="utf-8"))
    late = {**demo, "events": [{**demo["events"][0], "time": 30}]}  # urgent_eval подаётся в 30
    analysis = client.post("/v1/validate", json=late).json()["analysis"]
    assert analysis == {"lower_bound": 33, "critical_path": ["urgent_eval"]}


def test_validation_analysis_skips_tasks_with_errors(client):
    cycle = json.loads((SCENARIOS / "cycle.json").read_text(encoding="utf-8"))
    lenient = client.post("/v1/validate", json=cycle).json()
    assert lenient["valid"] is False and lenient["analysis"] is not None  # цикл убран, остальное планируется
    assert client.post("/v1/validate?mode=strict", json=cycle).json()["analysis"] is None


def test_critical_path_follows_the_dependency_that_set_each_start(make_task, make_problem):
    tasks = [make_task("a", 2), make_task("b", 5), make_task("c", 1, "a", "b"), make_task("d", 1, "a")]
    problem = make_problem(tasks)
    assert critical_path(problem, analyze(problem)) == ["b", "c"]
    assert critical_path(make_problem([]), analyze(make_problem([]))) == []
