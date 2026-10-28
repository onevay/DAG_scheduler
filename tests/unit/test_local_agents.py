"""Агенты на машине сервера: проверка, запуск, остановка, пределы, API; настоящие процессы — медленно."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.local_agents import CheckFailed, LocalAgents
from expsched.api.store import RunStore


class Recorder:
    """Проверка с заданным ответом и запуск «агента» — процесса, который просто ждёт."""

    def __init__(self, code=0, output="ok"):
        self.code, self.output = code, output
        self.checks, self.spawned = [], []

    def check(self, args, env):
        self.checks.append(list(args))
        return self.code, self.output

    def spawn(self, args, env, log):
        self.spawned.append(list(args))
        log.write_text("agent started\nhello ok\n", encoding="utf-8")
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


@pytest.fixture
def manager(tmp_path):
    recorder = Recorder()
    agents = LocalAgents(tmp_path / "agents", check=recorder.check, start=recorder.spawn, limit=2)
    yield agents, recorder
    agents.close()


def test_start_checks_first_then_runs_the_agent_and_stops_it(manager, tmp_path):
    agents, recorder = manager
    agents.start("box", "fake", {"cpu": 4, "ram_mb": None}, "http://127.0.0.1:8000")
    check, run = recorder.checks[0], recorder.spawned[0]
    assert check[1:5] == ["-m", "expsched.agent", "check", "--no-hello"] and run[1:3] == [
        "-m",
        "expsched.agent",
    ]
    assert check[5:] == run[3:]  # проверяется ровно то, что запустится
    assert run[3:] == ["--id", "box", "--runtime", "fake", "--scheduler", "http://127.0.0.1:8000",
                       "--state", str(tmp_path / "agents" / "box" / "state"), "--cpu", "4"]  # fmt: skip
    [described] = agents.describe()
    assert (
        described["alive"]
        and described["exit_code"] is None
        and described["log"] == ["agent started", "hello ok"]
    )
    with pytest.raises(ValueError, match="already running"):
        agents.start("box", "fake", {}, "http://x")
    assert agents.stop("box") and agents.describe() == [] and not agents.stop("box")


def test_failed_check_starts_nothing(tmp_path):
    recorder = Recorder(code=1, output="FAIL runtime docker: Cannot connect to the Docker daemon")
    agents = LocalAgents(tmp_path, check=recorder.check, start=recorder.spawn)
    with pytest.raises(CheckFailed, match="Cannot connect to the Docker daemon"):
        agents.start("box", "docker", {}, "http://127.0.0.1:8000")
    assert recorder.spawned == [] and agents.describe() == []


def test_at_most_limit_agents_run(manager):
    agents, _ = manager
    agents.start("a", "fake", {}, "http://x")
    agents.start("b", "fake", {}, "http://x")
    with pytest.raises(ValueError, match="2 local agents are already running"):
        agents.start("c", "fake", {}, "http://x")


def test_api_needs_the_token_and_reports_conflicts_and_failed_checks(tmp_path, monkeypatch):
    recorder = Recorder()
    app = create_app(store=RunStore(tmp_path / "runs"))
    app.state.local_agents = LocalAgents(tmp_path / "agents", check=recorder.check, start=recorder.spawn)
    with TestClient(app) as client:
        monkeypatch.delenv("EXPSCHED_AGENT_TOKEN", raising=False)
        assert client.post("/v1/local-agents", json={"id": "box"}).json()["error"]["code"] == "FORBIDDEN"
        monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
        started = client.post("/v1/local-agents", json={"id": "box", "runtime": "fake", "cpu": 2})
        assert started.status_code == 201
        assert (
            started.json()["id"] == "box" and started.json()["alive"] and started.json()["runtime"] == "fake"
        )
        assert client.post("/v1/local-agents", json={"id": "box"}).json()["error"]["code"] == "AGENT_RUNNING"
        assert client.post("/v1/local-agents", json={"id": "../etc"}).status_code == 422
        assert (
            client.post("/v1/local-agents", json={"id": "x", "runtime": "gravesentinel"}).status_code == 422
        )
        recorder.code, recorder.output = 1, "FAIL scheduler: connection refused"
        refused = client.post("/v1/local-agents", json={"id": "other"}).json()["error"]
        assert refused == {"code": "AGENT_NOT_READY", "message": "FAIL scheduler: connection refused"}
        assert [a["id"] for a in client.get("/v1/local-agents").json()["agents"]] == ["box"]
        assert client.delete("/v1/local-agents/box").status_code == 204
        assert client.delete("/v1/local-agents/box").status_code == 404
    assert app.state.local_agents.describe() == []


@pytest.mark.slow
def test_server_starts_a_real_agent_that_says_hello_and_stops_it(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "EXPSCHED_AGENT_TOKEN": "s3cret", "EXPSCHED_RUNS_DIR": str(tmp_path / "runs")}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "expsched.api.app:app", "--port", str(port)], env=env
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{url}/v1/policies")
                break
            except httpx.TransportError:
                time.sleep(0.1)
        started = httpx.post(
            f"{url}/v1/local-agents", json={"id": "local", "runtime": "fake", "cpu": 2}, timeout=60
        )
        assert started.status_code == 201, started.text
        deadline = time.monotonic() + 30
        while not [
            a for a in httpx.get(f"{url}/v1/agents").json()["agents"] if a["id"] == "local" and a["alive"]
        ]:
            assert time.monotonic() < deadline, "the local agent did not say hello"
            time.sleep(0.2)
        assert httpx.delete(f"{url}/v1/local-agents/local", timeout=30).status_code == 204
        assert httpx.get(f"{url}/v1/local-agents").json() == {"agents": []}
        assert (tmp_path / ".expsched-local-agents" / "local" / "agent.log").exists()
    finally:
        server.kill()
