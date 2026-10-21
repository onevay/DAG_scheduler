"""Сертификаты невыполнимости дедлайнов."""

from __future__ import annotations

from expsched.analysis.analyze import Analysis
from expsched.domain.models import Problem
from expsched.validation.diagnostics import Code, Diagnostic


def deadline_certificates(problem: Problem, analysis: Analysis) -> list[Diagnostic]:
    """E030 только для задач с собственным дедлайном, у которых EFT > D.

    Предков проверять не нужно: если EFT предка больше его LFT, то по пути до задачи с дедлайном
    EFT > D, и сертификат будет выдан для неё. Иначе на каждого предка пришло бы лишнее E030.
    Это достаточное условие невыполнимости, но не необходимое: молчание E030 не значит «выполнимо».
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
