"""Несколько машин: список агентов, ёмкость агента против узлов, отдельный слушатель, отказ машины."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.api.app import agent_listener, create_app
from expsched.api.store import RunStore
from expsched.contract.agent import Hello
from expsched.contract.io_models import load
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.driver import Emulation

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
STEP_MS = 50


def two_machines(**changes):
    problem = load((SCENARIOS / "two_machines.json").read_bytes())
    return problem.model_copy(update={"time_scale_ms": 100, **changes})


def hello(cpu=4, ram_mb=8192):
    return Hello(
        protocol=1,
        agent_version="0.1.0",
        runtime="fake",
        capacity={"cpu": cpu, "ram_mb": ram_mb},
        max_containers=4,
    )


class Machines:
    """Планировщик и два агента — alpha и beta, часы общие; машину можно «выключить»."""

    def __init__(self, tmp_path, problem):
        self.now = 0.0
        self.registry = AgentRegistry(clock=lambda: self.now, heartbeat_s=1)
        self.runtimes = {name: FakeRuntime() for name in ("alpha", "beta")}
        self.agents = {
            name: Agent(name, rt, tmp_path / name, {"cpu": 4, "ram_mb": 8192}, now=lambda: "T")
            for name, rt in self.runtimes.items()
        }
        self.online = {"alpha", "beta"}
        for name, agent in self.agents.items():
            agent.welcome(self.registry.hello(name, agent.hello()))
        self.emulation = Emulation(
            "0123456789abcdef", problem, problem.policy, self.registry, clock=lambda: self.now
        )

    def run(self, steps=4000, until=lambda: False):
        while self.emulation.status == "running" and steps and not until():
            self.emulation.tick()
            for name in sorted(self.online):
                agent = self.agents[name]
                agent.handle(self.registry.poll(name, agent.poll(0)))
                self.runtimes[name].advance(STEP_MS)
                agent.watch()
                if batch := agent.batch():
                    agent.acked(self.registry.events(name, batch))
            self.now += STEP_MS / 1000
            steps -= 1
        return self.emulation


def test_each_machine_runs_its_own_workers(tmp_path):
    m = Machines(tmp_path, two_machines())
    emulation = m.run()
    assert emulation.status == "done" and emulation.result is not None
    assert emulation.result.result["status"] == "OK"
    placed = {(c.task, c.agent) for c in emulation.containers.values()}
    assert {agent for _, agent in placed} == {"alpha", "beta"}  # обе машины работали
    for name, runtime in m.runtimes.items():  # каждая — только со своими узлами
        workers = {c.worker for c in emulation.containers.values() if c.agent == name}
        assert workers == {"ws-1" if name == "alpha" else "ws-2"}
        assert runtime.started == len([c for c in emulation.containers.values() if c.agent == name])


def test_a_machine_that_goes_silent_loses_its_workers_and_the_work_moves_on(tmp_path):
    m = Machines(tmp_path, two_machines())
    # beta замолкает, когда у её обучения есть сохранённая работа
    m.run(until=lambda: any(c.agent == "beta" and c.progress > 0 for c in m.emulation.containers.values()))
    lost = next(c for c in m.emulation.containers.values() if c.agent == "beta" and c.progress > 0)
    m.online.discard("beta")
    emulation = m.run()
    assert emulation.status == "done" and emulation.result.result["status"] == "OK"
    retry = emulation.containers[(lost.task, lost.attempt + 1)]
    assert (retry.agent, retry.resume_from) == ("alpha", lost.progress)
    # чекпоинт остался на beta: alpha об этом говорит, но продолжает с позиции планировщика
    assert retry.detail is not None and "not on this machine" in retry.detail


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    app = create_app(store=RunStore(tmp_path / "runs"))
    with TestClient(app) as client:
        yield client, app


def emulate(problem):
    return {"problem": problem.model_copy(update={"mode": "emulate"}).model_dump(mode="json")}


def test_agents_are_listed_with_their_capacity_and_running_attempts(client):
    client, app = client
    auth = {"Authorization": "Bearer t"}
    assert client.get("/v1/agents").json() == {"agents": [], "heartbeat_timeout_s": 15}
    client.post("/v1/agents/alpha/hello", json=hello().model_dump(mode="json"), headers=auth)
    run_id = client.post("/v1/runs", json=emulate(two_machines())).json()["run_id"]
    app.state.emulations.live[run_id].emulation.tick()
    listed = client.get("/v1/agents").json()["agents"]
    assert [(a["id"], a["runtime"], a["capacity"], a["alive"]) for a in listed] == [
        ("alpha", "fake", {"cpu": 4, "ram_mb": 8192}, True)
    ]
    assert listed[0]["running"] == [f"{run_id}:prepare:1"]
    live = client.get("/v1/live").json()["runs"]
    assert [(r["run_id"], r["name"], r["mode"], r["status"], r["running"]) for r in live] == [
        (run_id, "two_machines", "emulate", "running", 1)
    ]
    client.post(f"/v1/runs/{run_id}/actions", json={"kind": "cancel"})


def test_a_known_agent_must_hold_its_workers(client):
    client, _ = client
    small = hello(cpu=2).model_dump(mode="json")
    client.post("/v1/agents/alpha/hello", json=small, headers={"Authorization": "Bearer t"})
    response = client.post("/v1/runs", json=emulate(two_machines()))
    assert response.status_code == 422
    assert "workers of agent alpha need more than it offers: cpu 4 > 2" in response.json()["error"]["message"]
    # beta сервер ещё не видел — его не проверить, прогон подождёт его; alpha уменьшили узел — проходит
    smaller = two_machines()
    workers = [
        w.model_copy(update={"resources": {"cpu": 2, "ram_mb": 4096}}) if w.id == "ws-1" else w
        for w in smaller.cluster.workers
    ]
    smaller = smaller.model_copy(update={"cluster": smaller.cluster.model_copy(update={"workers": workers})})
    started = client.post("/v1/runs", json=emulate(smaller))
    assert started.status_code == 202
    client.post(f"/v1/runs/{started.json()['run_id']}/actions", json={"kind": "cancel"})


def test_separate_agent_listener_serves_only_the_agent_api(monkeypatch):
    app = create_app()
    assert agent_listener(app, None) is None
    monkeypatch.delenv("EXPSCHED_AGENT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="needs EXPSCHED_AGENT_TOKEN"):
        agent_listener(app, "0.0.0.0:8001")
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    with pytest.raises(RuntimeError, match="host:port"):
        agent_listener(app, "0.0.0.0")
    server = agent_listener(app, "0.0.0.0:8001")
    assert server is not None and (server.config.host, server.config.port) == ("0.0.0.0", 8001)
    agent_app = server.config.app
    assert agent_app.state.agents is app.state.agents  # тот же реестр, что у основного сервера
    with TestClient(agent_app) as agents_only:
        assert agents_only.get("/v1/runs").status_code == 404  # ни клиента, ни остального API
        assert agents_only.get("/v1/agents").status_code == 404  # и списка агентов тоже
        assert (
            agents_only.post("/v1/agents/alpha/hello", json=hello().model_dump(mode="json")).status_code
            == 403
        )
