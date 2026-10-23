"""Срезы сохранённого результата для ответов API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def explain_view(
    result: Mapping[str, Any], task: str | None = None, worker: str | None = None, t: int | None = None
) -> dict[str, Any]:
    """Объяснение прогона, суженное до задачи, узла и момента времени.

    Каждый список оставляет только то, что относится ко всем заданным условиям: звенья цепочки и попытки —
    этой задачи, на этом узле, идущие в момент `t`; ожидания — этой задачи в момент `t`; простой — этого узла
    в момент `t`. Ожидание — свойство задачи, простой — узла: при фильтре по узлу ожиданий в ответе нет, при
    фильтре по задаче нет простоя. Разложение makespan отдаётся целиком.
    """
    explain = result["explain"]

    def during(lo: int, hi: int | None) -> bool:
        return t is None or (lo <= t and (hi is None or t < hi))

    def matches(item: Mapping[str, Any]) -> bool:
        return (task is None or item.get("task") == task) and (worker is None or item.get("worker") == worker)

    waits = [
        {"task": key, **w}
        for key, summary in sorted(result["tasks"].items())
        for w in summary["waits"]
        if (task is None or key == task) and worker is None and during(w["from"], w["to"])
    ]
    return {
        "chain": [c for c in explain["chain"] if matches(c) and during(c["start"], c["end"])],
        "decomposition": explain["decomposition"],
        "attempts": [a for a in result["attempts"] if matches(a) and during(a["start"], a["end"])],
        "waits": waits,
        "idle": [
            i
            for i in explain["idle"]["intervals"]
            if task is None and (worker is None or i["worker"] == worker) and during(i["from"], i["to"])
        ],
    }
