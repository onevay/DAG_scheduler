import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.pipeline import run

DEMO = json.loads(
    (Path(__file__).resolve().parents[2] / "scenarios" / "demo.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Одно хранилище на модуль: временные каталоги на Windows создаются медленно."""
    with TestClient(create_app(store=RunStore(tmp_path_factory.mktemp("runs")))) as client:
        yield client  # выход из контекста закрывает рабочие процессы


class InlineRunner:
    """Прогон в процессе теста: можно считать вызовы и подменять прогон."""

    def __init__(self, compute=run):
        self.compute = compute
        self.calls = 0

    async def run(self, problem, policy):
        self.calls += 1
        return self.compute(problem, policy)

    def close(self):
        pass


def with_tasks(tasks):
    return {**DEMO, "tasks": tasks, "events": []}


def test_policies_lists_implemented_ones_and_defaults(client):
    body = client.get("/v1/policies").json()
    assert body["policies"] == ["fifo", "fifo_skip", "priority", "priority_backfill"]
    assert body["defaults"]["validation_mode"] == "lenient"


# ---- проверка входа ----


def test_validate_accepts_the_demo(client):
    response = client.post("/v1/validate", json=DEMO)
    assert response.status_code == 200 and response.json() == {"valid": True, "diagnostics": []}


def test_validate_reports_graph_errors_with_200_in_both_modes(client):
    cycle = with_tasks(
        [{"id": "a", "duration": 1, "dependencies": ["b"]}, {"id": "b", "duration": 1, "dependencies": ["a"]}]
    )
    for mode in ("lenient", "strict"):
        response = client.post(f"/v1/validate?mode={mode}", json=cycle)
        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert [d["code"] for d in response.json()["diagnostics"]] == ["E012"]


def test_validate_rejects_a_schema_violation_with_422(client):
    response = client.post("/v1/validate", json={**DEMO, "unexpected": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SCHEMA_INVALID"
    assert [d["code"] for d in response.json()["diagnostics"]] == ["E001"]


def test_oversized_body_is_refused_with_413(client, monkeypatch):
    monkeypatch.setattr("expsched.api.app.MAX_BODY_BYTES", 100)
    for path, body in [("/v1/validate", DEMO), ("/v1/runs", {"problem": DEMO})]:
        response = client.post(path, json=body)
        assert response.status_code == 413 and response.json()["error"]["code"] == "INPUT_TOO_LARGE"


# ---- прогон ----


def test_run_is_stored_and_read_back(client):
    response = client.post("/v1/runs", json={"problem": DEMO})
    assert response.status_code == 201
    body = response.json()
    assert body["result"]["status"] == "OK" and body["result"]["metrics"]["makespan"] == 21
    run_id = body["run_id"]
    assert run_id == body["result"]["input_hash"].removeprefix("sha256:")[:16]
    assert client.get(f"/v1/runs/{run_id}").json() == body
    assert client.get(f"/v1/runs/{run_id}/problem").json()["policy"]["name"] == "priority"


def test_same_request_is_not_computed_again(tmp_path):
    runner = InlineRunner()
    client = TestClient(create_app(store=RunStore(tmp_path), runner=runner))  # своё хранилище: демо там нет
    first = client.post("/v1/runs", json={"problem": DEMO}).json()
    assert client.post("/v1/runs", json={"problem": DEMO}).json() == first and runner.calls == 1
    other = client.post("/v1/runs", json={"problem": DEMO, "policy": {"name": "fifo_skip"}}).json()
    assert other["run_id"] != first["run_id"] and runner.calls == 2  # другая политика — другой прогон


def test_journal_is_read_in_pages(client):
    run_id = client.post("/v1/runs", json={"problem": DEMO}).json()["run_id"]
    pages, from_seq = [], 0
    while from_seq is not None:
        page = client.get(f"/v1/runs/{run_id}/events", params={"from_seq": from_seq, "limit": 20}).json()
        pages.append(page["events"])
        from_seq = page["next_seq"]
    records = [r for page in pages for r in page]
    assert [r["seq"] for r in records] == list(range(page["total"])) and len(pages) > 1
    assert all(len(p) == 20 for p in pages[:-1])


@pytest.mark.parametrize("path", ["", "/problem", "/events"])
def test_unknown_run_is_404(client, path):
    response = client.get(f"/v1/runs/0123456789abcdef{path}")
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_run_id_must_look_like_one(client):
    response = client.get("/v1/runs/..%2F..%2Fetc")
    assert response.status_code in (404, 422) and "error" in response.json()


def test_run_with_another_policy(client):
    body = client.post("/v1/runs", json={"problem": DEMO, "policy": {"name": "fifo"}}).json()
    assert body["result"]["policy"] == "fifo"


def test_run_with_a_schema_violation_is_422(client):
    response = client.post("/v1/runs", json={"problem": {**DEMO, "tasks": [{"id": "a", "duration": 0}]}})
    assert response.status_code == 422
    assert [d["code"] for d in response.json()["diagnostics"]] == ["E070"]


def test_run_with_a_policy_that_is_not_implemented_is_400(client):
    response = client.post("/v1/runs", json={"problem": DEMO, "policy": {"name": "cpsat"}})
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "POLICY_NOT_IMPLEMENTED", "message": "policy 'cpsat' is not implemented yet"}
    }


# ---- ошибки, CORS, OpenAPI ----


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/v1/nowhere", 404, "NOT_FOUND"),
        ("GET", "/v1/runs", 405, "METHOD_NOT_ALLOWED"),
        ("POST", "/v1/validate?mode=loose", 422, "INVALID_REQUEST"),
    ],
)
def test_every_error_has_the_same_shape(client, method, path, status, code):
    response = client.request(method, path, json=DEMO)
    assert response.status_code == status
    assert set(response.json()) == {"error"} and response.json()["error"]["code"] == code


def test_unexpected_failure_is_reported_without_a_traceback(tmp_path):
    def broken(*args):
        raise RuntimeError("secret detail")

    app = create_app(store=RunStore(tmp_path), runner=InlineRunner(broken))
    response = TestClient(app, raise_server_exceptions=False).post("/v1/runs", json={"problem": DEMO})
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "INTERNAL", "message": "internal server error"}}


def test_cors_is_enabled_only_for_listed_origins(client):
    preflight = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    allowed = TestClient(create_app(["http://localhost:5173"])).options("/v1/runs", headers=preflight)
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = TestClient(create_app(["http://localhost:5173"])).get(
        "/v1/policies", headers={"Origin": "http://evil.example"}
    )
    assert "access-control-allow-origin" not in other.headers
    assert "access-control-allow-origin" not in client.get("/v1/policies", headers=preflight).headers


def test_openapi_describes_requests_responses_and_errors(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    run = spec["paths"]["/v1/runs"]["post"]
    assert run["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RunRequest"
    }
    assert set(run["responses"]) >= {"201", "400", "413", "422", "500"}
    assert {"RunOut", "ResultOut", "ErrorOut", "ProblemIn", "ReplanRecord"} <= set(schemas)

    def refs(node):
        if isinstance(node, dict):
            yield from (v for k, v in node.items() if k == "$ref")
            for v in node.values():
                yield from refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from refs(v)

    assert {r.removeprefix("#/components/schemas/") for r in refs(spec)} <= set(
        schemas
    )  # все ссылки разрешаются
