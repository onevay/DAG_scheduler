"""Движок с внешними часами: окончания и сбои попыток подаются извне, сохранённое — по чекпоинтам агента."""

from pathlib import Path

import pytest

from expsched.contract.io_models import load, to_domain
from expsched.contract.output_models import EVENT
from expsched.domain.models import Event, EvType
from expsched.engine.events import Crash, Finish
from expsched.engine.executor import External, Simulated
from expsched.engine.simulator import Simulator
from expsched.policies.registry import make_policy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def scenario(name):
    problem = load((SCENARIOS / f"{name}.json").read_bytes())
    return to_domain(problem), problem.policy


def drive(sim, until=10_000):
    """Агент, у которого попытки идут ровно прогноз: окончание каждой подаётся в её прогнозный момент."""
    known = set()
    while (t := sim.next_time()) is not None and t <= until:
        sim.advance(t)
        for key, run in sorted(sim.state.run.items()):
            if (key, run.attempt) not in known:
                known.add((key, run.attempt))
                sim.inject(run.end_pred, Finish(key, run.attempt))
    sim._finalize()
    return sim


@pytest.mark.parametrize("name", ["demo", "backfill", "fragmentation", "sweep", "hetero_gpus", "staggered"])
def test_external_finishes_at_the_forecast_reproduce_the_simulated_journal(name):
    problem, policy = scenario(name)
    simulated = Simulator(problem, make_policy(policy), forecast=True)
    simulated.run_all()
    driven = drive(Simulator(problem, make_policy(policy), forecast=True, executor=External()))
    assert driven.journal.result_hash() == simulated.journal.result_hash()


def test_default_executor_is_the_simulation():
    problem, policy = scenario("demo")
    assert isinstance(Simulator(problem, make_policy(policy)).executor, Simulated)


def started(sim, key):
    return [r for r in sim.journal.records if r["type"] == "START" and r["task"] == key]


def interrupted(sim):
    return [
        (r["task"], r["cause"], r["saved"], r["remaining"])
        for r in sim.journal.records
        if r["type"] == "INTERRUPTED"
    ]


def test_crashed_attempt_uses_a_retry_and_starts_again(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 5, ckpt=2, max_retries=1)])
    sim = Simulator(problem, greedy, executor=External())
    sim.advance(0)
    sim.executor.checkpoint("a", 1, 2)  # агент сообщил чекпоинт: сохранено 2
    sim.inject(3, Crash("a", 1, "OOM", "container exceeded its memory limit"))
    sim.advance(3)
    assert interrupted(sim) == [("a", "OOM", 2, 3)]
    assert [(r["t"], r["attempt"], r["resume_from"]) for r in started(sim, "a")] == [(0, 1, 0), (3, 2, 2)]
    sim.inject(4, Crash("a", 1, "FAILED"))  # про уже прерванную попытку — пропускается
    sim.inject(5, Crash("a", 2, "FAILED", "exit code 1"))
    sim.advance(5)
    assert sim.state.status["a"].value == "FAILED"  # ретрай был один
    for record in sim.journal.records:
        EVENT.validate_python(record)  # FAILED и OOM — допустимые причины в журнале


def test_node_failure_keeps_what_the_agent_checkpointed(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 10, ckpt=4)], events=[Event(6, EvType.WORKER_DOWN, worker="n0")])
    sim = Simulator(problem, greedy, executor=External())
    sim.advance(0)
    sim.executor.checkpoint("a", 1, 5)  # агент сохранил больше, чем дал бы интервал задачи
    sim.executor.checkpoint("a", 1, 3)  # опоздавшее меньшее значение не в счёт
    sim.advance(6)
    assert interrupted(sim) == [("a", "WORKER_DOWN", 5, 5)]


def test_clock_only_moves_forward_and_advance_stops_at_its_limit(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 2), make_task("b", 2, release=5)])
    sim = Simulator(problem, greedy, executor=External())
    sim.advance(3)
    assert sim.t == 0 and sim.next_time() == 5  # окончания a движок не знает: ждёт факта или release b
    with pytest.raises(ValueError, match="earlier"):
        sim.inject(-1, Finish("a", 1))
    sim.inject(2, Finish("a", 1))
    sim.advance(4)
    assert sim.t == 2 and sim.state.status["a"].value == "SUCCEEDED" and sim.next_time() == 5


def test_forecast_of_external_attempts_uses_their_predicted_ends(make_task, make_problem, greedy):
    tasks = [make_task("a", 4, gpu=4), make_task("b", 1, gpu=4)]
    problem = make_problem(tasks, events=[Event(1, EvType.WORKER_UP, worker="n0")])
    sim = Simulator(problem, greedy, executor=External())
    sim.advance(0)
    assert sim.forecast() == {"b": 4}  # a идёт до 4 по прогнозу, b стартует следом
