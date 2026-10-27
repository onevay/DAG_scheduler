import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from expsched.contract.io_models import load, to_domain
from expsched.contract.limits import InputTooLarge
from expsched.contract.schemas import SCHEMAS
from expsched.domain.models import EvType
from expsched.domain.resources import Resources

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def problem(**over):
    base = {
        "cluster": {"workers": [{"id": "node-A", "resources": {"gpu": 4}, "labels": {"gpu_type": "a100"}}]},
        "tasks": [
            {"id": "prep", "duration": 2, "resources": {"gpu": 1}},
            {
                "id": "train",
                "duration": 8,
                "resources": {"gpu": 4},
                "priority": 5,
                "dependencies": ["prep"],
                "checkpoint_interval": 2,
                "constraints": {"gpu_type": "a100"},
            },
        ],
    }
    return json.dumps({**base, **over}).encode()


def test_minimal_problem_gets_defaults():
    p = load(problem())
    assert p.schema_version == "1.0" and p.mode == "simulate"
    assert p.policy.name == "priority_backfill" and p.policy.validation_mode == "lenient"


def test_to_domain_maps_fields():
    d = to_domain(load(problem(events=[{"time": 5, "type": "worker_down", "worker": "node-A"}])))
    train = d.tasks["train"]
    assert train.deps == ("prep",) and train.ckpt == 2 and train.release == 0
    assert train.resources == Resources({"gpu": 4})
    assert train.constraints == (("gpu_type", "a100"),)
    assert d.workers["node-A"].labels == (("gpu_type", "a100"),)
    assert d.events[0].type is EvType.WORKER_DOWN and d.events[0].worker == "node-A"
    assert d.eligible(train) == ("node-A",)


def test_online_retry_fields_are_canonical_and_map_to_domain():
    raw = problem(
        tasks=[
            {
                "id": "task",
                "duration": 1,
                "retry_exit_codes": [42, 137],
                "retry_backoff": 5,
                "max_retries": 3,
            }
        ]
    )
    task = to_domain(load(raw)).tasks["task"]
    assert task.retry_exit_codes == (42, 137)
    assert task.retry_backoff == 5
    assert task.max_retries == 3

    for retry_exit_codes in ([42, 42], [137, 42], [0], [256], [True]):
        with pytest.raises(ValidationError):
            load(
                problem(
                    tasks=[
                        {
                            "id": "task",
                            "duration": 1,
                            "retry_exit_codes": retry_exit_codes,
                        }
                    ]
                )
            )


def test_submit_event_carries_task():
    ev = {"time": 9, "type": "submit_task", "task": {"id": "urgent", "duration": 3, "priority": 10}}
    d = to_domain(load(problem(events=[ev])))
    assert d.events[0].type is EvType.SUBMIT and d.events[0].task is not None
    assert d.events[0].task.id == "urgent"


@pytest.mark.parametrize(
    "raw",
    [
        problem(unknown_field=1),  # E001: лишние поля запрещены
        problem(tasks=[{"id": "t", "duration": 0}]),  # E070
        problem(tasks=[{"id": "t", "duration": 1, "priority": 11}]),
        problem(tasks=[{"id": "t", "duration": 1, "typo": 1}]),
        problem(tasks=[{"id": "t", "duration": 1}, {"id": "t", "duration": 2}]),  # дубликат id
        problem(tasks=[{"id": "bad id", "duration": 1}]),
        problem(tasks=[{"id": "t", "duration": 1, "resources": {"GPU": 1}}]),  # ключ ресурса
        problem(cluster={"workers": []}),
        problem(events=[{"time": 1, "type": "worker_down"}]),  # нет worker
        problem(events=[{"time": 1, "type": "submit_task"}]),  # нет task
        problem(events=[{"time": 1, "type": "cancel_task", "worker": "node-A"}]),  # неизвестный тип события
        b"{not json",
    ],
)
def test_invalid_input_is_rejected(raw):
    with pytest.raises(ValidationError):
        load(raw)


def test_oversized_body_is_rejected_before_parsing():
    with pytest.raises(InputTooLarge):
        load(b"x" * 11, max_bytes=10)


def test_self_dependency_and_duplicates_pass_schema():
    # Это диагностики графа (E011, W060), а не ошибки схемы.
    raw = problem(tasks=[{"id": "a", "duration": 1, "dependencies": ["a", "a"]}])
    assert load(raw).tasks[0].dependencies == ["a", "a"]


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_committed_schemas_are_up_to_date(name):
    # То же, что проверяет CI: `make schema` не должен менять файлы.
    assert (SCHEMA_DIR / name).read_bytes().decode("utf-8") == SCHEMAS[name]() + "\n"
