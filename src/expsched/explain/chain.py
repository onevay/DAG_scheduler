"""Критическая цепочка прогона и разложение makespan: из чего сложилась длина расписания."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from expsched.domain.models import Task
from expsched.verify.attempts import Attempt, attempts_from_log


def critical_chain(tasks: Mapping[str, Task], log: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Звенья цепочки от начала прогона к последнему окончанию, по одному на попытку.

    Цепочка строится назад от последней успешной попытки. Причина старта каждой попытки ищется по порядку:
    dep — ровно тогда закончилась зависимость; fail / pre — ровно тогда прервалась (отказом / вытеснением)
    прошлая попытка этой же задачи; res — ровно тогда на том же узле закончилась другая попытка и освободила
    место; иначе цепочка обрывается: origin — старт в 0, rel — старт в момент появления задачи (release или
    подача), up — в момент возврата узла, wait — другая причина (например, решение политики).
    `by` — задача, которая сделала старт возможным.
    """
    attempts = [a for a in attempts_from_log(log) if a.end is not None]
    done = {a.task: a for a in attempts if a.outcome == "DONE"}
    if not done:
        return []
    appeared = {r["task"]: r["t"] for r in log if r["type"] == "SUBMIT"}
    returned = {(r["worker"], r["t"]) for r in log if r["type"] == "WORKER_UP"}
    current = max(done.values(), key=lambda a: (_end(a), a.task))
    chain: list[dict[str, Any]] = []
    while True:
        link = _link(current)
        chain.append(link)
        cause, previous = _cause(current, tasks, attempts, done)
        link["cause"] = cause
        if previous is None:
            if cause == "wait":
                task = tasks.get(current.task)
                if task is not None and current.start == max(task.release, appeared.get(current.task, 0)):
                    link["cause"] = "rel"
                elif (current.worker, current.start) in returned:
                    link["cause"] = "up"
            break
        link["by"] = previous.task
        current = previous
    return chain[::-1]


def decompose(chain: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """makespan = dep + res + lost + release + wait.

    dep и res — работа звеньев, ставших возможными благодаря зависимости (и всех остальных звеньев) или
    освободившемуся месту; lost — работа прерванных попыток после последнего чекпоинта; release и wait — время
    до начала цепочки: задача ещё не появилась или ждала по другой причине. Звенья примыкают друг к другу без
    зазоров, поэтому сумма равна окончанию последнего звена.
    """
    out = {"dep": 0, "res": 0, "lost": 0, "release": 0, "wait": 0}
    for link in chain:
        out["res" if link["cause"] == "res" else "dep"] += link["work"]
        out["lost"] += link["lost"]
    if chain and chain[0]["start"] > 0:
        out["release" if chain[0]["cause"] == "rel" else "wait"] += chain[0]["start"]
    return out


def _cause(
    a: Attempt, tasks: Mapping[str, Task], attempts: Sequence[Attempt], done: Mapping[str, Attempt]
) -> tuple[str, Attempt | None]:
    if a.start == 0:
        return "origin", None
    task = tasks.get(a.task)
    deps = sorted(d for d in set(task.deps if task else ()) if d in done and _end(done[d]) == a.start)
    if deps:
        return "dep", done[deps[0]]
    for b in attempts:
        if b.task == a.task and b.attempt == a.attempt - 1 and b.end == a.start:
            return ("pre" if b.outcome == "PRE" else "fail"), b
    freed = sorted(
        (b for b in attempts if b.worker == a.worker and b.end == a.start and b.task != a.task), key=_key
    )
    if freed:
        return "res", freed[0]
    return "wait", None


def _link(a: Attempt) -> dict[str, Any]:
    elapsed = _end(a) - a.start
    work = elapsed if a.outcome == "DONE" else a.saved
    return {
        "task": a.task,
        "attempt": a.attempt,
        "worker": a.worker,
        "start": a.start,
        "end": _end(a),
        "work": work,
        "lost": elapsed - work,
    }


def _end(a: Attempt) -> int:
    return a.start if a.end is None else a.end  # в цепочку попадают только закончившиеся попытки


def _key(a: Attempt) -> tuple[str, int]:
    return (a.task, a.attempt)
