"""Доказуемо недостижимые дедлайны."""

from __future__ import annotations

from expsched.domain.analysis import Analysis
from expsched.domain.models import Problem
from expsched.validation.diagnostics import Code, Diagnostic


def deadline_certificates(problem: Problem, analysis: Analysis) -> list[Diagnostic]:
    """E030 для задач с собственным дедлайном, которые не успевают даже при неограниченных ресурсах.

    Предков не проверяем: их опоздание уже видно по задаче с дедлайном. Отсутствие E030 не доказывает,
    что дедлайны выполнимы.
    """
    out: list[Diagnostic] = []
    for key in analysis.order:
        deadline = problem.tasks[key].deadline
        if deadline is not None and analysis.eft[key] > deadline:
            out.append(
                Diagnostic(
                    Code.E030,
                    {"task": key, "earliest_finish": analysis.eft[key], "deadline": deadline},
                    f"Минимальное завершение — {analysis.eft[key]}: сдвиньте дедлайн или сократите цепочку",
                )
            )
    return out
