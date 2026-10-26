"""Прогон на агентах: драйвер, реестр агентов и агент на подделке среды с общими подменными часами."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.contract.io_models import ProblemIn, load
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.driver import Emulation

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
RUN = "0123456789abcdef"
STEP_MS = 50


def laptop(**changes):
    problem = load((SCENARIOS / "laptop.json").read_bytes())
    workers = [w.model_copy(update={"agent": "laptop"}) for w in problem.cluster.workers]
    cluster = problem.cluster.model_copy(update={"workers": workers})
    return problem.model_copy(update={"cluster": cluster, "time_scale_ms": 100, **changes})


class Loop:
    """Планировщик и агент по очереди, часы общие: шаг — STEP_MS мс стены."""

    def __init__(self, tmp_path, problem):
        self.now = 0.0
        self.registry = AgentRegistry(clock=lambda: self.now, heartbeat_s=1)
        self.runtime = FakeRuntime()
        self.agent = Agent("laptop", self.runtime, tmp_path, {"cpu": 8, "ram_mb": 16384}, now=lambda: "T")
        self.emulation = Emulation(RUN, problem, problem.policy, self.registry, clock=lambda: self.now)
        self.online = True
        self.registry.hello("laptop", self.agent.hello())

    def step(self):
        self.emulation.tick()
        if self.online:
            self.agent.handle(self.registry.poll("laptop", self.agent.poll(0)))
        self.runtime.advance(STEP_MS)
        self.now += STEP_MS / 1000
        if self.online:
            self.agent.watch()
            if batch := self.agent.batch():
                self.agent.acked(self.registry.events("laptop", batch))

    def run(self, steps=2000):
        while self.emulation.status == "running" and steps:
            self.step()
            steps -= 1
        return self.emulation


def records(emulation, kind):
    return [r for r in emulation.sim.journal.records if r["type"] == kind]


def test_laptop_runs_on_its_agent_to_the_end(tmp_path):
    emulation = Loop(tmp_path, laptop()).run()
    assert emulation.status == "done" and emulation.result is not None
    result = emulation.result.result
    assert result["status"] == "OK" and result["violations"] == []
    assert 90 <= result["metrics"]["makespan"] <= 96  # 90 в симуляции; факты приходят с шагом слежения
    assert {c.state for c in emulation.containers.values()} == {"exited"}
    assert (
        result["tasks"]["train_small"]["start"] >= 35
    )  # по-прежнему ждёт памяти, которую держит embed_index


def test_container_out_of_memory_is_retried(tmp_path):
    loop = Loop(tmp_path, laptop())
    while not loop.runtime.containers:
        loop.step()
    key = sorted(loop.runtime.containers)[0]
    loop.runtime.oom(key)
    emulation = loop.run()
    task = key.split(":")[1]
    assert [(r["task"], r["cause"]) for r in records(emulation, "INTERRUPTED")] == [(task, "OOM")]
    assert emulation.result is not None and emulation.result.result["status"] == "OK"


def test_silent_agent_is_a_failure_of_its_nodes_and_comes_back(tmp_path):
    loop = Loop(tmp_path, laptop())
    for _ in range(40):
        loop.step()
    loop.online = False  # агент перестал выходить на связь дольше heartbeat
    for _ in range(40):
        loop.step()
    assert [r["worker"] for r in records(loop.emulation, "WORKER_DOWN")] == ["laptop"]
    loop.online = True
    loop.registry.hello("laptop", loop.agent.hello())
    emulation = loop.run()
    assert [r["worker"] for r in records(emulation, "WORKER_UP")] == ["laptop"]
    assert {r["cause"] for r in records(emulation, "INTERRUPTED")} == {"WORKER_DOWN"}
    assert emulation.result is not None and emulation.result.result["status"] == "OK"


def test_cancel_stops_running_attempts(tmp_path):
    loop = Loop(tmp_path, laptop())
    for _ in range(5):
        loop.step()
    running = set(loop.emulation.sim.state.run)
    loop.emulation.cancel()
    loop.step()
    assert loop.emulation.status == "cancelled" and running and not loop.runtime.containers


def test_every_worker_needs_an_agent():
    with pytest.raises(ValueError, match="without an agent: laptop"):
        Emulation(
            RUN,
            load((SCENARIOS / "laptop.json").read_bytes()),
            ProblemIn.model_fields["policy"].default,
            AgentRegistry(),
        )


# ---- HTTP ----


def test_agent_api_needs_the_token_and_keeps_event_order(tmp_path, monkeypatch):
    client = TestClient(create_app(store=RunStore(tmp_path)))
    hello = {
        "protocol": 1,
        "agent_version": "t",
        "runtime": "fake",
        "capacity": {"cpu": 2},
        "max_containers": 1,
    }
    assert client.post("/v1/agents/laptop/hello", json=hello).json()["error"]["code"] == "FORBIDDEN"
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    assert (
        client.post("/v1/agents/laptop/hello", json=hello, headers={"Authorization": "Bearer x"}).status_code
        == 403
    )
    assert client.post("/v1/agents/laptop/hello", json=hello, headers=auth).json()["next_event_seq"] == 0

    ref = {"run_id": RUN, "task": "t", "attempt": 1}
    client.app.state.agents.send("laptop", {**ref, "kind": "stop", "reason": "cancel"})
    reply = client.post(
        "/v1/agents/laptop/poll", json={"acked_command_id": 0, "wait_s": 0}, headers=auth
    ).json()
    assert [c["command_id"] for c in reply["commands"]] == [1]
    assert client.post(
        "/v1/agents/laptop/poll", json={"acked_command_id": 1, "wait_s": 0}, headers=auth
    ).json() == {"commands": []}

    def event(seq):
        return {**ref, "seq": seq, "at": "T", "type": "killed"}

    events = client.post("/v1/agents/laptop/events", json={"events": [event(0), event(1)]}, headers=auth)
    assert events.json() == {"next_event_seq": 2}
    again = client.post("/v1/agents/laptop/events", json={"events": [event(1), event(2)]}, headers=auth)
    assert again.json() == {"next_event_seq": 3}  # повтор принятого пропущен
    gap = client.post("/v1/agents/laptop/events", json={"events": [event(5)]}, headers=auth)
    assert gap.status_code == 409 and gap.json()["error"]["code"] == "EVENT_GAP"
