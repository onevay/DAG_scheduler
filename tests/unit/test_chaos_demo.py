"""Демо сбоев: две машины разной скорости на агентах fake, неизвестные длительности, хаос — до OK."""

import json
from pathlib import Path

from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.contract.io_models import load
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.driver import Emulation
from expsched.emulate.replay import replay
from expsched.estimate.history import pinned
from expsched.validation import validate

DEMO = Path(__file__).resolve().parents[2] / "scenarios" / "chaos_demo.json"
STEP_MS = 50
CAPACITY = {"cpu": 16, "ram_mb": 65536, "gpu": 2}


def run_demo(tmp_path, problem):
    now = [0.0]
    registry = AgentRegistry(clock=lambda: now[0], heartbeat_s=1)
    machines = {}
    for name in ("demo-fast", "demo-slow"):
        runtime = FakeRuntime()
        agent = Agent(name, runtime, tmp_path / name, CAPACITY, now=lambda: "T")
        agent.welcome(registry.hello(name, agent.hello()))
        machines[name] = (agent, runtime)
    emulation = Emulation("0123456789abcdef", problem, problem.policy, registry, clock=lambda: now[0])
    for _ in range(20_000):
        if emulation.status != "running":
            break
        emulation.tick()
        for name, (agent, runtime) in sorted(machines.items()):
            agent.handle(registry.poll(name, agent.poll(0)))
            runtime.advance(STEP_MS)
            agent.watch()
            if batch := agent.batch():
                agent.acked(registry.events(name, batch))
        now[0] += STEP_MS / 1000
    return emulation


def test_demo_is_valid_and_has_retries_for_its_chaos():
    _, diagnostics = validate(DEMO.read_bytes(), "strict")
    assert diagnostics == []


def test_demo_survives_its_chaos_on_two_machines_of_different_speed(tmp_path):
    problem = load(DEMO.read_bytes())
    emulation = run_demo(tmp_path, problem)
    result = emulation.result.result
    assert result["status"] == "OK" and result["violations"] == []
    events = emulation.result.events
    crashes = [r for r in events if r["type"] == "INTERRUPTED" and r["cause"] != "PREEMPT"]
    assert crashes and any(r["type"] == "WORKER_DOWN" for r in events)
    assert result["shadow"]["fact_makespan"] > result["shadow"]["plan_makespan"]  # сбои стоят времени

    # быстрый узел и правда вдвое быстрее: законченная попытка там длится половину своей работы
    fast = [
        a
        for a in result["attempts"]
        if a["worker"] == "fast" and a["outcome"] == "DONE" and a["task"] != "report"
    ]
    real = {t.id: int(t.container.env["EXPSCHED_TOTAL"]) for t in problem.tasks if t.container}
    assert fast and all(
        abs((a["end"] - a["start"]) * 2 - (real[a["task"]] - a["resume_from"])) <= 2 for a in fast
    )

    same = replay(problem, events, pinned(problem, result["estimates"]))
    assert same["same"] and same["same_journal"]


def test_attempt_cut_in_the_step_that_started_it_is_not_sent(tmp_path, monkeypatch):
    # Крупный шаг часов: старт и отказ узла хаосом попадают в один тик драйвера — раньше падало KeyError
    import test_chaos_demo

    monkeypatch.setattr(test_chaos_demo, "STEP_MS", 130)
    raw = load(DEMO.read_bytes()).model_dump(mode="json")
    for seed in (9, 16, 22):
        raw["chaos"] |= {"crash_every": 10, "node_down_every": 30, "seed": seed}
        emulation = run_demo(tmp_path / str(seed), load(json.dumps(raw).encode()))
        assert emulation.status == "done" and emulation.result.result["violations"] == []
