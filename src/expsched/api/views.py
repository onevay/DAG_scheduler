"""Срезы сохранённого результата и сводки входа для ответов API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from expsched.analysis import analyze, critical_path
from expsched.domain.models import Problem
from expsched.validation import quarantine
from expsched.validation.diagnostics import Diagnostic


def explain_view(
    result: Mapping[str, Any], task: str | None = None, worker: str | None = None, t: int | None = None
) -> dict[str, Any]:
    """Объяснение прогона, суженное до задачи, узла и момента времени.

    Каждый список оставляет только то, что относится ко всем заданным условиям: звенья цепочки и попытки —
    этой задачи, на этом узле, идущие в момент `t`; ожидания — этой задачи в момент `t`; простой — этого узла
    в момент `t`. Ожидание — свойство задачи, простой — узла: при фильтре по узлу ожиданий в ответе нет, при
    фильтре по задаче нет простоя. Разложение makespan отдаётся целиком.
    """
    explain = result["explain"]

    def during(lo: int, hi: int | None) -> bool:
        return t is None or (lo <= t and (hi is None or t < hi))

    def matches(item: Mapping[str, Any]) -> bool:
        return (task is None or item.get("task") == task) and (worker is None or item.get("worker") == worker)

    waits = [
        {"task": key, **w}
        for key, summary in sorted(result["tasks"].items())
        for w in summary["waits"]
        if (task is None or key == task) and worker is None and during(w["from"], w["to"])
    ]
    return {
        "chain": [c for c in explain["chain"] if matches(c) and during(c["start"], c["end"])],
        "decomposition": explain["decomposition"],
        "attempts": [a for a in result["attempts"] if matches(a) and during(a["start"], a["end"])],
        "waits": waits,
        "idle": [
            i
            for i in explain["idle"]["intervals"]
            if task is None and (worker is None or i["worker"] == worker) and during(i["from"], i["to"])
        ],
    }


def input_analysis(problem: Problem, diagnostics: Sequence[Diagnostic]) -> dict[str, Any] | None:
    """Нижняя граница makespan и критический путь планируемой части входа (без задач с ошибками).

    Поданные событиями задачи входят со своим моментом подачи как release_time, если все их зависимости
    известны. None — планировать нечего.
    """
    runnable = quarantine(problem, diagnostics).problem
    tasks = dict(runnable.tasks)
    for event in runnable.events:
        task = event.task
        if task is not None and task.id not in tasks and set(task.deps) <= tasks.keys():
            tasks[task.id] = replace(task, release=max(task.release, event.time))
    if not tasks:
        return None
    planned = replace(runnable, tasks=tasks)
    analysis = analyze(planned)
    return {"lower_bound": analysis.lb, "critical_path": critical_path(planned, analysis)}
