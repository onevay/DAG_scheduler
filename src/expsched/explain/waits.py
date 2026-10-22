"""Интервалы ожидания задач: «почему и сколько задача ждала»."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def wait_intervals(log: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Интервалы `{code, from, to, detail}` по задачам.

    Интервал начинается записью WAIT и длится до следующей записи WAIT этой задачи, её старта или конца
    журнала. Интервалы нулевой длины не попадают в результат.
    """
    end_of_run = max((r["t"] for r in log), default=0)
    out: dict[str, list[dict[str, Any]]] = {}
    current: dict[str, dict[str, Any]] = {}

    def close(task: str, t: int) -> None:
        interval = current.pop(task, None)
        if interval is not None and t > interval["from"]:
            out.setdefault(task, []).append({**interval, "to": t})

    for r in log:
        if r["type"] == "WAIT":
            close(r["task"], r["t"])
            current[r["task"]] = {"code": r["code"], "from": r["t"], "detail": r["detail"]}
        elif r["type"] == "START":
            close(r["task"], r["t"])
    for task in sorted(current):
        close(task, end_of_run)
    return out
