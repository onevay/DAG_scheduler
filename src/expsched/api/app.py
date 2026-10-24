"""REST API: проверка входа, прогон и чтение сохранённых прогонов.

Ошибка всегда приходит в одном формате — `{"error": {"code", "message"}, "diagnostics"?}` (ErrorOut).
Браузерные клиенты с другого адреса перечисляются в EXPSCHED_CORS_ORIGINS через запятую. Прогоны хранятся
в каталоге EXPSCHED_RUNS_DIR (по умолчанию runs/), не больше EXPSCHED_MAX_RUNS (100) штук. Каждый прогон
идёт в отдельном процессе: не дольше EXPSCHED_RUN_TIMEOUT секунд (60), не больше EXPSCHED_RUN_WORKERS (2)
одновременно.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path as FilePath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from expsched.api.models import (
    ErrorCode,
    ErrorOut,
    EventRequest,
    EventsPage,
    ExplainView,
    PoliciesOut,
    RunOut,
    RunRequest,
    RunsOut,
    ScenariosOut,
    ValidationOut,
    WhatIfOut,
    WhatIfRequest,
)
from expsched.api.runner import ProcessRunner, RunTimeout
from expsched.api.store import RUN_ID_PATTERN, RunStore, run_id_of
from expsched.api.views import explain_view, input_analysis
from expsched.contract.io_models import PolicyIn, ProblemIn, load
from expsched.contract.limits import MAX_BODY_BYTES, InputTooLarge
from expsched.contract.whatif import apply, deltas
from expsched.pipeline import input_hash
from expsched.policies.registry import implemented_policies
from expsched.validation import Code, has_errors, validate
from expsched.validation.schema import schema_diagnostics

SCHEMA_CODES = frozenset({Code.E001, Code.E070})  # вход не разобран: остальные проверки не выполнялись
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorOut} for status in (400, 404, 413, 422, 500, 504)
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
        problem, diagnostics = validate(await _read_body(request), mode)
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    found = [d.to_dict() for d in diagnostics]
    if any(d.code in SCHEMA_CODES for d in diagnostics):
        return error_response(422, "SCHEMA_INVALID", "input does not match the schema", found)
    analysis = input_analysis(problem, diagnostics) if problem is not None else None
    return {"valid": not has_errors(diagnostics), "diagnostics": found, "analysis": analysis}


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


@router.post("/runs", status_code=201, response_model=RunOut, responses=ERRORS)
async def create_run(request: Request) -> Any:
    """Прогон {problem, policy?}. Тот же вход с той же политикой не пересчитывается: отдаётся сохранённый."""
    try:
        run_request = RunRequest.model_validate_json(await _read_body(request))
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    except ValidationError as error:
        found = [d.to_dict() for d in schema_diagnostics(error)]
        return error_response(422, "SCHEMA_INVALID", "request does not match the schema", found)
    return await _run_and_store(
        request, run_request.problem, run_request.policy or run_request.problem.policy
    )


@router.post("/runs/{run_id}/events", status_code=201, response_model=RunOut, responses=ERRORS)
async def add_event(request: Request, run_id: RunId) -> Any:
    """Новый прогон: вход прогона `run_id` с его политикой и ещё одним событием. Исходный прогон не меняется.

    Так проверяют «а если узел упадёт в 12» или «а если подать срочную задачу»: что сдвинулось — в `replans`.
    """
    store = _store(request)
    if store.find(run_id) is None:
        return _no_run(run_id)
    try:
        event = EventRequest.model_validate_json(await _read_body(request)).event
    except InputTooLarge as error:
        return error_response(413, "INPUT_TOO_LARGE", str(error))
    except ValidationError as error:
        found = [d.to_dict() for d in schema_diagnostics(error)]
        return error_response(422, "SCHEMA_INVALID", "request does not match the schema", found)
    base = ProblemIn.model_validate(store.problem(run_id))
    problem = base.model_copy(update={"events": [*base.events, event]})
    return await _run_and_store(request, problem, problem.policy)


@router.post("/whatif", response_model=WhatIfOut, responses=ERRORS)
async def what_if(request: Request) -> Any:
    """Исходный вход и его варианты с одним изменением каждый: метрики вариантов и разница с исходным.

    Каждый вариант — обычный сохранённый прогон: по его `run_id` открываются расписание и объяснения.
    """
    try:
        body = WhatIfRequest.model_validate_json(await _read_body(request))
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
    try:
        runs = await asyncio.gather(*(_stored_run(request, p, config) for p in [body.problem, *variants]))
    except NotImplementedError as error:
        return error_response(400, "POLICY_NOT_IMPLEMENTED", str(error))
    except RunTimeout as error:
        return error_response(504, "RUN_TIMEOUT", str(error))
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
    if _store(request).find(run_id) is None:
        return _no_run(run_id)
    return {"run_id": run_id, "result": _store(request).result(run_id)}


@router.get("/runs/{run_id}/problem", response_model=ProblemIn, responses=ERRORS)
def get_problem(request: Request, run_id: RunId) -> Any:
    """Вход прогона с политикой, которой он посчитан."""
    if _store(request).find(run_id) is None:
        return _no_run(run_id)
    return _store(request).problem(run_id)


@router.get("/runs/{run_id}/events", response_model=EventsPage, responses=ERRORS)
def get_events(
    request: Request,
    run_id: RunId,
    from_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> Any:
    """Журнал страницами: записи с номерами [from_seq, from_seq + limit)."""
    if _store(request).find(run_id) is None:
        return _no_run(run_id)
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
    if store.find(run_id) is None:
        return _no_run(run_id)
    result = store.result(run_id)
    if result["explain"] is None:
        return error_response(404, "NOT_FOUND", f"run {run_id} was not simulated: the input has errors")
    if task is not None and task not in result["tasks"]:
        return error_response(404, "NOT_FOUND", f"run {run_id} has no task {task!r}")
    if worker is not None and worker not in {w["id"] for w in store.problem(run_id)["cluster"]["workers"]}:
        return error_response(404, "NOT_FOUND", f"run {run_id} has no worker {worker!r}")
    return explain_view(result, task, worker, t)


def create_app(
    cors_origins: Sequence[str] = (),
    store: RunStore | None = None,
    runner: ProcessRunner | None = None,
    scenarios_dir: FilePath | None = None,
) -> FastAPI:
    runner = runner or ProcessRunner(
        float(os.environ.get("EXPSCHED_RUN_TIMEOUT", "60")), int(os.environ.get("EXPSCHED_RUN_WORKERS", "2"))
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        runner.close()  # рабочие процессы не переживают сервер

    app = FastAPI(title="expsched", lifespan=lifespan)
    app.state.runner = runner
    app.state.scenarios_dir = scenarios_dir or FilePath(
        os.environ.get("EXPSCHED_SCENARIOS_DIR", FilePath(__file__).resolve().parents[3] / "scenarios")
    )
    app.state.store = store or RunStore(
        FilePath(os.environ.get("EXPSCHED_RUNS_DIR", "runs")), int(os.environ.get("EXPSCHED_MAX_RUNS", "100"))
    )
    app.include_router(router)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )
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


def error_response(
    status: int, code: ErrorCode, message: str, diagnostics: list[dict[str, Any]] | None = None
) -> JSONResponse:
    body = ErrorOut.model_validate({"error": {"code": code, "message": message}, "diagnostics": diagnostics})
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=status)


async def _run_and_store(request: Request, problem: ProblemIn, config: PolicyIn) -> Any:
    """Ответ `{run_id, result}` или ошибка: политика не реализована, прогон не уложился в таймаут."""
    try:
        run_id, result = await _stored_run(request, problem, config)
    except NotImplementedError as error:
        return error_response(400, "POLICY_NOT_IMPLEMENTED", str(error))
    except RunTimeout as error:
        return error_response(504, "RUN_TIMEOUT", str(error))
    return {"run_id": run_id, "result": result}


async def _stored_run(request: Request, problem: ProblemIn, config: PolicyIn) -> tuple[str, dict[str, Any]]:
    """Сохранённый прогон входа с политикой или новый: в рабочем процессе, с таймаутом, затем в хранилище."""
    run_id = run_id_of(input_hash(problem, config))
    store = _store(request)
    if store.find(run_id) is None:
        runner: ProcessRunner = request.app.state.runner
        outcome = await runner.run(problem, config)
        store.save(run_id, problem.model_copy(update={"policy": config}).model_dump(mode="json"), outcome)
    return run_id, store.result(run_id)


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


app = create_app(_cors_origins())
