import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.contract.io_models import load
from expsched.contract.whatif import Intervention, apply, deltas

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
FAILURE = json.loads((SCENARIOS / "failure.json").read_text(encoding="utf-8"))
CHANGE = TypeAdapter(Intervention)


def change(**fields):
    return CHANGE.validate_python(fields)


def task(problem, key):
    return next(t for t in problem.tasks if t.id == key)


def test_task_fields_are_changed():
    problem = load((SCENARIOS / "failure.json").read_bytes())
    assert (
        task(
            apply(problem, change(kind="set_checkpoint", task="pretrain_vit", checkpoint_interval=1)),
            "pretrain_vit",
        ).checkpoint_interval
        == 1
    )
    assert (
        task(apply(problem, change(kind="set_priority", task="ft_seg", priority=9)), "ft_seg").priority == 9
    )
    assert (
        task(apply(problem, change(kind="set_deadline", task="ft_seg", deadline=None)), "ft_seg").deadline
        is None
    )
    assert task(apply(problem, change(kind="set_preemptible", task="ft_seg")), "ft_seg").preemptible
    assert task(problem, "pretrain_vit").checkpoint_interval == 2  # исходный вход не меняется


def test_task_submitted_by_an_event_is_changed_too():
    problem = load((SCENARIOS / "demo.json").read_bytes())
    changed = apply(problem, change(kind="set_deadline", task="urgent_eval", deadline=20))
    assert next(e.task for e in changed.events if e.task).deadline == 20


def test_workers_and_failures():
    problem = load((SCENARIOS / "urgent_failure.json").read_bytes())
    added = apply(problem, change(kind="add_worker", worker={"id": "node-D", "resources": {"gpu": 4}}))
    assert [w.id for w in added.cluster.workers][-1] == "node-D"
    calm = apply(problem, change(kind="remove_failures", worker="node-A"))
    assert [e.type for e in calm.events] == ["submit_task"]  # подача задачи остаётся


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "set_priority", "task": "ghost", "priority": 5},
        {"kind": "remove_failures", "worker": "node-Z"},
        {"kind": "add_worker", "worker": {"id": "node-A", "resources": {"gpu": 1}}},
    ],
)
def test_changes_to_unknown_names_are_rejected(fields):
    with pytest.raises(ValueError):
        apply(load((SCENARIOS / "failure.json").read_bytes()), change(**fields))


def test_deltas():
    base = {
        "makespan": 22,
        "deadline_misses": {"high": 1, "low": 0},
        "utilization": {"gpu": 0.45, "cpu": None},
        "lost_work": {"gpu": 4},
        "mean_wait": 0.1,
    }
    variant = {
        "makespan": 21,
        "deadline_misses": {"high": 0, "low": 0},
        "utilization": {"gpu": 0.4765, "cpu": None},
        "lost_work": {"gpu": 0},
        "mean_wait": 0,
    }
    assert deltas(base, variant) == {
        "makespan": -1,
        "deadline_misses": -1,
        "mean_wait": -0.1,
        "utilization_gpu": 0.0265,
        "lost_work_gpu": -4,
    }
    assert deltas(None, variant) is None and deltas(base, None) is None


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(create_app(store=RunStore(tmp_path_factory.mktemp("runs")))) as client:
        yield client


def test_what_if_on_a_failure(client):
    interventions = [
        {"kind": "set_checkpoint", "task": "pretrain_vit", "checkpoint_interval": 1},
        {"kind": "remove_failures", "worker": "node-A"},
        {"kind": "add_worker", "worker": {"id": "node-D", "resources": {"gpu": 4}}},
    ]
    response = client.post("/v1/whatif", json={"problem": FAILURE, "interventions": interventions})
    assert response.status_code == 200
    body = response.json()
    assert body["baseline"]["metrics"]["makespan"] == 22
    assert [v["metrics"]["makespan"] for v in body["variants"]] == [21, 21, 22]  # лишний узел не помогает
    assert [v["delta"]["lost_work_gpu"] for v in body["variants"]] == [-4, -4, 0]
    assert body["variants"][0]["intervention"] == interventions[0]
    variant = client.get(
        f"/v1/runs/{body['variants'][0]['run_id']}/problem"
    ).json()  # вариант — обычный прогон
    assert next(t for t in variant["tasks"] if t["id"] == "pretrain_vit")["checkpoint_interval"] == 1


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        (
            {"problem": FAILURE, "interventions": [{"kind": "set_priority", "task": "ghost", "priority": 1}]},
            422,
            "INVALID_REQUEST",
        ),
        ({"problem": FAILURE, "interventions": []}, 422, "SCHEMA_INVALID"),
        ({"problem": FAILURE, "interventions": [{"kind": "teleport"}]}, 422, "SCHEMA_INVALID"),
    ],
)
def test_bad_what_if_requests(client, body, status, code):
    response = client.post("/v1/whatif", json=body)
    assert response.status_code == status and response.json()["error"]["code"] == code
