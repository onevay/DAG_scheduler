"""Попытки выполнения задач, восстановленные из журнала прогона."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass
class Attempt:
    task: str
    attempt: int
    worker: str
    start: int
    resume_from: int  # сколько работы было сделано до этой попытки
    end: int | None = None  # None — попытка не закончилась


def attempts_from_log(log: Iterable[Mapping[str, Any]]) -> list[Attempt]:
    """Попытки в порядке стартов. Окончание без соответствующего старта пропускается."""
    out: list[Attempt] = []
    current: dict[tuple[str, int], Attempt] = {}
    for r in log:
        if r["type"] == "START":
            attempt = Attempt(r["task"], r["attempt"], r["worker"], r["t"], r["resume_from"])
            current[(attempt.task, attempt.attempt)] = attempt
            out.append(attempt)
        elif r["type"] == "FINISH" and (r["task"], r["attempt"]) in current:
            current.pop((r["task"], r["attempt"])).end = r["t"]
    return out
