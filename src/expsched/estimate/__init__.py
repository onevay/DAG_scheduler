"""Оценки работы задач, длительность которых заранее неизвестна.

Работа задачи `W` — её время на узле со скоростью 1 при числе CPU/GPU как во входе. Неизвестная `W` —
логнормальная величина: медиана и разброс (σ логарифма). Источники по порядку: оценка во входе (`estimate`),
история прогонов той же задачи, известные длительности задач того же вида во входе, умолчание по виду.
Для решений берутся среднее (ранги, прогноз), квантиль (резервы) и средняя остаточная жизнь — сколько ещё
ждать попытку, которая уже идёт дольше ожидаемого.

Закон ускорения переносит работу, наблюдённую при другом числе CPU/GPU: Амдал `g(n) = s + (1 − s)/n` или
степенной `g(n) = n^−α`; время ∝ g(n).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from expsched.contract.io_models import EstimateIn, ProblemIn, ScalingIn, TaskIn

Source = Literal["estimate", "history", "similar", "kind"]
NORMAL = statistics.NormalDist()

# Умолчания по виду задачи: медиана работы, разброс и доля последовательной части (Амдал)
KIND: dict[str, tuple[float, float, float]] = {
    "data": (10, 0.3, 0.5),
    "train": (60, 0.6, 0.1),
    "eval": (10, 0.3, 0.3),
    "infer": (20, 0.4, 0.2),
    "other": (10, 0.5, 0.5),
}


@dataclass(frozen=True)
class Estimate:
    median: float
    spread: float  # σ логарифма; 0 — работа известна точно
    source: Source

    @property
    def mean(self) -> float:
        return self.median * math.exp(self.spread**2 / 2)

    def quantile(self, p: float) -> float:
        return self.median * math.exp(self.spread * NORMAL.inv_cdf(p)) if self.spread else self.median

    def residual(self, done: float) -> float:
        """Ожидаемая работа целиком, если уже сделано `done`, а попытка ещё идёт: E[W | W > done]."""
        if self.spread == 0 or done <= 0:
            return max(self.mean, done)
        mu, sigma = math.log(self.median), self.spread
        tail = 1 - NORMAL.cdf((math.log(done) - mu) / sigma)
        if tail < 1e-12:  # далеко за хвостом: распределение уже ничего не говорит
            return done * 1.5
        return self.mean * NORMAL.cdf((mu + sigma**2 - math.log(done)) / sigma) / tail

    def work(self) -> int:
        """Работа для движка: округлённое вверх среднее."""
        return max(1, math.ceil(self.mean - 1e-9))

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "median": round(self.median, 3),
            "spread": round(self.spread, 3),
            "mean": round(self.mean, 3),
            "p80": round(self.quantile(0.8), 3),
            "work": self.work(),
            "source": self.source,
        }


def units(resources: Mapping[str, int]) -> int:
    """Число основного ресурса задачи: GPU, если они есть, иначе ядра; без ресурсов — 1."""
    return max(1, resources.get("gpu", 0) or resources.get("cpu", 0) or 1)


def time_factor(scaling: ScalingIn | None, kind: str, n: int) -> float:
    """g(n): во сколько раз время при `n` CPU/GPU меньше времени при одном."""
    law = scaling or ScalingIn()
    if law.law == "power":
        return float(n ** -(law.alpha if law.alpha is not None else 0.8))
    serial = law.serial if law.serial is not None else KIND[kind][2]
    return serial + (1 - serial) / n


def rescale(work: float, task: TaskIn, n_seen: int) -> float:
    """Работа, наблюдённая при `n_seen` CPU/GPU, пересчитанная на число из входа задачи."""
    n = units(task.resources)
    return work * time_factor(task.scaling, task.kind, n) / time_factor(task.scaling, task.kind, n_seen)


def key(task: TaskIn) -> str:
    """Чем задача узнаётся в истории: образ и команда её контейнера, иначе id и вид."""
    if task.container:
        return f"image:{task.container.image} {' '.join(task.container.command)}".strip()
    return f"task:{task.id}:{task.kind}"


def estimate_for(
    task: TaskIn, similar: Mapping[str, list[int]], history: Mapping[str, Estimate] | None = None
) -> Estimate:
    spread = KIND[task.kind][1]
    if isinstance(task.estimate, EstimateIn):
        return Estimate(
            task.estimate.median,
            task.estimate.spread if task.estimate.spread is not None else spread,
            "estimate",
        )
    if task.estimate is not None:
        return Estimate(float(task.estimate), spread, "estimate")
    if history and key(task) in history:
        return history[key(task)]
    if similar.get(task.kind):
        return Estimate(float(statistics.median(similar[task.kind])), spread, "similar")
    return Estimate(KIND[task.kind][0], spread, "kind")


def resolve(
    problem: ProblemIn, history: Mapping[str, Estimate] | None = None
) -> tuple[ProblemIn, dict[str, Estimate]]:
    """Вход, где у каждой задачи есть длительность (у неизвестных — ожидаемая работа), и оценки этих задач."""
    tasks = problem.all_tasks()
    if all(t.duration is not None for t in tasks):
        return problem, {}
    similar: dict[str, list[int]] = {}
    for t in tasks:
        if t.duration is not None:
            similar.setdefault(t.kind, []).append(t.duration)
    found = {t.id: estimate_for(t, similar, history) for t in tasks if t.duration is None}

    def filled(t: TaskIn) -> TaskIn:
        return t.model_copy(update={"duration": found[t.id].work()}) if t.id in found else t

    events = [e.model_copy(update={"task": filled(e.task)}) if e.task else e for e in problem.events]
    resolved = problem.model_copy(update={"tasks": [filled(t) for t in problem.tasks], "events": events})
    return resolved, found
