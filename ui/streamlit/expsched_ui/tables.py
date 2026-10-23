"""Вход планировщика (problem.json) в виде таблиц для редактирования и обратно.

Задачи и узлы собираются из строк заново: ресурсы, кроме gpu, cpu и ram_gb, в таблицах не показываются
и при сборке теряются. Пустая ячейка — поле не задано.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

RESOURCES = ("gpu", "cpu", "ram_gb")
WORKER_COLUMNS = ("id", *RESOURCES, "labels")
TASK_COLUMNS = (
    "id",
    "kind",
    "duration",
    *RESOURCES,
    "priority",
    "deadline",
    "release_time",
    "dependencies",
    "preemptible",
    "checkpoint_interval",
    "max_retries",
    "constraints",
)
SUBMIT_COLUMNS = ("time", *TASK_COLUMNS)
WORKER_EVENT_COLUMNS = ("time", "worker", "event")
WORKER_EVENTS = {"down": "worker_down", "up": "worker_up"}  # значение в таблице -> тип события во входе
# Параметры политики, которые задаются в интерфейсе; имя политики — отдельно, ключом "policy".
POLICY_SETTINGS = (
    "aging",
    "validation_mode",
    "preemption",
    "max_preemptions_per_task",
    "min_runtime_before_preempt",
    "max_forecast_tasks",
)

EMPTY_PROBLEM: dict[str, Any] = {
    "cluster": {"workers": [{"id": "node-1", "resources": {"gpu": 4}}]},
    "tasks": [],
    "policy": {"name": "priority"},
}

Row = Mapping[str, Any]


def workers_rows(problem: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": w["id"], **_resource_cells(w.get("resources", {})), "labels": _pairs_text(w.get("labels", {}))}
        for w in problem["cluster"]["workers"]
    ]


def tasks_rows(problem: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_task_row(t) for t in problem.get("tasks", [])]


def submits_rows(problem: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Задачи, подаваемые во время прогона."""
    return [
        {"time": e["time"], **_task_row(e["task"])}
        for e in problem.get("events", [])
        if e["type"] == "submit_task"
    ]


def worker_events_rows(problem: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Отказы и возвраты узлов."""
    kinds = {v: k for k, v in WORKER_EVENTS.items()}
    return [
        {"time": e["time"], "worker": e["worker"], "event": kinds[e["type"]]}
        for e in problem.get("events", [])
        if e["type"] in kinds
    ]


def build_problem(
    workers: Iterable[Row],
    tasks: Iterable[Row],
    submits: Iterable[Row],
    worker_events: Iterable[Row],
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """problem.json из строк таблиц. Строки без id (у событий узла — без узла) пропускаются: это
    незаполненные новые строки.
    """
    node_events = [
        {"time": _number(r.get("time")) or 0, "type": WORKER_EVENTS[r["event"]], "worker": _text(r["worker"])}
        for r in worker_events
        if _text(r.get("worker")) and r.get("event") in WORKER_EVENTS
    ]
    task_events = [
        {"time": _number(r.get("time")) or 0, "type": "submit_task", "task": _task(r)}
        for r in submits
        if _text(r.get("id"))
    ]
    return {
        "time_unit": settings["time_unit"],
        "cluster": {"workers": [_worker(r) for r in workers if _text(r.get("id"))]},
        "tasks": [_task(r) for r in tasks if _text(r.get("id"))],
        "events": node_events + task_events,
        "policy": {"name": settings["policy"], **{k: settings[k] for k in POLICY_SETTINGS}},
    }


def _task_row(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "kind": task.get("kind", "other"),
        "duration": task["duration"],
        **_resource_cells(task.get("resources", {})),
        "priority": task.get("priority", 1),
        "deadline": task.get("deadline"),
        "release_time": task.get("release_time", 0),
        "dependencies": ", ".join(task.get("dependencies", [])),
        "preemptible": task.get("preemptible", False),
        "checkpoint_interval": task.get("checkpoint_interval"),
        "max_retries": task.get("max_retries", 2),
        "constraints": _pairs_text(task.get("constraints", {})),
    }


def _worker(row: Row) -> dict[str, Any]:
    return {"id": _text(row["id"]), "resources": _resources(row), "labels": _parse_pairs(row.get("labels"))}


def _task(row: Row) -> dict[str, Any]:
    priority = _number(row.get("priority"))
    preemptible = row.get("preemptible")
    task: dict[str, Any] = {
        "id": _text(row["id"]),
        "kind": _text(row.get("kind")) or "other",
        "duration": _number(row.get("duration")),
        "resources": _resources(row),
        "priority": 1 if priority is None else priority,
        "release_time": _number(row.get("release_time")) or 0,
        "dependencies": [d.strip() for d in _text(row.get("dependencies")).split(",") if d.strip()],
        "preemptible": False if _missing(preemptible) else bool(preemptible),
        "constraints": _parse_pairs(row.get("constraints")),
    }
    for field in ("deadline", "checkpoint_interval", "max_retries"):
        if _number(row.get(field)) is not None:
            task[field] = _number(row.get(field))
    return task


def _resource_cells(resources: Mapping[str, int]) -> dict[str, int]:
    return {r: resources.get(r, 0) for r in RESOURCES}


def _resources(row: Row) -> dict[str, int]:
    return {r: n for r in RESOURCES if (n := _number(row.get(r))) is not None and n > 0}


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value: Any) -> int | None:
    """Целое из ячейки; редакторы таблиц отдают числа как float, а пустые ячейки — как None или NaN."""
    if _missing(value) or value == "":
        return None
    return int(value)


def _text(value: Any) -> str:
    return "" if _missing(value) else str(value).strip()


def _pairs_text(pairs: Mapping[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in pairs.items())


def _parse_pairs(text: Any) -> dict[str, str]:
    """«gpu_type=a100, zone=b» -> словарь; элемент без «=» — ключ с пустым значением."""
    out = {}
    for item in _text(text).split(","):
        key, _, value = item.partition("=")
        if key.strip():
            out[key.strip()] = value.strip()
    return out
