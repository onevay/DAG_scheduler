"""Полная проверка входа: размер, JSON и схема, затем граф, ресурсы, события и дедлайны."""

from __future__ import annotations

from typing import Literal

from pydantic import ValidationError

from expsched.analysis.analyze import analyze
from expsched.contract.io_models import load, to_domain
from expsched.domain.models import Problem
from expsched.estimate import resolve
from expsched.validation.deadlines import deadline_certificates
from expsched.validation.diagnostics import Diagnostic, has_errors
from expsched.validation.events import validate_events
from expsched.validation.graph import dedupe_dependencies, validate_graph
from expsched.validation.quarantine import quarantine
from expsched.validation.resources import validate_resources
from expsched.validation.schema import schema_diagnostics

ValidationMode = Literal["strict", "lenient"]


def validate(raw: bytes, mode: ValidationMode) -> tuple[Problem | None, list[Diagnostic]]:
    """Проверка сырого входа. Слишком большой вход даёт исключение InputTooLarge, а не диагностику."""
    try:
        parsed = load(raw)
    except ValidationError as error:
        return None, schema_diagnostics(error)
    return validate_problem(to_domain(resolve(parsed)[0]), mode)


def validate_problem(problem: Problem, mode: ValidationMode) -> tuple[Problem | None, list[Diagnostic]]:
    """Проверка графа, ресурсов, событий и дедлайнов.

    В режиме strict при любой ошибке возвращает (None, диагностики). Иначе — экземпляр без повторов
    в зависимостях; задачи и события с ошибками из него убирает `quarantine`.
    """
    diagnostics = [*validate_graph(problem.tasks), *validate_resources(problem), *validate_events(problem)]
    # У задач в цикле нет сроков, поэтому дедлайны проверяются только на планируемой части.
    runnable = quarantine(problem, diagnostics).problem
    diagnostics += deadline_certificates(runnable, analyze(runnable))
    if mode == "strict" and has_errors(diagnostics):
        return None, diagnostics
    return dedupe_dependencies(problem), diagnostics
