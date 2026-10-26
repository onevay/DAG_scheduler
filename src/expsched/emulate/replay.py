"""Replay: журнал сохранённого прогона проигрывается фактами из него самого, без контейнеров.

Факты — то, что движок не решает сам: окончания попыток (FINISH), их сбои (INTERRUPTED с FAILED, OOM),
отказы и возвраты узлов, сохранённая к прерыванию работа. Они подаются в движок в те же моменты и теми же
эпохами, что и в прогоне: факт после решений того же момента — новая эпоха, как у драйвера на агентах.
Решения политики (старты и вытеснения) сравниваются с записанными: совпали — прогон воспроизводим, иначе —
первое расхождение. У прогона-симуляции и журнал совпадает целиком.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from expsched.contract.io_models import ProblemIn
from expsched.domain.models import Event, EvType
from expsched.engine.events import Crash, Finish, Payload
from expsched.engine.executor import External
from expsched.pipeline import RunResult, prepare

DECISIONS = ("START", "RESERVE", "REPLAN")


def replay(problem: ProblemIn, log: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`problem` — вход прогона вместе с его политикой, `log` — его журнал целиком."""
    prepared = prepare(problem, problem.policy)
    if isinstance(prepared, RunResult):
        expected = decisions(log)
        return _verdict(expected, [], same_journal=not log)
    executor = External()
    sim = prepared.simulator(executor)
    for r in log:
        if r["type"] == "INTERRUPTED":
            executor.checkpoint(r["task"], r["attempt"], r["saved"])
    if problem.mode == "simulate":
        # У прерванной попытки симуляции в очереди остаётся её прежнее окончание: движок его пропускает, но
        # эпоха в этот момент всё равно была — её и нужно повторить
        tasks = prepared.plan.problem.all_tasks()
        starts = {(r["task"], r["attempt"]): r for r in log if r["type"] == "START"}
        for r in log:
            if r["type"] == "INTERRUPTED" and (start := starts.get((r["task"], r["attempt"]))):
                end = start["t"] + tasks[r["task"]].duration - start["resume_from"]
                sim.inject(end, Finish(r["task"], r["attempt"]))
    queued = Counter((e.time, e.type, e.worker) for e in prepared.plan.problem.events)
    for t, facts in epochs(log):
        for fact in facts:
            if isinstance(fact, Event) and queued[(t, fact.type, fact.worker)] > 0:
                queued[(t, fact.type, fact.worker)] -= 1  # событие входа: оно уже в очереди движка
                continue
            sim.inject(t, fact)
        sim.advance(t)
    produced = sim.run_all()  # хвост без фактов: release и события входа после последнего факта
    return _verdict(decisions(log), decisions(produced), same_journal=list(produced) == list(log))


def epochs(log: Sequence[Mapping[str, Any]]) -> list[tuple[int, list[Payload]]]:
    """Факты журнала по эпохам: новая эпоха — другой момент или факт после решений того же момента."""
    out: list[tuple[int, list[Payload]]] = []
    decided = False
    for r in log:
        fact = _fact(r)
        if fact is None:
            decided = (
                decided or r["type"] in DECISIONS or (r["type"] == "INTERRUPTED" and r["cause"] == "PREEMPT")
            )
            continue
        if out and out[-1][0] == r["t"] and not decided:
            out[-1][1].append(fact)
        else:
            out.append((r["t"], [fact]))
            decided = False
    return out


def decisions(log: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Решения политики: старты попыток и вытеснения, по порядку."""
    keep = ("t", "type", "task", "worker", "attempt")
    return [
        {k: r[k] for k in keep}
        for r in log
        if r["type"] == "START" or (r["type"] == "INTERRUPTED" and r["cause"] == "PREEMPT")
    ]


def _fact(r: Mapping[str, Any]) -> Payload | None:
    if r["type"] == "FINISH":
        return Finish(r["task"], r["attempt"])
    if r["type"] == "INTERRUPTED" and r["cause"] in ("FAILED", "OOM"):
        return Crash(r["task"], r["attempt"], r["cause"], (r.get("reason") or {}).get("detail"))
    if r["type"] in ("WORKER_DOWN", "WORKER_UP"):
        return Event(r["t"], EvType(r["type"].lower()), worker=r["worker"])
    return None


def _verdict(expected: list[dict[str, Any]], got: list[dict[str, Any]], same_journal: bool) -> dict[str, Any]:
    first = next((i for i, (a, b) in enumerate(zip(expected, got, strict=False)) if a != b), None)
    if first is None and len(expected) != len(got):
        first = min(len(expected), len(got))
    return {
        "same": first is None,
        "same_journal": same_journal,
        "decisions": len(expected),
        "divergence": None
        if first is None
        else {
            "index": first,
            "expected": expected[first] if first < len(expected) else None,
            "got": got[first] if first < len(got) else None,
        },
    }
