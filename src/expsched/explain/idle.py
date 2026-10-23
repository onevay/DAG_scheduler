"""Простой GPU по узлам и причинам, восстановленный из журнала прогона."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from typing import Any

from expsched.domain.models import Problem
from expsched.domain.resources import Resources

CODES = ("IDLE_DOWN", "IDLE_NO_READY", "IDLE_FRAGMENTATION", "IDLE_RESERVED", "IDLE_POLICY")
Shape = tuple[Resources, tuple[str, ...]]  # требования задачи и её допустимые узлы


def idle_intervals(problem: Problem, log: Sequence[Mapping[str, Any]], horizon: int) -> list[dict[str, Any]]:
    """Интервалы `{worker, from, to, code, gpu, detail}` на [0, horizon), где на узле свободны GPU.

    Код — почему GPU свободны: IDLE_DOWN — узел недоступен; IDLE_NO_READY — нет готовых задач, которым
    подходит узел; IDLE_FRAGMENTATION — такие задачи есть, но ни одна не помещается в свободное;
    IDLE_RESERVED — помещается, но место держит резерв (`detail`: head, t_res); IDLE_POLICY — помещается,
    резерва нет, а политика её не запустила. `gpu` — сколько GPU свободно. Соседние интервалы с одинаковыми
    кодом и `gpu` склеены. Учитываются только GPU: на узле без GPU простоя нет.
    """
    tasks = problem.all_tasks()
    shape_of: dict[str, Shape] = {k: (t.resources, problem.eligible(t)) for k, t in tasks.items()}
    running: dict[str, tuple[str, Resources]] = {}
    ready: dict[Shape, int] = {}
    down: set[str] = set()
    reserved: dict[str, tuple[str, int]] = {}  # узел -> (задача, t_res)
    out: list[dict[str, Any]] = []
    last: dict[str, dict[str, Any]] = {}

    groups = [(t, list(rs)) for t, rs in itertools.groupby(log, key=lambda r: r["t"])]
    if not groups or groups[0][0] > 0:
        groups.insert(0, (0, []))
    for i, (t, records) in enumerate(groups):
        for r in records:
            _apply(r, shape_of, running, ready, down, reserved)
        end = min(horizon, groups[i + 1][0] if i + 1 < len(groups) else horizon)
        if end <= t:
            continue
        for worker in sorted(problem.workers):
            capacity = problem.workers[worker].capacity
            used = Resources()
            for w, need in running.values():
                if w == worker:
                    used = used + need
            free = capacity - used
            gpu = capacity.get("gpu") if worker in down else free.get("gpu")
            if gpu == 0:
                continue
            code, detail = _code(worker, free, down, ready, reserved)
            previous = last.get(worker)
            if (
                previous
                and previous["to"] == t
                and (previous["code"], previous["gpu"], previous["detail"])
                == (
                    code,
                    gpu,
                    detail,
                )
            ):
                previous["to"] = end
                continue
            interval = {"worker": worker, "from": t, "to": end, "code": code, "gpu": gpu, "detail": detail}
            out.append(interval)
            last[worker] = interval
    return out


def idle_by_code(intervals: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """GPU · время простоя по кодам; коды, которых не было, — с нулём."""
    out = dict.fromkeys(CODES, 0)
    for i in intervals:
        out[i["code"]] += i["gpu"] * (i["to"] - i["from"])
    return out


def _apply(
    r: Mapping[str, Any],
    shape_of: Mapping[str, Shape],
    running: dict[str, tuple[str, Resources]],
    ready: dict[Shape, int],
    down: set[str],
    reserved: dict[str, tuple[str, int]],
) -> None:
    kind = r["type"]
    if kind == "READY" and r["task"] in shape_of:
        shape = shape_of[r["task"]]
        ready[shape] = ready.get(shape, 0) + 1
    elif kind == "START":
        shape = shape_of[r["task"]]
        ready[shape] -= 1
        running[r["task"]] = (r["worker"], shape[0])
        for worker in [w for w, (head, _) in reserved.items() if head == r["task"]]:
            del reserved[worker]
    elif kind in ("FINISH", "INTERRUPTED"):
        running.pop(r["task"], None)
    elif kind == "WORKER_DOWN":
        down.add(r["worker"])
    elif kind == "WORKER_UP":
        down.discard(r["worker"])
    elif kind == "RESERVE":
        for worker in [w for w, (head, _) in reserved.items() if head == r["task"]]:
            del reserved[worker]
        reserved[r["worker"]] = (r["task"], r["t_res"])


def _code(
    worker: str,
    free: Resources,
    down: set[str],
    ready: Mapping[Shape, int],
    reserved: Mapping[str, tuple[str, int]],
) -> tuple[str, dict[str, Any]]:
    if worker in down:
        return "IDLE_DOWN", {}
    shapes = [need for (need, eligible), count in ready.items() if count > 0 and worker in eligible]
    if not shapes:
        return "IDLE_NO_READY", {}
    if not any(need.fits(free) for need in shapes):
        return "IDLE_FRAGMENTATION", {}
    if worker in reserved:
        head, t_res = reserved[worker]
        return "IDLE_RESERVED", {"head": head, "t_res": t_res}
    return "IDLE_POLICY", {}
