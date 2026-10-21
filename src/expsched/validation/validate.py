"""Конвейер валидации: размер -> JSON -> схема -> ссылки и циклы -> ресурсы -> дедлайны."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from expsched.analysis.analyze import analyze
from expsched.contract.io_models import load, to_domain
from expsched.domain.models import Problem
from expsched.validation.deadlines import deadline_certificates
from expsched.validation.diagnostics import Diagnostic, has_errors
from expsched.validation.graph import dedupe_dependencies, validate_graph
from expsched.validation.quarantine import quarantine
from expsched.validation.resources import validate_resources
from expsched.validation.schema import schema_diagnostics

ValidationMode = Literal["strict", "lenient"]


def validate(raw: bytes, mode: ValidationMode) -> tuple[Problem | None, list[Diagnostic]]:
    """Полный разбор входа. Слишком большое тело (InputTooLarge) — не диагностика, а отказ на границе."""
    try:
        parsed = load(raw)
    except ValidationError as error:
        return None, schema_diagnostics(error)
    return validate_problem(to_domain(parsed), mode)


def validate_problem(problem: Problem, mode: ValidationMode) -> tuple[Problem | None, list[Diagnostic]]:
    """Проверки над доменным экземпляром.

    Возвращает (None, диагностики), если в strict найдена хоть одна ошибка: симуляцию запускать нельзя.
    Иначе возвращает экземпляр без повторов в зависимостях; в lenient с ошибками его нужно пропустить
    через `quarantine`, чтобы получить планируемую часть. Дедлайны проверяются на планируемой части:
    у цикла нет ни ранних, ни поздних сроков.
    """
    diagnostics = [*validate_graph(problem.tasks), *validate_resources(problem)]
    runnable = quarantine(problem, diagnostics).problem
    diagnostics += deadline_certificates(runnable, analyze(runnable))
    if mode == "strict" and has_errors(diagnostics):
        return None, diagnostics
    return dedupe_dependencies(problem), diagnostics
