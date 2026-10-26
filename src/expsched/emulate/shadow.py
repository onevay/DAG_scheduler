"""Shadow: план (симуляция того же входа) рядом с фактом (прогон на агентах) и причина расхождения по задаче.

Причина — первая подходящая: `manual` — попытку прервал ручной отказ узла; `failure` — сбой контейнера
(FAILED, OOM) или отказ агента; `duration` — попытка шла не столько, сколько по плану; `upstream` —
стартовала позже, потому что зависимость закончилась позже плана; `decision` — политика решила иначе
(другой момент или узел); `same` — как в плане; `waiting` — по факту ещё не закончилась.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from expsched.verify.attempts import attempts_from_log

CAUSES = ("manual", "failure", "duration", "upstream", "decision", "same", "waiting")


def compare(
    plan_tasks: Mapping[str, Mapping[str, Any]],
    plan_log: Sequence[Mapping[str, Any]],
    fact_log: Sequence[Mapping[str, Any]],
    deps: Mapping[str, Collection[str]],
    durations: Mapping[str, int],
    dropped: Collection[tuple[str, int]],
) -> dict[str, Any]:
    """`dropped` — ручные отказы узлов (узел, момент); `durations` — работа задачи целиком."""
    fact = _first_start_and_finish(fact_log)
    plan_workers = {
        r["task"]: r["worker"] for r in plan_log if r["type"] == "START" and r.get("attempt") == 1
    }
    fact_workers = {
        r["task"]: r["worker"] for r in fact_log if r["type"] == "START" and r.get("attempt") == 1
    }
    interrupted: dict[str, list[Mapping[str, Any]]] = {}
    for r in fact_log:
        if r["type"] == "INTERRUPTED":
            interrupted.setdefault(r["task"], []).append(r)
    attempts = attempts_from_log(fact_log)
    tasks: dict[str, Any] = {}
    for key in sorted(set(plan_tasks) | set(fact)):
        plan = plan_tasks.get(key, {})
        p_start, p_finish = plan.get("start"), plan.get("finish")
        f_start, f_finish = fact.get(key, (None, None))
        cause = _cause(
            key,
            (p_start, p_finish, f_start, f_finish),
            interrupted.get(key, []),
            dropped,
            [a for a in attempts if a.task == key and a.outcome == "DONE"],
            durations.get(key),
            {
                d: (plan_tasks.get(d, {}).get("finish"), fact.get(d, (None, None))[1])
                for d in deps.get(key, ())
            },
            plan_workers.get(key) != fact_workers.get(key),
        )
        tasks[key] = {
            "plan_start": p_start,
            "plan_finish": p_finish,
            "fact_start": f_start,
            "fact_finish": f_finish,
            "finish_delta": f_finish - p_finish if f_finish is not None and p_finish is not None else None,
            "cause": cause,
        }
    finishes = [t["fact_finish"] for t in tasks.values() if t["fact_finish"] is not None]
    plan_finishes = [t["plan_finish"] for t in tasks.values() if t["plan_finish"] is not None]
    return {
        "plan_makespan": max(plan_finishes, default=0),
        "fact_makespan": max(finishes, default=0),
        "causes": {c: sum(1 for t in tasks.values() if t["cause"] == c) for c in CAUSES},
        "tasks": tasks,
    }


def _cause(
    key: str,
    times: tuple[int | None, int | None, int | None, int | None],
    interrupted: Sequence[Mapping[str, Any]],
    dropped: Collection[tuple[str, int]],
    done: Sequence[Any],
    duration: int | None,
    deps: Mapping[str, tuple[int | None, int | None]],
    other_worker: bool,
) -> str:
    p_start, p_finish, f_start, f_finish = times
    if f_finish is None:
        return "waiting"
    if any(r["cause"] == "WORKER_DOWN" and (r["worker"], r["t"]) in dropped for r in interrupted):
        return "manual"
    if interrupted:
        return "failure"
    if duration is not None and any(a.end - a.start != duration - a.resume_from for a in done):
        return "duration"
    if any(fact is not None and plan is not None and fact > plan for plan, fact in deps.values()):
        return "upstream"
    if f_start != p_start or f_finish != p_finish or other_worker:
        return "decision"
    return "same"


def _first_start_and_finish(log: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int | None, int | None]]:
    out: dict[str, tuple[int | None, int | None]] = {}
    for r in log:
        if r["type"] == "START":
            start, finish = out.get(r["task"], (None, None))
            out[r["task"]] = (r["t"] if start is None else start, finish)
        elif r["type"] == "FINISH":
            out[r["task"]] = (out.get(r["task"], (None, None))[0], r["t"])
    return out
