import json
import math
from pathlib import Path

import pytest
from expsched_ui import tables

from expsched.contract.io_models import ProblemIn

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
SETTINGS = {"policy": "priority", "aging": None, "validation_mode": "lenient", "time_unit": "hour"}


def scenario(name):
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def rebuild(problem, settings=SETTINGS):
    return tables.build_problem(
        tables.workers_rows(problem),
        tables.tasks_rows(problem),
        tables.submits_rows(problem),
        tables.worker_events_rows(problem),
        settings,
    )


@pytest.mark.parametrize("name", ["demo", "urgent_failure"])
def test_scenario_survives_a_round_trip_through_the_tables(name):
    original = ProblemIn.model_validate(scenario(name))
    rebuilt = ProblemIn.model_validate(rebuild(scenario(name)))
    assert rebuilt.cluster == original.cluster
    assert rebuilt.tasks == original.tasks
    assert rebuilt.events == original.events  # и отказы узлов, и подача задач
    assert rebuilt.time_unit == "hour" and rebuilt.policy.name == "priority"


def test_worker_events_are_shown_as_down_and_up():
    assert tables.worker_events_rows(scenario("failure")) == [
        {"time": 5, "worker": "node-A", "event": "down"},
        {"time": 15, "worker": "node-A", "event": "up"},
    ]


def test_incomplete_worker_event_rows_are_skipped():
    rows = [{"time": 1.0, "worker": "n", "event": None}, {"time": 2.0, "worker": math.nan, "event": "down"}]
    assert tables.build_problem([], [], [], rows, SETTINGS)["events"] == []


def test_cells_from_the_editor_are_normalised():
    # редактор отдаёт числа как float, пустые ячейки как NaN или None
    row = {
        "id": " a ",
        "kind": None,
        "duration": 3.0,
        "gpu": 2.0,
        "cpu": math.nan,
        "ram_gb": 0,
        "priority": math.nan,
        "deadline": math.nan,
        "release_time": None,
        "dependencies": "b, ,c",
        "preemptible": math.nan,
        "checkpoint_interval": 2.0,
        "max_retries": 0.0,
        "constraints": "gpu_type=a100, zone",
    }
    (task,) = tables.build_problem([], [row], [], [], SETTINGS)["tasks"]
    assert task == {
        "id": "a",
        "kind": "other",
        "duration": 3,
        "resources": {"gpu": 2},
        "priority": 1,
        "release_time": 0,
        "dependencies": ["b", "c"],
        "preemptible": False,
        "constraints": {"gpu_type": "a100", "zone": ""},
        "checkpoint_interval": 2,
        "max_retries": 0,
    }


def test_rows_without_an_id_are_skipped():
    problem = tables.build_problem(
        [{"id": math.nan}], [{"id": None, "duration": 1}], [{"id": ""}], [], SETTINGS
    )
    assert problem["cluster"]["workers"] == [] and problem["tasks"] == [] and problem["events"] == []


def test_settings_become_the_policy_block():
    problem = tables.build_problem([], [], [], [], {**SETTINGS, "policy": "fifo", "aging": 30})
    assert problem["policy"] == {"name": "fifo", "aging": 30, "validation_mode": "lenient"}


def test_empty_problem_is_valid_input():
    ProblemIn.model_validate(tables.EMPTY_PROBLEM)
