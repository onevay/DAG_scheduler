"""Результат прогона в виде строк для таблиц и диаграммы Ганта."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


def gantt_bars(result: Mapping[str, Any], problem: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Полосы попыток с дорожкой узла: задачи, идущие на узле одновременно, попадают на разные дорожки."""
    tasks = {t["id"]: t for t in problem.get("tasks", [])}
    submitted = {e["task"]["id"]: e["task"] for e in problem.get("events", []) if e["type"] == "submit_task"}
    summary = result["tasks"]
    bars = []
    for a in result["attempts"]:
        task = tasks.get(a["task"]) or submitted.get(a["task"], {})
        bars.append(
            {
                "task": a["task"],
                "worker": a["worker"],
                "start": a["start"],
                "end": a["end"] if a["end"] is not None else a["start"],
                "kind": task.get("kind", "other"),
                "submitted": a["task"] in submitted,
                "late": (summary.get(a["task"], {}).get("tardiness") or 0) > 0,
            }
        )
    return _assign_lanes(bars)


def task_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Итог по задачам: сколько ждала и почему дольше всего."""
    rows = []
    for key, task in result["tasks"].items():
        totals: dict[str, int] = {}
        for w in task["waits"]:
            totals[w["code"]] = totals.get(w["code"], 0) + w["to"] - w["from"]
        rows.append(
            {
                "task": key,
                "status": task["status"],
                "start": task["start"],
                "finish": task["finish"],
                "deadline": task["deadline"],
                "tardiness": task["tardiness"],
                "waited": sum(totals.values()),
                "main_reason": max(totals, key=lambda c: (totals[c], c)) if totals else "",
            }
        )
    return rows


def wait_rows(result: Mapping[str, Any], task: str) -> list[dict[str, Any]]:
    return [
        {
            "from": w["from"],
            "to": w["to"],
            "duration": w["to"] - w["from"],
            "reason": w["code"],
            "detail": json.dumps(w["detail"], ensure_ascii=False),
        }
        for w in result["tasks"][task]["waits"]
    ]


def comparison_rows(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Сводка нескольких прогонов одного входа: по строке на политику."""
    rows = []
    for policy, result in results.items():
        m = result["metrics"] or {}
        rows.append(
            {
                "policy": policy,
                "status": result["status"],
                "makespan": m.get("makespan"),
                "gap_to_lb": m.get("gap_to_lb"),
                "deadline_misses": sum((m.get("deadline_misses") or {}).values()),
                "weighted_tardiness": m.get("weighted_tardiness"),
                "utilization_gpu": m.get("utilization_gpu"),
                "mean_wait": m.get("mean_wait"),
                "violations": len(result["violations"]),
            }
        )
    return rows


def events_jsonl(events: Iterable[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in events)


def _assign_lanes(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Жадная раскладка: попытка занимает первую дорожку узла, освободившуюся к её старту."""
    lane_ends: dict[str, list[int]] = {}
    for bar in sorted(bars, key=lambda b: (b["worker"], b["start"], b["task"])):
        ends = lane_ends.setdefault(bar["worker"], [])
        lane = next((i for i, end in enumerate(ends) if end <= bar["start"]), len(ends))
        if lane == len(ends):
            ends.append(bar["end"])
        else:
            ends[lane] = bar["end"]
        bar["lane_index"] = lane
        bar["lane"] = f"{bar['worker']} · {lane + 1}"
    return sorted(bars, key=lambda b: (b["worker"], b["lane_index"], b["start"]))
