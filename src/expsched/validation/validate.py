"""Полная проверка входа: размер, JSON и схема, затем граф, ресурсы, события и дедлайны."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import ValidationError

from expsched.analysis.analyze import analyze
from expsched.contract.io_models import ProblemIn, load, to_domain
from expsched.domain.models import Problem
from expsched.estimate import Estimate, resolve
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
    problem, diagnostics, _ = check(raw, mode)
    return problem, diagnostics


def check(
    raw: bytes, mode: ValidationMode, history: Callable[[ProblemIn], Mapping[str, Estimate]] | None = None
) -> tuple[Problem | None, list[Diagnostic], dict[str, Estimate]]:
    """Как `validate`, и ещё оценки работы задач без длительности — с ними пойдёт прогон."""
    try:
        parsed = load(raw)
    except ValidationError as error:
        return None, schema_diagnostics(error), {}
    resolved, estimates = resolve(parsed, history(parsed) if history else None)
    return *validate_problem(to_domain(resolved), mode), estimates


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
