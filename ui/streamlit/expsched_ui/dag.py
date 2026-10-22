"""Граф зависимостей в формате DOT (Graphviz)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

NORMAL, SUBMITTED, PROBLEM = "#e8f0fe", "#fff4d6", "#f8d7da"


def problem_tasks(diagnostics: Iterable[Mapping[str, Any]]) -> set[str]:
    """Задачи, о которых есть ошибка (не предупреждение), включая все задачи найденных циклов."""
    out: set[str] = set()
    for d in diagnostics:
        if d.get("level") != "error":
            continue
        detail = d.get("detail", {})
        if "task" in detail:
            out.add(detail["task"])
        out.update(detail.get("cycle", []))
    return out


def dependency_dot(problem: Mapping[str, Any], highlight: Iterable[str] = ()) -> str:
    """Задачи — узлы графа, зависимости — стрелки от предшественника. Поданные позже задачи — жёлтые,
    задачи с ошибками — красные, ссылки на несуществующие задачи — пунктиром.
    """
    marked = set(highlight)
    tasks = [(t, NORMAL) for t in problem.get("tasks", [])]
    tasks += [(e["task"], SUBMITTED) for e in problem.get("events", []) if e["type"] == "submit_task"]
    known = {t["id"] for t, _ in tasks}
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];',
    ]
    for task, color in tasks:
        gpu = task.get("resources", {}).get("gpu", 0)
        fill = PROBLEM if task["id"] in marked else color
        lines.append(
            f'  "{task["id"]}" [label="{task["id"]}\\n{task["duration"]} · {gpu} GPU", fillcolor="{fill}"];'
        )
    for task, _ in tasks:
        for dep in dict.fromkeys(task.get("dependencies", [])):  # повтор зависимости — одна стрелка
            if dep not in known:
                lines.append(f'  "{dep}" [style="dashed", fillcolor="{PROBLEM}"];')
            lines.append(f'  "{dep}" -> "{task["id"]}";')
    lines.append("}")
    return "\n".join(lines)
