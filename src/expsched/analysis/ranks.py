"""Проходы по топологическому порядку: ранние и поздние сроки, ранги.

Все функции работают над остатками работы `remaining` (до старта симуляции это длительности).
Срок `None` означает «ограничения сверху нет»: ни у задачи, ни у её потомков нет дедлайна.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from expsched.domain.graph import TaskGraph
from expsched.domain.models import Task


def forward_pass(
    tasks: Mapping[str, Task],
    graph: TaskGraph,
    order: Sequence[str],
    remaining: Mapping[str, int],
    now: int,
) -> tuple[dict[str, int], dict[str, int]]:
    """EST_j = max(release_j, now, EFT предшественников); EFT_j = EST_j + R_j."""
    est: dict[str, int] = {}
    eft: dict[str, int] = {}
    for key in order:
        est[key] = max([tasks[key].release, now, *(eft[p] for p in graph.preds(key))])
        eft[key] = est[key] + remaining[key]
    return est, eft


def backward_pass(
    tasks: Mapping[str, Task],
    graph: TaskGraph,
    order: Sequence[str],
    remaining: Mapping[str, int],
) -> tuple[dict[str, int | None], dict[str, int | None], dict[str, int]]:
    """LFT_j = min(D_j, LST последователей); LST_j = LFT_j - R_j; rank_j = R_j + max rank последователей.

    rank (upward rank, bottom level) — длина самого длинного пути от задачи до стока, включая её саму.
    """
    lft: dict[str, int | None] = {}
    lst: dict[str, int | None] = {}
    rank: dict[str, int] = {}
    for key in reversed(order):
        limits = [tasks[key].deadline, *(lst[s] for s in graph.succs(key))]
        known = [x for x in limits if x is not None]
        latest_finish = min(known) if known else None
        lft[key] = latest_finish
        lst[key] = None if latest_finish is None else latest_finish - remaining[key]
        rank[key] = remaining[key] + max((rank[s] for s in graph.succs(key)), default=0)
    return lft, lst, rank
