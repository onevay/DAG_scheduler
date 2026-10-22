"""REST API: проверка входа и прогон. Прогон синхронный, результаты не хранятся."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from expsched.contract.io_models import PolicyIn, ProblemIn, Strict
from expsched.contract.limits import MAX_BODY_BYTES, InputTooLarge
from expsched.pipeline import run
from expsched.policies.registry import implemented_policies
from expsched.validation import Code, has_errors, validate
from expsched.validation.schema import schema_diagnostics

SCHEMA_CODES = frozenset({Code.E001, Code.E070})  # вход не разобран: остальные проверки не выполнялись

app = FastAPI(title="expsched")


class RunRequest(Strict):
    problem: ProblemIn
    policy: PolicyIn | None = None  # по умолчанию — политика из входа


@app.get("/v1/policies")
def policies() -> dict[str, Any]:
    """Реализованные политики и параметры по умолчанию — для формы клиента."""
    return {"policies": implemented_policies(), "defaults": PolicyIn().model_dump()}


@app.post("/v1/validate")
async def check_problem(request: Request, mode: Literal["strict", "lenient"] = "lenient") -> JSONResponse:
    """200 с диагностиками; 422, если вход не соответствует схеме; 413, если вход слишком большой."""
    try:
        _, diagnostics = validate(await _read_body(request), mode)
    except InputTooLarge as error:
        return JSONResponse({"error": str(error)}, status_code=413)
    body = {"valid": not has_errors(diagnostics), "diagnostics": [d.to_dict() for d in diagnostics]}
    schema_failed = any(d.code in SCHEMA_CODES for d in diagnostics)
    return JSONResponse(body, status_code=422 if schema_failed else 200)


@app.post("/v1/runs")
async def create_run(request: Request) -> JSONResponse:
    """Прогон {problem, policy?}: 200 с результатом и журналом, 422 — схема, 400 — политика не реализована."""
    try:
        run_request = RunRequest.model_validate_json(await _read_body(request))
    except InputTooLarge as error:
        return JSONResponse({"error": str(error)}, status_code=413)
    except ValidationError as error:
        return JSONResponse(
            {"diagnostics": [d.to_dict() for d in schema_diagnostics(error)]}, status_code=422
        )
    try:
        outcome = run(run_request.problem, run_request.policy)
    except NotImplementedError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    return JSONResponse({"result": outcome.result, "events": outcome.events})


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
