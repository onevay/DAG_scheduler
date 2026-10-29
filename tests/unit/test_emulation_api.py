"""Прогон на агентах через API: 202, контейнеры, действия, сохранение; настоящий агент по HTTP."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.contract.agent import EventsAck, HelloAck, PollReply
from expsched.contract.io_models import load

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
AUTH = {"Authorization": "Bearer s3cret"}


def laptop(scale_ms=10, agent="laptop"):
    problem = load((SCENARIOS / "laptop.json").read_bytes())
    workers = [w.model_copy(update={"agent": agent}) for w in problem.cluster.workers]
    problem = problem.model_copy(
        update={"cluster": problem.cluster.model_copy(update={"workers": workers}), "mode": "emulate"}
    )
    return problem.model_copy(update={"time_scale_ms": scale_ms}).model_dump(mode="json")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "s3cret")
    with TestClient(create_app(store=RunStore(tmp_path / "runs"))) as client:
        yield client


class HttpAgent:
    """Ядро агента на подделке среды, которое ходит в API через TestClient."""

    def __init__(self, client, tmp_path):
        self.client, self.runtime, self.clock = client, FakeRuntime(), time.monotonic()
        self.agent = Agent("laptop", self.runtime, tmp_path / "agent", {"cpu": 8, "ram_mb": 16384})
        self.agent.welcome(HelloAck.model_validate(self.call("hello", self.agent.hello())))

    def call(self, path, body):
        headers = {**AUTH, "Content-Type": "application/json"}
        return self.client.post(
            f"/v1/agents/laptop/{path}", content=body.model_dump_json(), headers=headers
        ).json()

    def step(self):
        self.agent.handle(PollReply.model_validate(self.call("poll", self.agent.poll(0))))
        now = time.monotonic()
        self.runtime.advance(round((now - self.clock) * 1000))
        self.clock = now
        self.agent.watch()
        if batch := self.agent.batch():
            self.agent.acked(EventsAck.model_validate(self.call("events", batch)))
        time.sleep(0.01)


def live(client, run_id):
    return client.get(f"/v1/runs/{run_id}/containers").json()


def test_run_on_an_agent_goes_in_the_background_and_is_saved(client, tmp_path):
    response = client.post("/v1/runs", json={"problem": laptop()})
    assert response.status_code == 202 and response.json()["status"] == "running"
    run_id = response.json()["run_id"]
    agent = HttpAgent(client, tmp_path)
    agent.step()
    assert client.get(f"/v1/runs/{run_id}").json()["error"]["code"] == "RUN_IN_PROGRESS"
    assert client.get(f"/v1/runs/{run_id}/problem").json()["mode"] == "emulate"
    assert client.get(f"/v1/runs/{run_id}/events").json()["total"] > 0
    deadline = time.monotonic() + 20
    while live(client, run_id)["status"] == "running" and time.monotonic() < deadline:
        agent.step()
    state = live(client, run_id)
    assert state["status"] == "done" and {c["state"] for c in state["containers"]} == {"exited"}
    for _ in range(50):  # результат сохраняется в фоне сразу после окончания
        if (saved := client.get(f"/v1/runs/{run_id}")).status_code == 200:
            break
        time.sleep(0.05)
    assert saved.json()["result"]["status"] == "OK" and saved.json()["result"]["violations"] == []


def test_actions_drop_and_restore_a_node_and_cancel(client, tmp_path):
    run_id = client.post("/v1/runs", json={"problem": laptop(scale_ms=100)}).json()["run_id"]
    agent = HttpAgent(client, tmp_path)
    while not agent.runtime.containers:
        agent.step()
    dropped = client.post(
        f"/v1/runs/{run_id}/actions", json={"kind": "worker_down", "worker": "laptop"}
    ).json()
    assert dropped["workers"]["laptop"] == {"agent": "laptop", "up": False, "dropped": True}
    for _ in range(5):
        agent.step()
    assert not agent.runtime.containers  # контейнеры уроненного узла остановлены
    restored = client.post(
        f"/v1/runs/{run_id}/actions", json={"kind": "worker_up", "worker": "laptop"}
    ).json()
    assert restored["workers"]["laptop"]["up"] is True
    assert client.post(f"/v1/runs/{run_id}/actions", json={"kind": "jump"}).status_code == 422
    cancelled = client.post(f"/v1/runs/{run_id}/actions", json={"kind": "cancel"}).json()
    assert cancelled["status"] == "cancelled"


def test_runs_on_agents_are_refused_when_they_cannot_go(client, monkeypatch):
    assert client.post("/v1/runs", json={"problem": laptop(agent=None)}).status_code == 422
    first = client.post("/v1/runs", json={"problem": laptop()})
    assert first.status_code == 202
    assert client.post("/v1/runs", json={"problem": laptop()}).json()["error"]["code"] == "RUN_QUEUE_FULL"
    replay = {**laptop(), "mode": "replay"}
    assert client.post("/v1/runs", json={"problem": replay}).status_code == 400
    assert client.get("/v1/runs/0123456789abcdef/containers").status_code == 404


@pytest.mark.slow
def test_real_agent_process_runs_a_laptop_over_http(tmp_path):
    """Сервер и агент — отдельные процессы, связь — настоящий HTTP; среда агента — подделка по часам стены."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "EXPSCHED_AGENT_TOKEN": "s3cret", "EXPSCHED_RUNS_DIR": str(tmp_path / "runs")}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "expsched.api.app:app", "--port", str(port)], env=env
    )
    url = f"http://127.0.0.1:{port}"
    agent = subprocess.Popen(
        [sys.executable, "-m", "expsched.agent", "--scheduler", url, "--id", "laptop", "--runtime", "fake",
         "--state", str(tmp_path / "agent"), "--cpu", "8", "--ram-mb", "16384"],
        env=env,
    )  # fmt: skip
    try:
        for _ in range(100):
            try:
                httpx.get(f"{url}/v1/policies")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        run_id = httpx.post(f"{url}/v1/runs", json={"problem": laptop(scale_ms=20)}).json()["run_id"]
        deadline = time.monotonic() + 60
        while httpx.get(f"{url}/v1/runs/{run_id}/containers").json()["status"] == "running":
            assert time.monotonic() < deadline, "run on the agent did not finish"
            time.sleep(0.2)
        for _ in range(50):  # результат сохраняется в фоне сразу после окончания
            if (saved := httpx.get(f"{url}/v1/runs/{run_id}")).status_code == 200:
                break
            time.sleep(0.1)
        result = saved.json()["result"]
        assert result["status"] == "OK" and result["violations"] == []
    finally:
        agent.kill()
        server.kill()


def test_shadow_run_shows_its_plan_from_the_start(client):
    problem = {**laptop(), "mode": "shadow"}
    run_id = client.post("/v1/runs", json={"problem": problem}).json()["run_id"]
    shadow = live(client, run_id)["shadow"]
    assert shadow["plan_makespan"] == 90 and shadow["causes"]["waiting"] == 6
    assert shadow["tasks"]["train_small"]["plan_start"] == 35
    client.post(f"/v1/runs/{run_id}/actions", json={"kind": "cancel"})
