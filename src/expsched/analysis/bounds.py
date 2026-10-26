"""Нижние границы makespan: по критическому пути, по загрузке ресурсов и по крупным задачам.

По ним оценивают расписание, когда оптимум неизвестен: gap = (makespan - LB) / LB. Отказы узлов не учтены.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from expsched.domain.models import Task, Worker


def path_bound(eft: Mapping[str, int], now: int) -> int:
    """Ни одна задача не закончится раньше своего самого раннего окончания (EFT)."""
    return max(eft.values(), default=now)


def energy_bounds(
    tasks: Mapping[str, Task], remaining: Mapping[str, int], workers: Iterable[Worker], now: int
) -> dict[str, int]:
    """По каждому ресурсу: вся работа (остаток × требование) должна уместиться в суммарную ёмкость узлов."""
    capacity: dict[str, int] = {}
    for worker in workers:
        for resource, amount in worker.capacity.items():
            capacity[resource] = capacity.get(resource, 0) + amount
    return {
        resource: now
        + _ceil_div(sum(remaining[k] * t.resources.get(resource) for k, t in tasks.items()), total)
        for resource, total in sorted(capacity.items())
    }


def threshold_bound(
    tasks: Mapping[str, Task], remaining: Mapping[str, int], workers: Iterable[Worker], now: int
) -> int:
    """Задачи, которым нужно не меньше θ единиц ресурса, конкурируют только за узлы, где его не меньше θ.

    Считается по каждому ресурсу задач, берётся наибольшая граница.
    """
    workers = list(workers)
    bound = now
    for resource in sorted({r for t in tasks.values() for r in t.resources}):
        for theta in sorted({t.resources.get(resource) for t in tasks.values()} - {0}):
            pool = sum(w.capacity.get(resource) for w in workers if w.capacity.get(resource) >= theta)
            work = sum(
                remaining[k] * t.resources.get(resource)
                for k, t in tasks.items()
                if t.resources.get(resource) >= theta
            )
            if pool:
                bound = max(bound, now + _ceil_div(work, pool))
    return bound


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)
