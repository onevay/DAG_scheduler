import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.store import RunStore
from expsched.api.views import explain_view
from expsched.contract.io_models import load
from expsched.engine.simulator import Simulator
from expsched.explain.idle import idle_by_code, idle_intervals
from expsched.metrics.metrics import metrics
from expsched.pipeline import run
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.fifo import FifoPolicy
from expsched.policies.preemption import PreemptivePolicy
from expsched.verify.attempts import attempts_from_log

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def result_of(scenario, **policy):
    problem = load((SCENARIOS / f"{scenario}.json").read_bytes())
    return run(problem, problem.policy.model_copy(update=policy)).result


def spans(intervals, worker):
    return [
        (i["from"], i["to"], i["code"], i["free"].get("gpu", 0)) for i in intervals if i["worker"] == worker
    ]


def test_idle_after_a_failure():
    idle = result_of("urgent_failure")["explain"]["idle"]
    assert idle["by_code"]["gpu"] == {
        "IDLE_DOWN": 40,  # node-A, 4 GPU с 5 до 15
        "IDLE_NO_READY": 89,
        "IDLE_FRAGMENTATION": 0,
        "IDLE_RESERVED": 0,
        "IDLE_POLICY": 0,
    }
    assert (5, 15, "IDLE_DOWN", 4) in spans(idle["intervals"], "node-A")
    assert (13, 15, "IDLE_NO_READY", 2) in spans(idle["intervals"], "node-C")  # ждёт ft_det, а не ресурсы


def test_idle_held_by_a_reservation():
    intervals = result_of("backfill")["explain"]["idle"]["intervals"]
    held = [i for i in intervals if i["code"] == "IDLE_RESERVED"]
    assert [(i["from"], i["to"]) for i in held] == [(0, 1), (1, 3), (3, 4)]  # свободно 2, 1, 2 GPU
    assert all(i["detail"] == {"head": "big", "t_res": 4} for i in held)
    assert result_of("backfill", name="fifo")["explain"]["idle"]["by_code"]["gpu"]["IDLE_RESERVED"] == 0


def test_fragmentation_and_policy(make_task, make_problem):
    # на n0 занято 3 GPU из 4: b просит 2 и не помещается
    tasks = [make_task("a", 2, gpu=3), make_task("b", 1, gpu=2)]
    problem = make_problem(tasks, gpus=(4,))
    intervals = idle_intervals(problem, Simulator(problem, FifoPolicy()).run_all(), 3)
    assert spans(intervals, "n0")[0] == (0, 2, "IDLE_FRAGMENTATION", 1)
    # строгий FIFO стоит за big, которой нет места, и не запускает small, хотя n1 свободен
    tasks = [make_task("a", 3, gpu=4), make_task("big", 1, gpu=4), make_task("small", 1, gpu=1)]
    problem = make_problem(tasks, gpus=(4, 2))
    intervals = idle_intervals(problem, Simulator(problem, FifoPolicy()).run_all(), 5)
    assert spans(intervals, "n1")[0] == (0, 3, "IDLE_POLICY", 2)


@pytest.mark.parametrize("seed", range(30))
def test_busy_and_idle_add_up_to_capacity(random_problem, seed):
    problem = random_problem(seed, failures=True)
    problem = dataclasses.replace(
        problem, tasks={k: dataclasses.replace(t, preemptible=True) for k, t in problem.tasks.items()}
    )
    log = Simulator(problem, PreemptivePolicy(BackfillPolicy(), min_runtime=0)).run_all()
    horizon = metrics(problem, log, None)["makespan"]
    tasks = problem.all_tasks()
    busy = sum(
        (min(a.end, horizon) - a.start) * tasks[a.task].resources.get("gpu")
        for a in attempts_from_log(log)
        if a.end is not None and a.start < horizon
    )
    capacity = sum(w.capacity.get("gpu") for w in problem.workers.values()) * horizon
    idle = idle_by_code(idle_intervals(problem, log, horizon), {"gpu"})["gpu"]
    assert busy + sum(idle.values()) == capacity


def test_explain_view_narrows_every_list():
    result = result_of("urgent_failure")
    task = explain_view(result, task="pretrain_vit")
    assert [(c["attempt"], c["cause"]) for c in task["chain"]] == [(1, "dep"), (2, "fail")]
    assert [a["worker"] for a in task["attempts"]] == ["node-A", "node-B"] and task["idle"] == []
    moment = explain_view(result, worker="node-C", t=14)
    assert moment["waits"] == [] and [(i["from"], i["to"]) for i in moment["idle"]] == [(13, 15)]
    assert moment["chain"] == [] and moment["attempts"] == []  # на node-C в 14 ничего не шло
    urgent = explain_view(result, task="urgent_eval", t=9)
    assert [(w["code"], w["from"], w["to"]) for w in urgent["waits"]] == [("WAIT_DEPS", 9, 10)]
    assert explain_view(result)["decomposition"] == result["explain"]["decomposition"]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(create_app(store=RunStore(tmp_path_factory.mktemp("runs")))) as client:
        yield client


def post(client, scenario, **policy):
    problem = json.loads((SCENARIOS / f"{scenario}.json").read_text(encoding="utf-8"))
    return client.post(
        "/v1/runs", json={"problem": problem, "policy": {**problem["policy"], **policy}}
    ).json()


def test_explain_endpoint(client):
    run_id = post(client, "urgent_failure")["run_id"]
    body = client.get(f"/v1/runs/{run_id}/explain", params={"worker": "node-A", "t": 7}).json()
    assert [(i["from"], i["to"], i["code"]) for i in body["idle"]] == [(5, 15, "IDLE_DOWN")]
    assert client.get(f"/v1/runs/{run_id}/explain").json()["decomposition"]["lost"] == 1


@pytest.mark.parametrize(
    ("params", "status"),
    [({"task": "ghost"}, 404), ({"worker": "node-Z"}, 404), ({"t": -1}, 422)],
)
def test_explain_endpoint_rejects_unknown_names(client, params, status):
    run_id = post(client, "urgent_failure")["run_id"]
    response = client.get(f"/v1/runs/{run_id}/explain", params=params)
    assert response.status_code == status and "error" in response.json()


def test_explain_of_a_run_without_simulation_is_404(client):
    run_id = post(client, "cycle", validation_mode="strict")["run_id"]
    response = client.get(f"/v1/runs/{run_id}/explain")
    assert response.status_code == 404 and "not simulated" in response.json()["error"]["message"]
