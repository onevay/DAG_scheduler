"""Хватит ли задачам повторов, если на агентах включён хаос."""

from __future__ import annotations

import math

from expsched.contract.io_models import ChaosIn, ProblemIn, TaskIn
from expsched.validation.diagnostics import Code, Diagnostic


def retries_needed(task: TaskIn, chaos: ChaosIn) -> int:
    """Сколько повторов может понадобиться задаче, если каждый сбой за время её работы придётся на неё."""
    estimate = task.estimate
    median = estimate if isinstance(estimate, (int, float)) else estimate.median if estimate else 1
    work = task.duration or median
    return sum(math.ceil(work / every) for every in (chaos.crash_every, chaos.node_down_every) if every)


def chaos_diagnostics(problem: ProblemIn) -> list[Diagnostic]:
    chaos = problem.chaos
    if chaos is None:
        return []
    if problem.mode not in ("emulate", "shadow"):
        return [
            Diagnostic(
                Code.W081,
                {"mode": problem.mode},
                "Хаос действует только на агентах: в этом режиме он не включится.",
            )
        ]
    short = {
        t.id: (t.max_retries, need)
        for t in problem.all_tasks()
        if t.max_retries < (need := retries_needed(t, chaos))
    }
    if not short:
        return []
    names = ", ".join(sorted(short)[:5])
    return [
        Diagnostic(
            Code.W080,
            {"tasks": {k: {"max_retries": r, "needed": n} for k, (r, n) in sorted(short.items())}},
            f"Хаос может оборвать задачу больше раз, чем у неё повторов: {names}. Поднимите max_retries "
            "или сделайте сбои реже, иначе задача может не выполниться.",
        )
    ]
