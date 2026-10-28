"""Прогон целиком: проверка входа, карантин, симуляция выбранной политикой, сборка результата."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from expsched.analysis import analyze
from expsched.contract.hashing import canonical_hash
from expsched.contract.io_models import PolicyIn, ProblemIn, to_domain
from expsched.domain.models import Problem, Task, time_for
from expsched.domain.states import TaskStatus
from expsched.engine.executor import Executor
from expsched.engine.simulator import Simulator
from expsched.estimate import Estimate, resolve
from expsched.explain.chain import critical_chain, decompose
from expsched.explain.idle import idle_by_code, idle_intervals
from expsched.explain.waits import wait_intervals
from expsched.metrics.metrics import metrics
from expsched.policies.registry import make_policy
from expsched.validation import Quarantine, quarantine, validate_problem
from expsched.verify.attempts import attempts_from_log
from expsched.verify.invariants import verify

SCHEMA_VERSION = "1.1"


@dataclass(frozen=True)
class RunResult:
    result: dict[str, Any]  # итог прогона, пригодный для JSON
    events: list[dict[str, Any]]  # журнал прогона


def input_hash(problem: ProblemIn, policy: PolicyIn) -> str:
    """Хеш входа вместе с действующей политикой: одинаковый у прогонов, которые дадут одинаковый результат."""
    return canonical_hash({"problem": problem.model_dump(mode="json"), "policy": policy.model_dump()})


@dataclass(frozen=True)
class Prepared:
    """Вход после проверки и карантина — всё, чтобы запустить симулятор и потом собрать результат."""

    problem: ProblemIn
    config: PolicyIn
    base: dict[str, Any]
    validated: Problem
    diagnostics: list[dict[str, Any]]
    plan: Quarantine
    forecast: bool
    estimates: dict[str, Estimate] = field(default_factory=dict)  # задачи без длительности: чем их заменили

    def simulator(self, executor: Executor | None = None) -> Simulator:
        policy = make_policy(self.config, self.problem.seed)
        return Simulator(self.plan.problem, policy, forecast=self.forecast, executor=executor)


def run(problem: ProblemIn, policy: PolicyIn | None = None) -> RunResult:
    """Прогон входа политикой `policy` (по умолчанию — из входа).

    Статус результата: OK — все задачи выполнены; PARTIAL — часть задач не выполнена (ошибки входа,
    блокировка, не дошли до конца); INFEASIBLE — режим strict нашёл ошибки, и симуляция не запускалась.
    Диагностики результата — это ошибки входа; то, что случилось во время прогона, видно в журнале.
    `replans` — None, если задач больше `max_forecast_tasks` и прогноз не строился.
    """
    prepared = prepare(problem, policy)
    if isinstance(prepared, RunResult):
        return prepared
    sim = prepared.simulator()
    sim.run_all()
    return conclude(prepared, sim)


def prepare(
    problem: ProblemIn, policy: PolicyIn | None = None, history: Mapping[str, Estimate] | None = None
) -> Prepared | RunResult:
    """Проверка и карантин входа; RunResult — вход не прошёл строгую проверку (INFEASIBLE)."""
    config = policy or problem.policy
    base = {
        "schema_version": SCHEMA_VERSION,
        "input_hash": input_hash(problem, config),
        "policy": config.name,
    }
    resolved, estimates = resolve(problem, history)
    validated, diagnostics = validate_problem(to_domain(resolved), config.validation_mode)
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
            "explain": None,
            "replans": [],
            "diagnostics": found,
            "violations": [],
        }
        return RunResult(result, [])

    plan = quarantine(validated, diagnostics)
    forecast = 0 < len(plan.problem.all_tasks()) <= config.max_forecast_tasks
    return Prepared(resolved, config, base, validated, found, plan, forecast, estimates)


def conclude(prepared: Prepared, sim: Simulator, exact: bool = True) -> RunResult:
    """Результат законченного прогона симулятора. `exact=False` — длительности попыток взяты из жизни
    (прогон на агентах): проверка I1 «попытка шла ровно остаток работы» не делается."""
    base, config, plan, forecast = prepared.base, prepared.config, prepared.plan, prepared.forecast
    log = sim.run_all()  # у законченного прогона событий нет: только итоговая диагностика
    status = {**plan.excluded, **sim.state.status}
    planned = {k: t for k, t in sim.state.tasks.items() if status[k] is not TaskStatus.INFEASIBLE}
    fastest = max((w.speed for w in plan.problem.workers.values()), default=1)
    if fastest > 1:  # граница должна остаться границей и на узлах быстрее эталона
        planned = {k: replace(t, duration=time_for(t.duration, fastest)) for k, t in planned.items()}
    lower_bound = analyze(replace(plan.problem, tasks=planned)).lb if planned else None
    scores = metrics(plan.problem, log, lower_bound, config.objective)
    result = {
        **base,
        "status": "OK" if set(status.values()) <= {TaskStatus.SUCCEEDED} else "PARTIAL",
        "result_hash": sim.journal.result_hash(),
        "attempts": [asdict(a) for a in attempts_from_log(log)],
        "tasks": _task_summaries(prepared.validated.all_tasks() | sim.state.tasks, status, log),
        "metrics": scores,
        "explain": _explain(plan.problem, sim.state.tasks, log, scores["makespan"]),
        "replans": [r for r in log if r["type"] == "REPLAN"] if forecast else None,
        "diagnostics": prepared.diagnostics,
        "violations": verify(plan.problem, log, config.max_preemptions_per_task, exact),
    }
    if prepared.estimates:
        result["estimates"] = {k: e.to_dict() for k, e in sorted(prepared.estimates.items())}
    return RunResult(result, log)


def _explain(
    problem: Problem, tasks: Mapping[str, Task], log: list[dict[str, Any]], makespan: int
) -> dict[str, Any]:
    chain = critical_chain(tasks, log)
    idle = idle_intervals(problem, log, makespan)
    return {
        "chain": chain,
        "decomposition": decompose(chain),
        "idle": {
            "by_code": idle_by_code(idle, {r for w in problem.workers.values() for r in w.capacity}),
            "intervals": idle,
        },
    }


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
