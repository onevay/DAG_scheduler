import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.runner import ProcessRunner, RunTimeout
from expsched.api.store import RunStore
from expsched.contract.io_models import PolicyIn, ProblemIn

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
DEMO = json.loads((SCENARIOS / "demo.json").read_text(encoding="utf-8"))


def wide(n):
    """Широкий DAG: много готовых задач одновременно, прогон идёт секунды."""
    tasks = [
        {
            "id": f"t{i}",
            "duration": 1 + i % 5,
            "resources": {"gpu": 1 + i % 2},
            "dependencies": [f"t{i // 2}"] if i else [],
        }
        for i in range(n)
    ]
    workers = [{"id": f"n{i}", "resources": {"gpu": 4}} for i in range(4)]
    return {"cluster": {"workers": workers}, "tasks": tasks, "policy": {"name": "priority"}}


@pytest.fixture(scope="module")
def runner():
    runner = ProcessRunner(timeout=60, workers=2)
    yield runner
    runner.close()


def test_run_happens_in_a_worker_process_that_is_reused(runner):
    problem = ProblemIn.model_validate(DEMO)
    first = asyncio.run(runner.run(problem, problem.policy))
    second = asyncio.run(runner.run(problem, problem.policy))
    assert first.result["metrics"]["makespan"] == 21 and first == second
    worker = runner._idle.get_nowait()
    assert runner._idle.empty()  # оба прогона прошли в одном процессе
    runner._idle.put(worker)


def test_errors_of_the_run_come_back(runner):
    problem = ProblemIn.model_validate(DEMO)
    with pytest.raises(NotImplementedError, match="cpsat"):
        asyncio.run(runner.run(problem, PolicyIn(name="cpsat")))


def test_run_over_the_timeout_is_killed():
    runner = ProcessRunner(timeout=0.3, workers=1)
    problem = ProblemIn.model_validate(wide(3000))
    with pytest.raises(RunTimeout):
        asyncio.run(runner.run(problem, problem.policy))
    assert runner._idle.empty()  # зависший процесс не вернулся в очередь
    runner.timeout = 60
    demo = ProblemIn.model_validate(DEMO)
    assert asyncio.run(runner.run(demo, demo.policy)).result["status"] == "OK"  # следующий — в новом процессе
    runner.close()


def test_api_answers_504_on_timeout(tmp_path):
    app = create_app(store=RunStore(tmp_path), runner=ProcessRunner(timeout=0.3, workers=1))
    response = TestClient(app).post("/v1/runs", json={"problem": wide(3000)})
    assert response.status_code == 504 and response.json()["error"]["code"] == "RUN_TIMEOUT"
    assert not any(tmp_path.iterdir())  # прогон, который не закончился, не сохраняется
    app.state.runner.close()


def test_server_keeps_answering_during_a_long_run(tmp_path):
    app = create_app(store=RunStore(tmp_path), runner=ProcessRunner(timeout=60, workers=1))

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            long_run = asyncio.create_task(client.post("/v1/runs", json={"problem": wide(1500)}))
            await asyncio.sleep(0.05)
            policies = await client.get("/v1/policies")
            finished_first = long_run.done()
            return policies.status_code, finished_first, (await long_run).status_code

    assert asyncio.run(scenario()) == (200, False, 201)  # справка ответила, пока прогон ещё шёл
    app.state.runner.close()
