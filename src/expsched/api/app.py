"""REST API: проверка входа, прогон и чтение сохранённых прогонов.

Ошибка всегда приходит в одном формате — `{"error": {"code", "message"}, "diagnostics"?}` (ErrorOut).
Браузерные клиенты с другого адреса перечисляются в EXPSCHED_CORS_ORIGINS через запятую. Прогоны хранятся
в каталоге EXPSCHED_RUNS_DIR (по умолчанию runs/), не больше EXPSCHED_MAX_RUNS (100) штук и не больше
EXPSCHED_MAX_RUNS_MB (2048) мегабайт. Каждый прогон идёт в отдельном процессе: не дольше EXPSCHED_RUN_TIMEOUT
секунд (60), не больше EXPSCHED_RUN_WORKERS (2) одновременно, с пределом памяти EXPSCHED_RUN_MEMORY_MB (8192;
0 — без предела, на Windows предела нет). Идущих и ждущих прогонов — не больше EXPSCHED_RUN_QUEUE (32), сверх
этого — 503. Проверка входа, разбор тел и работа с хранилищем идут вне цикла событий: большой вход не
останавливает остальные запросы.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import APIRouter, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from expsched.api import agents, emulations
from expsched.api.emulations import Emulations
from expsched.api.errors import error_response
from expsched.api.history import History
from expsched.api.local_agents import LocalAgents
from expsched.api.models import (
    ErrorCode,
    ErrorOut,
    EventRequest,
    EventsPage,
    ExplainView,
    LiveStarted,
    PoliciesOut,
    ReplayOut,
    RunOut,
    RunRequest,
    RunsOut,
    ScenariosOut,
    ValidationOut,
    WhatIfOut,
    WhatIfRequest,
)
from expsched.api.runner import ProcessRunner, RunFailed, RunQueueFull, RunTimeout
from expsched.api.store import RUN_ID_PATTERN, RunNotFound, RunOutdated, RunStore, run_id_of
from expsched.api.views import explain_view, input_analysis
from expsched.api.web import add_security_headers, mount_web
from expsched.contract.io_models import PolicyIn, ProblemIn, load
from expsched.contract.limits import MAX_BODY_BYTES, InputTooLarge
from expsched.contract.whatif import apply, deltas
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.replay import replay
from expsched.estimate.history import pinned
from expsched.pipeline import input_hash
from expsched.policies.registry import implemented_policies
from expsched.validation import Code, has_errors
from expsched.validation.schema import schema_diagnostics
from expsched.validation.validate import check

SCHEMA_CODES = frozenset({Code.E001, Code.E070})  # вход не разобран: остальные проверки не выполнялись
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorOut} for status in (400, 403, 404, 409, 413, 422, 500, 503, 504)
}
RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]
# Тела запросов читаются вручную, с обрывом на лимите, поэтому их схемы добавляются в OpenAPI отдельно.
REQUEST_BODIES: dict[str, type[BaseModel]] = {
    "/v1/validate": ProblemIn,
    "/v1/runs": RunRequest,
    "/v1/runs/{run_id}/events": EventRequest,
    "/v1/whatif": WhatIfRequest,
}

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")


@router.get("/policies", response_model=PoliciesOut)
def policies() -> dict[str, Any]:
    """Реализованные политики и параметры по умолчанию — для формы клиента."""
    return {"policies": implemented_policies(), "defaults": PolicyIn().model_dump()}


@router.post("/validate", response_model=ValidationOut, responses=ERRORS)
async def check_problem(request: Request, mode: Literal["strict", "lenient"] = "lenient") -> Any:
    """Диагностики входа. 422 — вход не соответствует схеме, остальные ошибки входа приходят с кодом 200."""
    try:
        body = await _read_body(request)
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    history = request.app.state.emulations.history
    return await asyncio.to_thread(_check, body, mode, history)


def _check(body: bytes, mode: Literal["strict", "lenient"], history: History | None = None) -> Any:
    """Проверка и анализ графа — секунды работы процессора на большом входе, поэтому в отдельном потоке."""
    problem, diagnostics, estimates = check(body, mode, history.estimates if history else None)
    found = [d.to_dict() for d in diagnostics]
    if any(d.code in SCHEMA_CODES for d in diagnostics):
        return error_response(422, "SCHEMA_INVALID", "input does not match the schema", found)
    analysis = input_analysis(problem, diagnostics) if problem is not None else None
    return {
        "valid": not has_errors(diagnostics),
        "diagnostics": found,
        "analysis": analysis,
        "estimates": {k: e.to_dict() for k, e in estimates.items()},
    }


@router.get("/scenarios", response_model=ScenariosOut)
def scenarios(request: Request) -> Any:
    """Примеры входа: имя и размер, чтобы выбрать, с чего начать."""
    out = []
    for name, problem in _scenarios(request).items():
        submitted = [e for e in problem.events if e.task is not None]
        out.append(
            {
                "name": name,
                "tasks": len(problem.tasks) + len(submitted),
                "workers": len(problem.cluster.workers),
                "events": len(problem.events),
                "policy": problem.policy.name,
                "time_unit": problem.time_unit,
            }
        )
    return {"scenarios": out}


@router.get("/scenarios/{name}", response_model=ProblemIn, responses=ERRORS)
def scenario(request: Request, name: Annotated[str, Path(pattern=r"^[a-z0-9_-]{1,64}$")]) -> Any:
    """Вход примера; без собственного `name` получает имя файла."""
    problem = _scenarios(request).get(name)
    if problem is None:
        return error_response(404, "NOT_FOUND", f"no scenario {name!r}")
    return problem.model_copy(update={"name": problem.name or name}).model_dump(mode="json")


@router.get("/runs", response_model=RunsOut)
def runs(request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> Any:
    """Последние сохранённые прогоны, новые первыми."""
    return {"runs": _store(request).recent(limit)}


@router.post(
    "/runs", status_code=201, response_model=RunOut, responses={**ERRORS, 202: {"model": LiveStarted}}
)
async def create_run(request: Request) -> Any:
    """Прогон {problem, policy?}. Тот же вход с той же политикой не пересчитывается: отдаётся сохранённый.

    С `problem.mode` `emulate` или `shadow` (план рядом) — прогон на агентах: 202 `{run_id, status}`, идёт
    в фоне, его состояние — `GET /v1/runs/{run_id}/containers`, результат появится по окончании.
    """
    try:
        run_request = await asyncio.to_thread(RunRequest.model_validate_json, await _read_body(request))
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    except ValidationError as error:
        found = [d.to_dict() for d in schema_diagnostics(error)]
        return error_response(422, "SCHEMA_INVALID", "request does not match the schema", found)
    problem, policy = run_request.problem, run_request.policy or run_request.problem.policy
    if problem.mode == "replay":
        return error_response(
            400, "INVALID_REQUEST", "replay a stored run with POST /v1/runs/{run_id}/replay"
        )
    if problem.mode in ("emulate", "shadow"):
        return await _start_emulation(request, problem, policy)
    return await _run_and_store(request, problem, policy)


async def _start_emulation(request: Request, problem: ProblemIn, policy: PolicyIn) -> Any:
    manager: Emulations = request.app.state.emulations
    if manager.running() >= manager.limit:
        return error_response(503, "RUN_QUEUE_FULL", f"{manager.running()} runs on agents are already going")
    try:
        run_id = await manager.start(problem, policy)
    except ValueError as error:
        return error_response(422, "INVALID_REQUEST", str(error))
    return JSONResponse({"run_id": run_id, "status": "running"}, status_code=202)


@router.post("/runs/{run_id}/events", status_code=201, response_model=RunOut, responses=ERRORS)
async def add_event(request: Request, run_id: RunId) -> Any:
    """Новый прогон: вход прогона `run_id` с его политикой и ещё одним событием. Исходный прогон не меняется.

    Так проверяют «а если узел упадёт в 12» или «а если подать срочную задачу»: что сдвинулось — в `replans`.
    """
    store = _store(request)
    base = await asyncio.to_thread(lambda: ProblemIn.model_validate(store.problem(run_id)))
    try:
        event = (await asyncio.to_thread(EventRequest.model_validate_json, await _read_body(request))).event
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    except ValidationError as error:
        found = [d.to_dict() for d in schema_diagnostics(error)]
        return error_response(422, "SCHEMA_INVALID", "request does not match the schema", found)
    problem = base.model_copy(update={"events": [*base.events, event]})
    return await _run_and_store(request, problem, problem.policy)


@router.post("/whatif", response_model=WhatIfOut, responses=ERRORS)
async def what_if(request: Request) -> Any:
    """Исходный вход и его варианты с одним изменением каждый: метрики вариантов и разница с исходным.

    Каждый вариант — обычный сохранённый прогон: по его `run_id` открываются расписание и объяснения.
    """
    try:
        body = await asyncio.to_thread(WhatIfRequest.model_validate_json, await _read_body(request))
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    except ValidationError as error:
        found = [d.to_dict() for d in schema_diagnostics(error)]
        return error_response(422, "SCHEMA_INVALID", "request does not match the schema", found)
    config = body.policy or body.problem.policy
    try:
        variants = [apply(body.problem, change) for change in body.interventions]
    except ValueError as error:
        return error_response(422, "INVALID_REQUEST", str(error))
    problems = [body.problem, *variants]
    store = _store(request)
    # Места в очереди — сразу под все ещё не посчитанные варианты: what-if принимается целиком или никак
    missing = await asyncio.to_thread(
        lambda: len({i for p in problems if store.find(i := run_id_of(input_hash(p, config))) is None})
    )
    runner: ProcessRunner = request.app.state.runner
    try:
        with runner.reserve(missing):
            runs = await asyncio.gather(*(_stored_run(request, p, config, reserved=True) for p in problems))
    except (NotImplementedError, RunTimeout, RunQueueFull, RunFailed) as error:
        return _run_error(error)
    (base_id, base), *others = runs
    return {
        "baseline": _summary(base_id, base),
        "variants": [
            {
                "intervention": change.model_dump(mode="json"),
                **_summary(run_id, result),
                "delta": deltas(base["metrics"], result["metrics"]),
            }
            for change, (run_id, result) in zip(body.interventions, others, strict=True)
        ],
    }


def _summary(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": run_id, "status": result["status"], "metrics": result["metrics"]}


@router.get("/runs/{run_id}", response_model=RunOut, responses=ERRORS)
def get_run(request: Request, run_id: RunId) -> Any:
    live = request.app.state.emulations.live.get(run_id)
    if live is not None and live.emulation.status == "running":
        return error_response(409, "RUN_IN_PROGRESS", f"run {run_id} is still going on agents")
    return {"run_id": run_id, "result": _store(request).result(run_id)}


@router.post("/runs/{run_id}/replay", response_model=ReplayOut, responses=ERRORS)
async def replay_run(request: Request, run_id: RunId) -> Any:
    """Проиграть журнал сохранённого прогона его же фактами, без контейнеров: те же ли решения у политики."""
    live = request.app.state.emulations.live.get(run_id)
    if live is not None and live.emulation.status == "running":
        return error_response(409, "RUN_IN_PROGRESS", f"run {run_id} is still going on agents")
    store = _store(request)

    def load_and_replay() -> dict[str, Any]:
        problem = ProblemIn.model_validate(store.problem(run_id))
        events, _ = store.events(run_id, 0, 10**9)
        return replay(problem, events, pinned(problem, store.result(run_id).get("estimates")))

    return await asyncio.to_thread(load_and_replay)


@router.get("/runs/{run_id}/problem", response_model=ProblemIn, responses=ERRORS)
def get_problem(request: Request, run_id: RunId) -> Any:
    """Вход прогона с политикой, которой он посчитан."""
    live = request.app.state.emulations.live.get(run_id)
    return live.problem if live is not None else _store(request).problem(run_id)


@router.get("/runs/{run_id}/events", response_model=EventsPage, responses=ERRORS)
def get_events(
    request: Request,
    run_id: RunId,
    from_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> Any:
    """Журнал страницами: записи с номерами [from_seq, from_seq + limit); у идущего прогона — сколько есть."""
    live = request.app.state.emulations.live.get(run_id)
    if live is not None and live.emulation.status == "running":
        records = live.emulation.sim.journal.records
        events, total = list(records[from_seq : from_seq + limit]), len(records)
    else:
        events, total = _store(request).events(run_id, from_seq, limit)
    next_seq = from_seq + len(events)
    return {"events": events, "total": total, "next_seq": next_seq if next_seq < total else None}


@router.get("/runs/{run_id}/explain", response_model=ExplainView, responses=ERRORS)
def get_explain(
    request: Request,
    run_id: RunId,
    task: str | None = None,
    worker: str | None = None,
    t: Annotated[int | None, Query(ge=0)] = None,
) -> Any:
    """Почему прогон такой: цепочка, попытки, ожидания и простой, суженные до задачи, узла и момента."""
    store = _store(request)
    result = store.result(run_id)
    if result["explain"] is None:
        return error_response(404, "NOT_FOUND", f"run {run_id} was not simulated: the input has errors")
    if task is not None and task not in result["tasks"]:
        return error_response(404, "NOT_FOUND", f"run {run_id} has no task {task!r}")
    if worker is not None and worker not in {w["id"] for w in store.problem(run_id)["cluster"]["workers"]}:
        return error_response(404, "NOT_FOUND", f"run {run_id} has no worker {worker!r}")
    return explain_view(result, task, worker, t)


class _AgentServer(uvicorn.Server):
    """Слушатель агентов в цикле событий основного сервера; сигналы ловит основной — он и останавливает."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def agent_listener(app: FastAPI, listen: str | None) -> uvicorn.Server | None:
    """Отдельный адрес только для агентов (`EXPSCHED_AGENT_LISTEN=0.0.0.0:8001`): агенты с других машин
    ходят на него, а клиент и остальной API остаются на 127.0.0.1. Без токена агентов адрес не открывается."""
    if not listen:
        return None
    if not os.environ.get("EXPSCHED_AGENT_TOKEN"):
        raise RuntimeError("EXPSCHED_AGENT_LISTEN needs EXPSCHED_AGENT_TOKEN: the agent API would be open")
    host, _, port = listen.rpartition(":")
    if not port.isdigit():
        raise RuntimeError(f"EXPSCHED_AGENT_LISTEN must be host:port, got {listen!r}")
    agent_app = FastAPI(title="expsched agents", docs_url=None, redoc_url=None, openapi_url=None)
    agent_app.state = app.state  # тот же реестр агентов и те же прогоны
    agent_app.include_router(agents.router)
    config = uvicorn.Config(
        agent_app, host=host or "0.0.0.0", port=int(port), lifespan="off", log_level="info"
    )
    return _AgentServer(config)


def create_app(
    cors_origins: Sequence[str] = (),
    store: RunStore | None = None,
    runner: ProcessRunner | None = None,
    scenarios_dir: FilePath | None = None,
    web_dir: FilePath | None = None,
) -> FastAPI:
    runner = runner or ProcessRunner(
        float(os.environ.get("EXPSCHED_RUN_TIMEOUT", "60")),
        int(os.environ.get("EXPSCHED_RUN_WORKERS", "2")),
        int(os.environ.get("EXPSCHED_RUN_QUEUE", "32")),
        int(os.environ.get("EXPSCHED_RUN_MEMORY_MB", "8192")) or None,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not os.environ.get("EXPSCHED_AGENT_TOKEN"):
            log.info(
                "agent API: no EXPSCHED_AGENT_TOKEN, so only this server's local agents can connect "
                "(with a token it made up); set EXPSCHED_AGENT_TOKEN for agents on other machines"
            )
        listener = agent_listener(app, os.environ.get("EXPSCHED_AGENT_LISTEN"))
        task = asyncio.create_task(listener.serve()) if listener else None
        yield
        if listener and task:
            listener.should_exit = True
            await task
        app.state.emulations.close()  # прогоны на агентах отменяются: их контейнеры останавливаются
        await asyncio.to_thread(app.state.local_agents.close)  # свои агенты уходят вместе с сервером
        runner.close()  # рабочие процессы не переживают сервер

    app = FastAPI(title="expsched", lifespan=lifespan)
    app.state.runner = runner
    app.state.scenarios_dir = scenarios_dir or FilePath(
        os.environ.get("EXPSCHED_SCENARIOS_DIR", FilePath(__file__).resolve().parents[3] / "scenarios")
    )
    app.state.store = store or RunStore(
        FilePath(os.environ.get("EXPSCHED_RUNS_DIR", "runs")),
        int(os.environ.get("EXPSCHED_MAX_RUNS", "100")),
        int(os.environ.get("EXPSCHED_MAX_RUNS_MB", "2048")) << 20,
    )
    app.state.agents = AgentRegistry()
    app.state.agent_token = secrets.token_urlsafe(32)  # без EXPSCHED_AGENT_TOKEN — только для своих агентов
    app.state.emulations = Emulations(app.state.agents, app.state.store)
    app.state.local_agents = LocalAgents(
        FilePath(os.environ.get("EXPSCHED_LOCAL_AGENTS_DIR", app.state.store.root / ".local-agents"))
    )
    app.include_router(router)
    app.include_router(agents.listing)
    app.include_router(agents.local)
    app.include_router(agents.router)
    app.include_router(emulations.listing)
    app.include_router(emulations.router)
    add_security_headers(app)
    if web_dir is not None:
        mount_web(app, web_dir, lambda path: error_response(404, "NOT_FOUND", f"/{path} not found"))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
    app.add_exception_handler(RunNotFound, _run_not_found)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _request_error)
    app.add_exception_handler(Exception, _internal_error)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _with_request_bodies(
                get_openapi(title=app.title, version=app.version, routes=app.routes)
            )
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


async def _run_and_store(request: Request, problem: ProblemIn, config: PolicyIn) -> Any:
    """Ответ `{run_id, result}` или ошибка прогона (см. `_run_error`)."""
    try:
        run_id, result = await _stored_run(request, problem, config)
    except (NotImplementedError, RunTimeout, RunQueueFull, RunFailed) as error:
        return _run_error(error)
    return {"run_id": run_id, "result": result}


def _run_error(error: Exception) -> JSONResponse:
    """Политика не реализована — 400, очередь полна — 503, таймаут — 504, падение прогона — 500."""
    if isinstance(error, NotImplementedError):
        return error_response(400, "POLICY_NOT_IMPLEMENTED", str(error))
    if isinstance(error, RunQueueFull):
        return error_response(503, "RUN_QUEUE_FULL", str(error))
    if isinstance(error, RunTimeout):
        return error_response(504, "RUN_TIMEOUT", str(error))
    log.error("run failed: %s", error)  # трассировка рабочего процесса — только в журнал сервера
    return error_response(500, "RUN_FAILED", "the run failed in the worker process; see the server log")


async def _stored_run(
    request: Request, problem: ProblemIn, config: PolicyIn, reserved: bool = False
) -> tuple[str, dict[str, Any]]:
    """Сохранённый прогон входа с политикой или новый: в рабочем процессе, с таймаутом, затем в хранилище.

    Прогон, вытесненный между проверкой и чтением, считается заново, устаревший — тоже, на его месте.
    `reserved` — место в очереди уже взято.
    """
    store = _store(request)
    run_id = await asyncio.to_thread(lambda: run_id_of(input_hash(problem, config)))
    try:
        return run_id, await asyncio.to_thread(store.result, run_id)
    except RunOutdated:
        await asyncio.to_thread(store.discard, run_id)
    except RunNotFound:
        pass
    runner: ProcessRunner = request.app.state.runner
    with nullcontext() if reserved else runner.reserve():
        outcome = await runner.run(problem, config)
    stored = problem.model_copy(update={"policy": config}).model_dump(mode="json")
    await asyncio.to_thread(store.save, run_id, stored, outcome)
    return run_id, outcome.result


def _scenarios(request: Request) -> dict[str, ProblemIn]:
    """Примеры из каталога сценариев по имени файла; файлы, не прошедшие схему, пропускаются."""
    out = {}
    root: FilePath = request.app.state.scenarios_dir
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            out[path.stem] = load(path.read_bytes())
        except ValueError:
            log.warning("scenario %s does not match the schema", path.name)
    return out


def _store(request: Request) -> RunStore:
    store: RunStore = request.app.state.store
    return store


def _no_run(run_id: str) -> JSONResponse:
    return error_response(
        404, "NOT_FOUND", f"run {run_id} is not stored: it was never made or has been evicted"
    )


async def _run_not_found(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, RunOutdated):
        message = f"run {error.run_id} was made by an older version of the service: run the input again"
        return error_response(404, "NOT_FOUND", message)
    return _no_run(error.run_id if isinstance(error, RunNotFound) else "")


async def _read_body(request: Request) -> bytes:
    """Тело запроса с обрывом на лимите: большой вход не читается целиком в память."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise InputTooLarge(f"body is {declared} bytes, limit is {MAX_BODY_BYTES}")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise InputTooLarge(f"body exceeds {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _http_error(request: Request, error: Exception) -> JSONResponse:
    status = error.status_code if isinstance(error, HTTPException) else 400
    code: ErrorCode = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(status, "INVALID_REQUEST")  # type: ignore[assignment]
    return error_response(status, code, str(getattr(error, "detail", error)))


async def _request_error(request: Request, error: Exception) -> JSONResponse:
    problems = error.errors() if isinstance(error, RequestValidationError) else []
    message = "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in problems) or str(error)
    return error_response(422, "INVALID_REQUEST", message)


async def _internal_error(request: Request, error: Exception) -> JSONResponse:
    log.exception("unhandled error in %s %s", request.method, request.url.path)
    return error_response(500, "INTERNAL", "internal server error")  # трассировка клиенту не отдаётся


def _with_request_bodies(spec: dict[str, Any]) -> dict[str, Any]:
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for path, model in REQUEST_BODIES.items():
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, definition in schema.pop("$defs", {}).items():
            schemas.setdefault(name, definition)
        schemas.setdefault(model.__name__, schema)
        spec["paths"][path]["post"]["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{model.__name__}"}}},
        }
    return spec


def _cors_origins() -> list[str]:
    return [o.strip() for o in os.environ.get("EXPSCHED_CORS_ORIGINS", "").split(",") if o.strip()]


def _web_dir() -> FilePath | None:
    """Сборка клиента, если она есть: `EXPSCHED_WEB_DIR` или `ui/web/dist` в репозитории."""
    default = FilePath(__file__).resolve().parents[3] / "ui" / "web" / "dist"
    web_dir = FilePath(os.environ.get("EXPSCHED_WEB_DIR", default))
    if (web_dir / "index.html").is_file():
        return web_dir
    log.warning("no client build in %s: serving the API only", web_dir)
    return None


app = create_app(_cors_origins(), web_dir=_web_dir())
