"""Попытки выполнения задач и интервалы недоступности узлов, восстановленные из журнала прогона."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Outcome = Literal["DONE", "INT", "PRE"]  # завершилась; прервана отказом узла; вытеснена


@dataclass
class Attempt:
    task: str
    attempt: int
    worker: str
    start: int
    resume_from: int  # сколько работы было сделано до этой попытки
    end: int | None = None  # None — попытка не закончилась
    outcome: Outcome | None = None
    saved: int = 0  # работа прерванной попытки, сохранённая чекпоинтом


def attempts_from_log(log: Iterable[Mapping[str, Any]]) -> list[Attempt]:
    """Попытки в порядке стартов. Окончание или прерывание без соответствующего старта пропускается."""
    out: list[Attempt] = []
    current: dict[tuple[str, int], Attempt] = {}
    for r in log:
        key = (r.get("task"), r.get("attempt"))
        if r["type"] == "START":
            attempt = Attempt(r["task"], r["attempt"], r["worker"], r["t"], r["resume_from"])
            current[(attempt.task, attempt.attempt)] = attempt
            out.append(attempt)
        elif r["type"] == "FINISH" and key in current:
            attempt = current.pop(key)
            attempt.end, attempt.outcome = r["t"], "DONE"
        elif r["type"] == "INTERRUPTED" and key in current:
            attempt = current.pop(key)
            attempt.end, attempt.saved = r["t"], r["saved"]
            attempt.outcome = "PRE" if r["cause"] == "PREEMPT" else "INT"
    return out


def down_intervals(log: Iterable[Mapping[str, Any]]) -> dict[str, list[tuple[int, int | None]]]:
    """Интервалы недоступности узлов [отказ, возврат); None — узел так и не вернулся."""
    out: dict[str, list[tuple[int, int | None]]] = {}
    since: dict[str, int] = {}
    for r in log:
        if r["type"] == "WORKER_DOWN":
            since[r["worker"]] = r["t"]
        elif r["type"] == "WORKER_UP" and r["worker"] in since:
            out.setdefault(r["worker"], []).append((since.pop(r["worker"]), r["t"]))
    for worker, start in since.items():
        out.setdefault(worker, []).append((start, None))
    return out
