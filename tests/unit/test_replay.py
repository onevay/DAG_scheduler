"""Replay: журнал прогона, проигранный его же фактами, даёт те же решения политики."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_emulation import Loop, laptop

from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.contract.io_models import load
from expsched.emulate.replay import epochs, replay
from expsched.pipeline import run

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def stored(name, policy):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    config = problem.policy.model_copy(update={"name": policy})
    return problem.model_copy(update={"policy": config}), run(problem, config).events


@pytest.mark.parametrize(
    "policy", ["fifo", "priority_backfill", pytest.param("cpsat", marks=pytest.mark.slow)]
)
@pytest.mark.parametrize("name", sorted(p.stem for p in SCENARIOS.glob("*.json")))
def test_simulated_run_replays_to_the_same_journal(name, policy):
    verdict = replay(*stored(name, policy))
    assert verdict["same"] and verdict["same_journal"] and verdict["divergence"] is None


def test_run_on_agents_with_failures_replays_to_the_same_decisions(tmp_path):
    loop = Loop(tmp_path, laptop(mode="emulate"))
    while not loop.runtime.containers:
        loop.step()
    loop.runtime.oom(sorted(loop.runtime.containers)[0])
    for _ in range(60):
        loop.step()
    loop.emulation.drop("laptop")
    for _ in range(10):
        loop.step()
    loop.emulation.restore("laptop")
    emulation = loop.run()
    assert emulation.result is not None
    verdict = replay(laptop(mode="emulate"), emulation.result.events)
    assert verdict["same"] and verdict["same_journal"] and verdict["decisions"] > 6


def test_other_policy_is_caught_at_its_first_different_decision():
    problem, log = stored("rush_hour", "fifo")  # журнал FIFO, а проигрываем priority
    verdict = replay(
        problem.model_copy(update={"policy": problem.policy.model_copy(update={"name": "priority"})}), log
    )
    divergence = verdict["divergence"]
    assert not verdict["same"] and divergence is not None
    assert divergence["expected"] != divergence["got"] and divergence["expected"]["type"] == "START"
    starts = [r for r in log if r["type"] == "START"]
    assert divergence["index"] < len(starts)


def test_fact_after_decisions_of_the_same_moment_is_a_new_epoch():
    log = [
        {"t": 3, "type": "FINISH", "task": "a", "attempt": 1},
        {"t": 3, "type": "START", "task": "b", "worker": "n", "attempt": 1},
        {"t": 3, "type": "FINISH", "task": "c", "attempt": 1},
        {"t": 4, "type": "WORKER_DOWN", "worker": "n"},
    ]
    assert [(t, len(facts)) for t, facts in epochs(log)] == [(3, 1), (3, 1), (4, 1)]


def test_replay_over_the_api(tmp_path):
    client = TestClient(create_app(store=RunStore(tmp_path)))
    problem = json.loads((SCENARIOS / "urgent_failure.json").read_text(encoding="utf-8"))
    run_id = client.post("/v1/runs", json={"problem": problem}).json()["run_id"]
    verdict = client.post(f"/v1/runs/{run_id}/replay").json()
    assert verdict == {
        "same": True,
        "same_journal": True,
        "decisions": verdict["decisions"],
        "divergence": None,
    }
    assert client.post("/v1/runs/0123456789abcdef/replay").status_code == 404
    assert client.post("/v1/runs", json={"problem": {**problem, "mode": "replay"}}).status_code == 400
