"""Ответ-ошибка API в едином формате `{error: {code, message}, diagnostics?}`."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from expsched.api.models import ErrorCode, ErrorOut


def error_response(
    status: int, code: ErrorCode, message: str, diagnostics: list[dict[str, Any]] | None = None
) -> JSONResponse:
    body = ErrorOut.model_validate({"error": {"code": code, "message": message}, "diagnostics": diagnostics})
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=status)
