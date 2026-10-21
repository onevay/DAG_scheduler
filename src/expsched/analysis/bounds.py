"""Нижние границы makespan: путевая, энергетическая по ресурсам, пороговая по GPU.

Границы нужны, чтобы оценивать качество там, где оптимум неизвестен: gap = (Cmax - LB) / LB.
Отказы узлов не учитываются: это границы для кластера без отказов.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from expsched.domain.models import Task, Worker

THRESHOLD_RESOURCE = "gpu"


def path_bound(eft: Mapping[str, int], now: int) -> int:
    """Критический путь: ни одна задача не закончится раньше своего EFT."""
    return max(eft.values(), default=now)


def energy_bounds(
    tasks: Mapping[str, Task], remaining: Mapping[str, int], workers: Iterable[Worker], now: int
) -> dict[str, int]:
    """По каждому ресурсу: суммарная «энергия» R·r не может превысить суммарную ёмкость за время bound."""
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
    """Задачи, которым нужно не меньше θ GPU, конкурируют только за узлы, где GPU не меньше θ."""
    workers = list(workers)
    bound = now
    for theta in sorted({t.resources.get(THRESHOLD_RESOURCE) for t in tasks.values()} - {0}):
        pool = sum(
            w.capacity.get(THRESHOLD_RESOURCE) for w in workers if w.capacity.get(THRESHOLD_RESOURCE) >= theta
        )
        work = sum(
            remaining[k] * t.resources.get(THRESHOLD_RESOURCE)
            for k, t in tasks.items()
            if t.resources.get(THRESHOLD_RESOURCE) >= theta
        )
        if pool:
            bound = max(bound, now + _ceil_div(work, pool))
    return bound


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)
