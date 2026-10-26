import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from expsched.api.app import create_app
from expsched.api.runner import ProcessRunner, RunFailed, RunQueueFull, RunTimeout, _limit_memory
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


def test_cpsat_runs_in_a_worker_process(runner):
    pytest.importorskip("ortools")
    problem = ProblemIn.model_validate(DEMO)
    outcome = asyncio.run(runner.run(problem, PolicyIn(name="cpsat")))
    assert outcome.result["status"] == "OK" and outcome.result["violations"] == []


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


def test_queue_takes_all_or_nothing_and_frees_places():
    runner = ProcessRunner(workers=1, queue_size=3)
    with runner.reserve(2):
        with pytest.raises(RunQueueFull), runner.reserve(2):
            pass
        with runner.reserve(1):
            pass
    with runner.reserve(3):  # места вернулись
        pass
    runner.close()


def test_memory_limit_is_skipped_where_the_os_has_none():
    assert _limit_memory(None) is False
    if sys.platform == "win32":
        assert _limit_memory(1024) is False


@pytest.mark.skipif(sys.platform == "win32", reason="на Windows предела памяти процесса нет")
def test_worker_over_its_memory_limit_fails_instead_of_eating_the_machine():
    # Предел ставится уже запущенному процессу: маленький вход может уложиться в выделенную ранее память,
    # а вход в несколько мегабайт — нет
    runner = ProcessRunner(timeout=30, workers=1, memory_mb=64)
    big = ProblemIn.model_validate(wide(20_000))
    with pytest.raises(RunFailed):
        asyncio.run(runner.run(big, big.policy))
    runner.close()


def test_server_keeps_answering_during_a_heavy_validation(tmp_path):
    app = create_app(store=RunStore(tmp_path), runner=ProcessRunner(workers=1))
    big = wide(60_000)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api", timeout=120) as client:
            check = asyncio.create_task(client.post("/v1/validate", json=big))
            await asyncio.sleep(0.05)
            policies = await client.get("/v1/policies")
            finished_first = check.done()
            return policies.status_code, finished_first, (await check).status_code

    assert asyncio.run(scenario()) == (200, False, 200)  # проверка большого входа не держит остальные запросы
    app.state.runner.close()
