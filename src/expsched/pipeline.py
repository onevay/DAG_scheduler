"""Прогон целиком: проверка входа, карантин, симуляция выбранной политикой, сборка результата."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from expsched.analysis import analyze
from expsched.contract.hashing import canonical_hash
from expsched.contract.io_models import PolicyIn, ProblemIn, to_domain
from expsched.domain.models import Task
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator
from expsched.explain.waits import wait_intervals
from expsched.metrics.metrics import metrics
from expsched.policies.registry import make_policy
from expsched.validation import quarantine, validate_problem
from expsched.verify.attempts import attempts_from_log
from expsched.verify.invariants import verify

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class RunResult:
    result: dict[str, Any]  # итог прогона, пригодный для JSON
    events: list[dict[str, Any]]  # журнал прогона


def input_hash(problem: ProblemIn, policy: PolicyIn) -> str:
    """Хеш входа вместе с действующей политикой: одинаковый у прогонов, которые дадут одинаковый результат."""
    return canonical_hash({"problem": problem.model_dump(mode="json"), "policy": policy.model_dump()})


def run(problem: ProblemIn, policy: PolicyIn | None = None) -> RunResult:
    """Прогон входа политикой `policy` (по умолчанию — из входа).

    Статус результата: OK — все задачи выполнены; PARTIAL — часть задач не выполнена (ошибки входа,
    блокировка, не дошли до конца); INFEASIBLE — режим strict нашёл ошибки, и симуляция не запускалась.
    Диагностики результата — это ошибки входа; то, что случилось во время прогона, видно в журнале.
    `replans` — None, если задач больше `max_forecast_tasks` и прогноз не строился.
    """
    config = policy or problem.policy
    base = {
        "schema_version": SCHEMA_VERSION,
        "input_hash": input_hash(problem, config),
        "policy": config.name,
    }
    validated, diagnostics = validate_problem(to_domain(problem), config.validation_mode)
    found = [d.to_dict() for d in diagnostics]
    result: dict[str, Any]
    if validated is None:
        result = {
            **base,
            "status": "INFEASIBLE",
            "result_hash": None,
            "attempts": [],
            "tasks": {},
            "metrics": None,
            "replans": [],
            "diagnostics": found,
            "violations": [],
        }
        return RunResult(result, [])

    plan = quarantine(validated, diagnostics)
    forecast = 0 < len(plan.problem.all_tasks()) <= config.max_forecast_tasks
    sim = Simulator(plan.problem, make_policy(config), forecast=forecast)
    log = sim.run_all()
    status = {**plan.excluded, **sim.state.status}
    planned = {k: t for k, t in sim.state.tasks.items() if status[k] is not TaskStatus.INFEASIBLE}
    lower_bound = analyze(replace(plan.problem, tasks=planned)).lb if planned else None
    result = {
        **base,
        "status": "OK" if set(status.values()) <= {TaskStatus.SUCCEEDED} else "PARTIAL",
        "result_hash": sim.journal.result_hash(),
        "attempts": [asdict(a) for a in attempts_from_log(log)],
        "tasks": _task_summaries(validated.all_tasks() | sim.state.tasks, status, log),
        "metrics": metrics(plan.problem, log, lower_bound),
        "replans": [r for r in log if r["type"] == "REPLAN"] if forecast else None,
        "diagnostics": found,
        "violations": verify(plan.problem, log, config.max_preemptions_per_task),
    }
    return RunResult(result, log)


def _task_summaries(
    tasks: Mapping[str, Task], status: Mapping[str, TaskStatus], log: list[dict[str, Any]]
) -> dict[str, Any]:
    starts: dict[str, int] = {}
    finishes: dict[str, int] = {}
    for r in log:
        if r["type"] == "START":
            starts.setdefault(r["task"], r["t"])
        elif r["type"] == "FINISH":
            finishes[r["task"]] = r["t"]
    waits = wait_intervals(log)
    out = {}
    for key in sorted(tasks):
        deadline, finish = tasks[key].deadline, finishes.get(key)
        out[key] = {
            "status": str(status.get(key, TaskStatus.PENDING)),
            "start": starts.get(key),
            "finish": finish,
            "deadline": deadline,
            "tardiness": max(0, finish - deadline) if finish is not None and deadline is not None else None,
            "waits": waits.get(key, []),
        }
    return out
