"""Находки ревью бэкенда: каждая — тест, который падал до исправления."""

import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_emulation import Loop, laptop
from test_local_agents import Recorder

from expsched.agent.docker import DockerRuntime
from expsched.api.app import create_app
from expsched.api.emulations import Emulations
from expsched.api.history import History
from expsched.api.local_agents import LocalAgents
from expsched.api.store import RunStore
from expsched.emulate.agents import AgentRegistry

LOOPBACK = ("127.0.0.1", 50000)


def test_non_ascii_agent_token_is_refused_not_a_server_error(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    with TestClient(create_app(store=RunStore(tmp_path)), raise_server_exceptions=False) as client:
        body = {"protocol": 1, "agent_version": "x", "runtime": "fake", "capacity": {}, "max_containers": 1}
        reply = client.post(
            "/v1/agents/a/hello", json=body, headers={"authorization": "Bearer t\xe9".encode("latin-1")}
        )
        assert reply.status_code == 403


def test_local_agents_are_started_and_stopped_only_from_this_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    recorder = Recorder()
    app = create_app(store=RunStore(tmp_path / "runs"))
    app.state.local_agents = LocalAgents(tmp_path / "agents", check=recorder.check, start=recorder.spawn)
    with TestClient(app, client=("10.0.0.7", 40000)) as remote:
        assert remote.post("/v1/local-agents", json={"id": "box", "runtime": "fake"}).status_code == 403
        assert remote.delete("/v1/local-agents/box").status_code == 403
    assert recorder.spawned == []
    with TestClient(app, client=LOOPBACK) as local:
        assert local.post("/v1/local-agents", json={"id": "box", "runtime": "fake"}).status_code == 201
        assert local.delete("/v1/local-agents/box").status_code == 204


def test_two_simultaneous_starts_of_one_agent_spawn_one_process(tmp_path):
    recorder = Recorder()
    gate = threading.Event()

    def slow_check(args, env):
        gate.wait(2)  # обе проверки идут одновременно
        return recorder.check(args, env)

    agents = LocalAgents(tmp_path, check=slow_check, start=recorder.spawn, limit=1)
    outcomes = []

    def start(agent_id):
        try:
            agents.start(agent_id, "fake", {}, "http://127.0.0.1:1")
            outcomes.append("started")
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=start, args=(i,)) for i in ("box", "box", "other")]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    try:
        assert sorted(outcomes) == ["refused", "refused", "started"] and len(recorder.spawned) == 1
    finally:
        agents.close()


def test_finished_runs_on_agents_are_not_kept_forever(tmp_path):
    manager = Emulations(AgentRegistry(), RunStore(tmp_path))
    manager.keep = 3
    for i in range(6):
        manager.live[f"{i:016x}"] = SimpleNamespace(emulation=SimpleNamespace(status="done"))
    manager.live["f" * 16] = SimpleNamespace(emulation=SimpleNamespace(status="running"))
    manager.prune()
    assert list(manager.live) == ["0000000000000003", "0000000000000004", "0000000000000005", "f" * 16]


def test_model_time_of_a_finished_run_stops(tmp_path):
    loop = Loop(tmp_path, laptop())
    emulation = loop.run()
    assert emulation.status == "done"
    end = emulation.now()
    loop.now += 100
    assert emulation.now() == end


def test_task_environment_cannot_override_what_the_agent_tells_the_workload(tmp_path):
    from test_docker_runtime import Cli, command

    cli = Cli(run=(0, "abc\n", ""))
    spec = {"image": "expsched-workload:local", "env": {"EXPSCHED_RESUME_FROM": "0", "EXPSCHED_TOTAL": "9"}}
    DockerRuntime("box", run=cli).start(command(container=spec, resume_from=4), tmp_path)
    args = cli.calls[0]
    env = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert "EXPSCHED_RESUME_FROM=4" in env and "EXPSCHED_RESUME_FROM=0" not in env
    assert "EXPSCHED_TOTAL=9" in env  # своё окружение задачи остаётся


@pytest.mark.parametrize(
    "body",
    ['{"observations": {"k": [{"x": 1}]}}', "[1, 2]", '{"observations": {"k": 5}}', '{"observations": []}'],
)
def test_damaged_history_is_ignored_not_fatal(tmp_path, body):
    path = tmp_path / ".estimates.json"
    path.write_text(body, encoding="utf-8")
    assert History(path).load() == {}


def test_agent_journal_and_memory_stay_bounded_and_survive_a_restart(tmp_path):
    # Долгоживущий агент: каждая попытка дописывала журнал и оставалась в памяти навсегда
    from expsched.agent.core import Agent
    from expsched.agent.runtime import FakeRuntime
    from expsched.contract.agent import EventsAck, PollReply

    def make(runtime):
        return Agent(
            "box", runtime, tmp_path / "agent", {"cpu": 8, "ram_mb": 16384}, now=lambda: "T", compact_at=40
        )

    def start(task, command_id, work=2):
        return {
            "run_id": "0123456789abcdef", "task": task, "attempt": 1, "kind": "start",
            "command_id": command_id, "worker": "w", "limits": {"cpu": 1}, "work": work,
            "checkpoint_interval": 1, "time_scale_ms": 10,
        }  # fmt: skip

    runtime = FakeRuntime()
    agent = make(runtime)
    for i in range(1, 61):
        agent.handle(PollReply.model_validate({"commands": [start(f"t{i}", i)]}))
        runtime.advance(30)
        agent.watch()
        agent.acked(EventsAck(next_event_seq=agent.next_seq))
    agent.handle(PollReply.model_validate({"commands": [start("long", 61, work=50)]}))
    runtime.advance(55)  # 5 единиц из 50: чекпоинт на 5
    agent.watch()
    lines = agent.journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 60 and len(agent.attempts) <= 2

    again = make(runtime)  # перезапуск по уплотнённому журналу
    assert (again.last_command, again.next_seq) == (agent.last_command, agent.next_seq)
    assert [e.seq for e in again.outbox] == [e.seq for e in agent.outbox]
    assert [r.task for r in again.hello().running] == ["long"]
    long = again.attempts["0123456789abcdef:long:1"]
    assert long.progress == 5 and long.container_id is not None


def test_history_spread_is_bounded_like_the_input_one():
    # Две непохожие попытки давали σ ≈ 9,8 и ожидаемую работу 5·10²³ — горизонт cpsat за int64
    from expsched.contract.io_models import MAX_SPREAD, TaskIn
    from expsched.estimate.history import Observation, fit

    task = TaskIn(id="t", kind="train", resources={"gpu": 1})
    found = fit([Observation(1, 1, "model"), Observation(1_000_000, 1, "model")], task, 1000)
    assert found is not None and found.spread == MAX_SPREAD and found.work() < 10**9


def test_without_a_token_only_the_servers_own_agents_connect(tmp_path, monkeypatch):
    # Раньше без EXPSCHED_AGENT_TOKEN локальные агенты, демо и прогоны на агентах отвечали 403
    monkeypatch.delenv("EXPSCHED_AGENT_TOKEN", raising=False)
    envs = []
    recorder = Recorder()

    def spawn(args, env, log):
        envs.append(env)
        return recorder.spawn(args, env, log)

    app = create_app(store=RunStore(tmp_path / "runs"))
    app.state.local_agents = LocalAgents(tmp_path / "agents", check=recorder.check, start=spawn)
    hello = {"protocol": 1, "agent_version": "x", "runtime": "fake", "capacity": {}, "max_containers": 1}
    with TestClient(app, client=LOOPBACK) as client:
        assert client.post("/v1/local-agents", json={"id": "box", "runtime": "fake"}).status_code == 201
        token = envs[0]["EXPSCHED_AGENT_TOKEN"]
        assert len(token) >= 32
        own = client.post("/v1/agents/box/hello", json=hello, headers={"authorization": f"Bearer {token}"})
        assert own.status_code == 200
        guess = client.post("/v1/agents/x/hello", json=hello, headers={"authorization": "Bearer "})
        assert guess.status_code == 403


def test_lower_bound_of_a_run_on_agents_stays_below_its_makespan(tmp_path):
    # Уточнения меняют длительность задачи (у известной — «ещё половина прошедшего», когда окончание
    # приходит с задержкой опроса); граница считалась по ним и выходила больше факта: 46 при makespan 43
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "load_agents.py"
    spec = importlib.util.spec_from_file_location("load_agents", path)
    load = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(load)
    report = load.run(load.problem(4, 50, 1), "heft", 50, tmp_path)
    assert report["status"] == "OK" and report["lower_bound"] <= report["makespan"]


def test_shadow_does_not_call_a_chaos_node_failure_manual(tmp_path):
    # Узел, уроненный хаосом, попадал в список ручных отказов: «План и факт» писал «ручной отказ узла»
    from test_chaos_demo import DEMO, run_demo

    from expsched.contract.io_models import load

    emulation = run_demo(tmp_path, load(DEMO.read_bytes()))
    shadow = emulation.result.result["shadow"]
    assert any(r["type"] == "WORKER_DOWN" for r in emulation.result.events)
    assert shadow["causes"]["manual"] == 0 and shadow["causes"]["failure"] > 0
