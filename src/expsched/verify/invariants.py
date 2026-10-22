"""Проверка расписания по журналу. Движку не доверяет: смотрит только на вход и записанные события."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from expsched.domain.models import Problem, Task
from expsched.domain.resources import Resources
from expsched.verify.attempts import Attempt, attempts_from_log, down_intervals


def verify(problem: Problem, log: Sequence[Mapping[str, Any]]) -> list[str]:
    """Нарушения вида «I3 node-B t=12: ...»; пустой список — расписание допустимо.

    I1 успешная попытка выполнила ровно оставшуюся работу; I2 старт не раньше окончания зависимостей;
    I3 загрузка узла не больше ёмкости; I4 попытка не идёт, пока узел недоступен; I5 старт не раньше release
    и подачи; I6 узел допустим для задачи; I7 после успешной попытки других нет; I11 попытка продолжает
    ровно с сохранённой работы; I12 попытки одной задачи не пересекаются.
    """
    tasks = problem.all_tasks()
    submitted = {r["task"]: r["t"] for r in log if r["type"] == "SUBMIT"}
    outages = down_intervals(log)
    attempts = attempts_from_log(log)
    unknown = sorted({a.task for a in attempts if a.task not in tasks})
    violations = [f"I0 {key}: task is not in the input" for key in unknown]
    attempts = [a for a in attempts if a.task in tasks]
    finished = {a.task: a.end for a in attempts if a.outcome == "DONE" and a.end is not None}

    for a in attempts:
        violations += _check_attempt(problem, tasks[a.task], a, finished, submitted.get(a.task, 0))
        violations += _check_outages(a, outages.get(a.worker, []))
    violations += _check_sequences(attempts)
    for worker in sorted(problem.workers):
        violations += _check_capacity(worker, problem.workers[worker].capacity, attempts, tasks)
    return violations


def _check_attempt(
    problem: Problem, task: Task, a: Attempt, finished: Mapping[str, int], submitted: int
) -> list[str]:
    out = []
    name = f"{a.task}#{a.attempt}"
    if a.outcome == "DONE" and a.end is not None and a.end - a.start != task.duration - a.resume_from:
        out.append(f"I1 {name}: worked {a.end - a.start}, expected {task.duration - a.resume_from}")
    for dep in sorted(set(task.deps)):
        if dep not in finished or finished[dep] > a.start:
            out.append(f"I2 {name}: starts at {a.start} before {dep} has finished")
    if a.start < max(task.release, submitted):
        out.append(f"I5 {name}: starts at {a.start} before release or submission")
    if a.worker not in problem.eligible(task):
        out.append(f"I6 {name}: worker {a.worker} is not eligible")
    return out


def _check_outages(a: Attempt, outages: Sequence[tuple[int, int | None]]) -> list[str]:
    """Полуинтервалы: попытка, прерванная ровно в момент отказа, с ним не пересекается."""
    end = math.inf if a.end is None else a.end
    return [
        f"I4 {a.task}#{a.attempt}: runs on {a.worker} while it is down from {lo}"
        for lo, hi in outages
        if a.start < (math.inf if hi is None else hi) and lo < end
    ]


def _check_sequences(attempts: Sequence[Attempt]) -> list[str]:
    by_task: dict[str, list[Attempt]] = {}
    for a in attempts:
        by_task.setdefault(a.task, []).append(a)
    out = []
    for key in sorted(by_task):
        runs = sorted(by_task[key], key=lambda a: a.attempt)
        saved = 0
        for a in runs:
            if a.resume_from != saved:
                out.append(f"I11 {key}#{a.attempt}: resumes from {a.resume_from}, saved work is {saved}")
            saved += a.saved
        for before, after in zip(runs, runs[1:], strict=False):
            if before.end is None or before.end > after.start:
                out.append(f"I12 {key}: attempts {before.attempt} and {after.attempt} overlap")
        done = [a for a in runs if a.outcome == "DONE"]
        if done and any(a.attempt > done[0].attempt for a in runs):
            out.append(f"I7 {key}: attempt after a successful one")
    return out


def _check_capacity(
    worker: str, capacity: Resources, attempts: Sequence[Attempt], tasks: Mapping[str, Task]
) -> list[str]:
    """Заметающая прямая: в один момент окончания учитываются раньше стартов (интервалы полуоткрытые)."""
    points: list[tuple[int, int, Resources]] = []
    for a in attempts:
        if a.worker == worker and (a.end is None or a.end >= a.start):  # обратный интервал уже нарушает I1
            points.append((a.start, 1, tasks[a.task].resources))
            if a.end is not None:
                points.append((a.end, 0, tasks[a.task].resources))
    out = []
    load = Resources()
    for t, is_start, need in sorted(points, key=lambda p: (p[0], p[1])):
        load = load + need if is_start else load - need
        if is_start and not load.fits(capacity):
            out.append(f"I3 {worker} t={t}: load {load!r} exceeds {capacity!r}")
    return out
