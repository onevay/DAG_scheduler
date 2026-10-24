"""Сквозной путь клиента по настоящему серверу: uvicorn в отдельном процессе, запросы по HTTP."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "EXPSCHED_RUNS_DIR": str(tmp_path_factory.mktemp("runs")),
        "EXPSCHED_RUN_WORKERS": "2",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "expsched.api.app:app", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False)
    try:
        for _ in range(300):  # запуск сервера: до 30 с на медленной машине
            try:
                client.get("/v1/policies")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("API did not start")
        yield client
    finally:
        client.close()
        server.terminate()
        server.wait(timeout=30)


def test_client_journey(api):
    names = [s["name"] for s in api.get("/v1/scenarios").json()["scenarios"]]
    assert "urgent_failure" in names
    problem = api.get("/v1/scenarios/urgent_failure").json()

    check = api.post("/v1/validate", json=problem).json()
    assert check["valid"] and check["analysis"]["lower_bound"] == 21

    created = api.post("/v1/runs", json={"problem": problem})
    assert created.status_code == 201
    run_id, result = created.json()["run_id"], created.json()["result"]
    assert result["metrics"]["makespan"] == 22 and result["violations"] == []

    events, from_seq = [], 0
    while from_seq is not None:
        page = api.get(f"/v1/runs/{run_id}/events", params={"from_seq": from_seq, "limit": 16}).json()
        events += page["events"]
        from_seq = page["next_seq"]
    assert [r["seq"] for r in events] == list(range(len(events))) and events[-1]["type"] == "FINISH"

    why = api.get(f"/v1/runs/{run_id}/explain", params={"worker": "node-C", "t": 14}).json()
    assert [i["code"] for i in why["idle"]] == ["IDLE_NO_READY"]
    assert why["decomposition"]["lost"] == 1

    shifted = api.post(
        f"/v1/runs/{run_id}/events", json={"event": {"time": 12, "type": "worker_down", "worker": "node-B"}}
    ).json()
    assert shifted["run_id"] != run_id and any(r["t"] == 12 for r in shifted["result"]["replans"])

    what_if = api.post(
        "/v1/whatif",
        json={
            "problem": problem,
            "interventions": [{"kind": "set_checkpoint", "task": "pretrain_vit", "checkpoint_interval": 1}],
        },
    ).json()
    assert what_if["baseline"]["run_id"] == run_id  # исходный прогон уже сохранён и не пересчитывался
    assert what_if["variants"][0]["delta"]["lost_work_gpu"] == -4

    listed = [r["run_id"] for r in api.get("/v1/runs").json()["runs"]]
    assert {run_id, shifted["run_id"], what_if["variants"][0]["run_id"]} <= set(listed)


def test_errors_over_http(api):
    assert api.get("/v1/runs/0123456789abcdef").json()["error"]["code"] == "NOT_FOUND"
    response = api.post("/v1/runs", content=b"{not json", headers={"content-type": "application/json"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "SCHEMA_INVALID"
