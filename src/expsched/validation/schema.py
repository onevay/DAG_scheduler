"""Ошибки схемы входа в виде диагностик: E070 для длительности, E001 для всего остального."""

from __future__ import annotations

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from expsched.validation.diagnostics import Code, Diagnostic

# Ограничивает размер ответа на вход с огромным числом ошибок.
MAX_SCHEMA_DIAGNOSTICS = 50


def schema_diagnostics(error: ValidationError) -> list[Diagnostic]:
    details = error.errors(include_url=False, include_context=False, include_input=False)
    out = [_to_diagnostic(d) for d in details[:MAX_SCHEMA_DIAGNOSTICS]]
    omitted = len(details) - MAX_SCHEMA_DIAGNOSTICS
    if omitted > 0:
        out.append(
            Diagnostic(Code.E001, {"omitted": omitted}, "Показаны первые ошибки; исправьте их и повторите")
        )
    return out


def _to_diagnostic(error: ErrorDetails) -> Diagnostic:
    loc = ".".join(str(part) for part in error["loc"])
    if error["type"] == "greater_than_equal" and error["loc"][-1:] == ("duration",):
        return Diagnostic(
            Code.E070,
            {"loc": loc},
            "Длительность должна быть не меньше 1: иначе симуляция не гарантирует завершение",
        )
    return Diagnostic(
        Code.E001,
        {"loc": loc, "msg": error["msg"], "type": error["type"]},
        "Поле не соответствует schemas/problem.schema.json",
    )
