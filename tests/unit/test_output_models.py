import dataclasses
from pathlib import Path

import pytest
from pydantic import ValidationError

from expsched.contract.io_models import load
from expsched.contract.output_models import EVENT, ResultOut
from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.pipeline import run
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.preemption import PreemptivePolicy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
POLICIES = ["fifo", "fifo_skip", "priority", "priority_backfill"]


def check(result, events):
    ResultOut.model_validate(result)
    for record in events:
        EVENT.validate_python(record)


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("*.json")), ids=lambda p: p.stem)
@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("mode", ["lenient", "strict"])
def test_scenario_output_matches_the_contract(path, policy, mode):
    problem = load(path.read_bytes())
    out = run(problem, problem.policy.model_copy(update={"name": policy, "validation_mode": mode}))
    check(out.result, out.events)


@pytest.mark.parametrize("seed", range(20))
def test_every_kind_of_record_matches_the_contract(random_problem, seed):
    """Отказы, вытеснения, резервы, блокировки и перепланирования на случайных входах."""
    problem = random_problem(seed, failures=True)
    tasks = {k: dataclasses.replace(t, preemptible=True) for k, t in problem.tasks.items()}
    extra = dataclasses.replace(tasks["t0"], id="late")
    events = [*problem.events, Event(3, EvType.SUBMIT, task=extra), Event(4, EvType.SUBMIT, task=extra)]
    problem = dataclasses.replace(problem, tasks=tasks, events=events)  # вторая подача — DUPLICATE_TASK_ID
    log = Simulator(problem, PreemptivePolicy(BackfillPolicy()), forecast=True).run_all()
    for record in log:
        EVENT.validate_python(record)


def test_seen_record_types_cover_the_contract(random_problem):
    seen = set()
    for seed in range(20):
        problem = random_problem(seed, failures=True)
        tasks = {k: dataclasses.replace(t, preemptible=True) for k, t in problem.tasks.items()}
        problem = dataclasses.replace(problem, tasks=tasks)
        seen |= {
            r["type"] for r in Simulator(problem, PreemptivePolicy(BackfillPolicy()), forecast=True).run_all()
        }
    # SUBMIT, REPLAN на подаче и DIAGNOSTIC проверяются сценариями и тестом выше
    assert {"READY", "WAIT", "START", "FINISH", "WORKER_DOWN", "WORKER_UP", "INTERRUPTED", "RESERVE"} <= seen


def test_undocumented_fields_are_rejected():
    out = run(load((SCENARIOS / "demo.json").read_bytes()))
    with pytest.raises(ValidationError):
        ResultOut.model_validate({**out.result, "extra": 1})
    with pytest.raises(ValidationError):
        EVENT.validate_python({**out.events[0], "extra": 1})
