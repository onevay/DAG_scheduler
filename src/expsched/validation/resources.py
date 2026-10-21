"""Проверка совместимости задач с кластером: помещается ли задача хоть на один узел."""

from __future__ import annotations

from collections.abc import Iterable

from expsched.domain.models import Problem, Task, Worker
from expsched.validation.diagnostics import Code, Diagnostic


def validate_resources(problem: Problem) -> list[Diagnostic]:
    """E020: требование больше ёмкости любого узла, подходящего по меткам; E021: подходящего узла нет."""
    out: list[Diagnostic] = []
    for key in sorted(problem.tasks):
        task = problem.tasks[key]
        if not problem.eligible(task):
            out.append(_explain_ineligible(task, problem.workers.values()))
    return out


def _explain_ineligible(task: Task, workers: Iterable[Worker]) -> Diagnostic:
    wanted = set(task.constraints)
    hosts = [w for w in workers if wanted <= set(w.labels)]
    constraints = dict(task.constraints)
    if not hosts:
        return Diagnostic(
            Code.E021,
            {"task": task.id, "constraints": constraints},
            "Ни у одного узла нет всех нужных меток: ослабьте constraints или добавьте узел",
        )
    for resource in task.resources:
        best = max(w.capacity.get(resource) for w in hosts)
        need = task.resources.get(resource)
        if need > best:
            return Diagnostic(
                Code.E020,
                {"task": task.id, "resource": resource, "need": need, "max": best},
                f"Нужно {need} {resource}, максимум на подходящем узле — {best}: "
                "разбейте задачу или добавьте узел",
            )
    return Diagnostic(
        Code.E021,
        {"task": task.id, "constraints": constraints},
        "Каждого ресурса по отдельности хватает, но ни один узел не вмещает все требования сразу",
    )
