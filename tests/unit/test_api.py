import json
from pathlib import Path

from fastapi.testclient import TestClient

from expsched.api.app import app

DEMO = json.loads(
    (Path(__file__).resolve().parents[2] / "scenarios" / "demo.json").read_text(encoding="utf-8")
)
client = TestClient(app)


def with_tasks(tasks):
    return {**DEMO, "tasks": tasks, "events": []}


def test_policies_lists_implemented_ones_and_defaults():
    body = client.get("/v1/policies").json()
    assert body["policies"] == ["fifo", "fifo_skip", "priority", "priority_backfill"]
    assert body["defaults"]["validation_mode"] == "lenient"


# ---- проверка входа ----


def test_validate_accepts_the_demo():
    response = client.post("/v1/validate", json=DEMO)
    assert response.status_code == 200 and response.json() == {"valid": True, "diagnostics": []}


def test_validate_reports_graph_errors_with_200_in_both_modes():
    cycle = with_tasks(
        [{"id": "a", "duration": 1, "dependencies": ["b"]}, {"id": "b", "duration": 1, "dependencies": ["a"]}]
    )
    for mode in ("lenient", "strict"):
        response = client.post(f"/v1/validate?mode={mode}", json=cycle)
        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert [d["code"] for d in response.json()["diagnostics"]] == ["E012"]


def test_validate_rejects_a_schema_violation_with_422():
    response = client.post("/v1/validate", json={**DEMO, "unexpected": 1})
    assert response.status_code == 422
    assert [d["code"] for d in response.json()["diagnostics"]] == ["E001"]


def test_oversized_body_is_refused_with_413(monkeypatch):
    monkeypatch.setattr("expsched.api.app.MAX_BODY_BYTES", 100)
    assert client.post("/v1/validate", json=DEMO).status_code == 413
    assert client.post("/v1/runs", json={"problem": DEMO}).status_code == 413


# ---- прогон ----


def test_run_returns_result_and_journal():
    response = client.post("/v1/runs", json={"problem": DEMO})
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["status"] == "OK" and body["result"]["policy"] == "priority"
    assert body["result"]["metrics"]["makespan"] == 21
    assert body["events"][0]["seq"] == 0 and len(body["events"]) > 0


def test_run_with_another_policy():
    body = client.post("/v1/runs", json={"problem": DEMO, "policy": {"name": "fifo"}}).json()
    assert body["result"]["policy"] == "fifo"


def test_run_with_a_schema_violation_is_422():
    response = client.post("/v1/runs", json={"problem": {**DEMO, "tasks": [{"id": "a", "duration": 0}]}})
    assert response.status_code == 422
    assert [d["code"] for d in response.json()["diagnostics"]] == ["E070"]


def test_run_with_a_policy_that_is_not_implemented_is_400():
    response = client.post("/v1/runs", json={"problem": DEMO, "policy": {"name": "cpsat"}})
    assert response.status_code == 400 and "cpsat" in response.json()["error"]
