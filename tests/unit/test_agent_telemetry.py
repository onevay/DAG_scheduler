"""Потребление и журналы контейнеров: агент собирает, опрос несёт, сервер помнит и отдаёт."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from expsched.agent.core import Agent
from expsched.agent.docker import DockerRuntime
from expsched.agent.runtime import FakeRuntime
from expsched.contract.agent import AgentEvent, Poll, PollReply, Usage
from expsched.emulate.agents import AgentRegistry

RUN = "0123456789abcdef"
KEY = f"{RUN}:train:1"
START = {
    "run_id": RUN,
    "task": "train",
    "attempt": 1,
    "kind": "start",
    "command_id": 1,
    "worker": "laptop",
    "limits": {"cpu": 2, "ram_mb": 1024},
    "work": 30,
    "checkpoint_interval": 10,
    "time_scale_ms": 100,
}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def agent(tmp_path, runtime, clock):
    return Agent("box", runtime, tmp_path, {"cpu": 4, "ram_mb": 8192}, now=lambda: "T", clock=clock)


def test_poll_carries_usage_and_new_log_lines_once(tmp_path):
    runtime, clock = FakeRuntime(), Clock()
    a = agent(tmp_path, runtime, clock)
    a.handle(PollReply.model_validate({"commands": [START]}))
    runtime.advance(1500)
    a.watch()
    poll = a.poll(1)
    assert poll.usage == {KEY: Usage(cpu_milli=1800, ram_mb=512)}
    assert poll.logs == {KEY: ["start: 0 of 30 units", "checkpoint: 10 units"]}
    assert a.poll(1).logs == {}  # строки отдаются один раз

    runtime.advance(700)
    clock.now += 1  # раньше интервала журналы не читаются — строки ждут в среде
    a.watch()
    assert a.poll(1).logs == {}
    clock.now += 1
    a.watch()
    assert a.poll(1).logs == {KEY: ["checkpoint: 20 units"]}


def test_failed_container_carries_the_tail_of_its_log(tmp_path):
    runtime, clock = FakeRuntime(), Clock()
    a = agent(tmp_path, runtime, clock)
    a.handle(PollReply.model_validate({"commands": [START]}))
    runtime.advance(1500)
    runtime.oom(KEY)
    a.watch()
    oom = a.outbox[-1]
    assert (
        oom.type == "oom"
        and oom.detail == "log: start: 0 of 30 units | checkpoint: 10 units | killed: out of memory"
    )
    assert a.poll(1).usage == {}  # упавший не замеряется


def test_poll_limits_are_part_of_the_contract():
    with pytest.raises(ValidationError):
        Poll(acked_command_id=0, logs={KEY: ["x"] * 201})
    with pytest.raises(ValidationError):
        Poll(acked_command_id=0, usage={"not-a-key": Usage(cpu_milli=1, ram_mb=1)})


def test_docker_usage_in_one_call_and_logs_after_the_last_seen_line():
    replies = {
        "stats": "\n".join(
            json.dumps(row)
            for row in (
                {"Name": f"expsched-{RUN}-train-1", "CPUPerc": "152.5%", "MemUsage": "1.5GiB / 4GiB"},
                {"Name": f"expsched-{RUN}-eval-1", "CPUPerc": "0.00%", "MemUsage": "12.5MiB / 1GiB"},
            )
        ),
        "logs": "2026-10-27T10:00:00.000000001Z start\n2026-10-27T10:00:01.000000001Z checkpoint 2\n",
    }
    calls = []

    def cli(args):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, replies.get(args[1], ""), "")

    runtime = DockerRuntime("box", run=cli)
    usage = runtime.usage([KEY, f"{RUN}:eval:1"])
    assert usage == {KEY: Usage(cpu_milli=1525, ram_mb=1536), f"{RUN}:eval:1": Usage(cpu_milli=0, ram_mb=12)}
    assert [c[1] for c in calls] == ["stats"]
    assert runtime.logs(KEY) == ["start", "checkpoint 2"]
    assert runtime.logs(KEY) == []  # те же строки по --since не повторяются
    assert calls[-1][3:5] == ["--since", "2026-10-27T10:00:01.000000001Z"]


def test_registry_hands_telemetry_to_the_run_and_the_api_serves_logs(tmp_path, monkeypatch):
    from expsched.api.app import create_app
    from expsched.api.store import RunStore

    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    app = create_app(store=RunStore(tmp_path / "runs"))
    with TestClient(app) as client:
        problem = json.loads(Path("scenarios/criu_pairs.json").read_text(encoding="utf-8"))
        for worker in problem["cluster"]["workers"]:
            worker["agent"] = "box"
        problem.update(mode="emulate", time_scale_ms=100)
        run_id = client.post("/v1/runs", json={"problem": problem}).json()["run_id"]
        registry: AgentRegistry = app.state.emulations.registry
        emulation = app.state.emulations.live[run_id].emulation
        emulation.tick()
        task, attempt = next(iter(emulation.containers))
        key = f"{run_id}:{task}:{attempt}"
        registry.poll(
            "box",
            Poll(acked_command_id=0, usage={key: Usage(cpu_milli=900, ram_mb=40)}, logs={key: ["a", "b"]}),
        )
        registry.poll("box", Poll(acked_command_id=0, logs={key: ["c"], f"{'f' * 16}:x:1": ["other run"]}))

        live = client.get(f"/v1/runs/{run_id}/containers").json()
        row = next(c for c in live["containers"] if c["task"] == task)
        assert row["usage"] == {"cpu_milli": 900, "ram_mb": 40}
        assert client.get(f"/v1/runs/{run_id}/containers/{task}/{attempt}/logs").json() == {
            "lines": ["a", "b", "c"]
        }
        assert client.get(f"/v1/runs/{run_id}/containers/{task}/9/logs").status_code == 404

        # конец попытки: замер больше не показывается, а хвост журнала из события — в причине сбоя
        emulation.on_event(
            AgentEvent.model_validate(
                {
                    "run_id": run_id,
                    "task": task,
                    "attempt": attempt,
                    "seq": 0,
                    "at": "T",
                    "type": "exited",
                    "exit_code": 3,
                    "detail": "log: b | c",
                }  # fmt: skip
            )
        )
        emulation.tick()
        row = next(
            c for c in client.get(f"/v1/runs/{run_id}/containers").json()["containers"] if c["task"] == task
        )
        assert row["usage"] is None
        interrupted = [r for r in emulation.sim.journal.records if r["type"] == "INTERRUPTED"]
        assert interrupted[0]["reason"] == {"detail": "exit code 3; log: b | c"}
        client.post(f"/v1/runs/{run_id}/actions", json={"kind": "cancel"})
