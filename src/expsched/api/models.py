"""Тела запросов и ответов REST API. Из них FastAPI строит OpenAPI-схему для клиентов."""

from __future__ import annotations

from typing import Literal

from expsched.contract.io_models import PolicyIn, PolicyName, ProblemIn, Strict
from expsched.contract.output_models import DiagnosticOut, EventRecord, ResultOut

ErrorCode = Literal[
    "INPUT_TOO_LARGE",  # тело больше лимита
    "SCHEMA_INVALID",  # вход не соответствует схеме; подробности — в diagnostics
    "INVALID_REQUEST",  # неверные параметры запроса
    "POLICY_NOT_IMPLEMENTED",
    "RUN_TIMEOUT",  # прогон не уложился в отведённое время
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "INTERNAL",  # ошибка сервера; подробности только в его журнале
]


class RunRequest(Strict):
    problem: ProblemIn
    policy: PolicyIn | None = None  # по умолчанию — политика из входа


class PoliciesOut(Strict):
    policies: list[PolicyName]  # реализованные
    defaults: PolicyIn


class ValidationOut(Strict):
    valid: bool
    diagnostics: list[DiagnosticOut]


class RunOut(Strict):
    run_id: str  # по нему читаются вход, результат и журнал прогона
    result: ResultOut


class EventsPage(Strict):
    events: list[EventRecord]
    total: int  # записей в журнале всего
    next_seq: int | None  # с какого номера читать следующую страницу; None — журнал прочитан


class ErrorInfo(Strict):
    code: ErrorCode
    message: str


class ErrorOut(Strict):
    error: ErrorInfo
    diagnostics: list[DiagnosticOut] | None = None
