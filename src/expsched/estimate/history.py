"""История прогонов на агентах: наблюдённая работа задач и подгонка оценок по ней.

Наблюдение — работа законченной задачи: сохранённое прежними попытками плюс время последней попытки на
скорости её узла, и число её CPU/GPU. Работа встроенной нагрузки — в единицах модели (она идёт по часам
прогона), работа своего контейнера — в миллисекундах стены: так её можно перенести на прогон с другим
масштабом времени. По наблюдениям одного ключа (`key`) подгоняются медиана и разброс работы; если задачу
запускали при разном числе CPU/GPU, доля последовательной части по Амдалу выбирается так, чтобы работа,
пересчитанная на одно ядро, разбросалась меньше всего.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from expsched.contract.io_models import MAX_SPREAD, ProblemIn, ScalingIn, TaskIn
from expsched.estimate import KIND, Estimate, key, time_factor, units

MIN_SPREAD = 0.05  # одно-два наблюдения не делают работу известной точно


@dataclass(frozen=True)
class Observation:
    work: float
    n: int  # число основного ресурса задачи
    unit: Literal["model", "ms"]

    def to_dict(self) -> dict[str, Any]:
        return {"work": round(self.work, 3), "n": self.n, "unit": self.unit}


def observe(problem: ProblemIn, result: Mapping[str, Any]) -> dict[str, list[Observation]]:
    """Наблюдения законченного прогона на агентах; `problem` — его вход как есть, без подставленных оценок."""
    speed = {w.id: w.speed for w in problem.cluster.workers}
    tasks = {t.id: t for t in problem.all_tasks()}
    out: dict[str, list[Observation]] = {}
    for a in result.get("attempts", []):
        task = tasks.get(a["task"])
        if task is None or a["outcome"] != "DONE" or a["end"] is None:
            continue
        work = a["resume_from"] + (a["end"] - a["start"]) * speed.get(a["worker"], 1)
        own = task.container is not None
        obs = Observation(
            work * problem.time_scale_ms if own else work, units(task.resources), "ms" if own else "model"
        )
        out.setdefault(key(task), []).append(obs)
    return out


def fit(observations: Sequence[Observation], task: TaskIn, time_scale_ms: int) -> Estimate | None:
    """Оценка работы задачи по наблюдениям её ключа, пересчитанная на её число CPU/GPU и масштаб прогона."""
    seen = [(o.work / time_scale_ms if o.unit == "ms" else o.work, o.n) for o in observations if o.work > 0]
    if not seen:
        return None
    scaling = task.scaling or ScalingIn()
    if task.scaling is None and len({n for _, n in seen}) >= 2:
        scaling = ScalingIn(law="amdahl", serial=_serial(seen, task.kind))
    base = [math.log(w / time_factor(scaling, task.kind, n)) for w, n in seen]  # работа на одном ядре
    mu = statistics.fmean(base)
    spread = statistics.stdev(base) if len(base) >= 2 else KIND[task.kind][1] / 2
    median = math.exp(mu) * time_factor(scaling, task.kind, units(task.resources))
    return Estimate(median, min(MAX_SPREAD, max(MIN_SPREAD, spread)), "history")


def _serial(seen: Sequence[tuple[float, int]], kind: str) -> float:
    """Доля последовательной части, при которой работа на одном ядре по наблюдениям ровнее всего."""

    def scatter(serial: float) -> float:
        law = ScalingIn(law="amdahl", serial=serial)
        return statistics.pvariance([math.log(w / time_factor(law, kind, n)) for w, n in seen])

    return min((s / 100 for s in range(101)), key=scatter)


def estimates(history: Mapping[str, Sequence[Observation]], problem: ProblemIn) -> dict[str, Estimate]:
    """Оценки по истории для задач входа без длительности, по их ключам."""
    out: dict[str, Estimate] = {}
    for task in problem.all_tasks():
        obs = history.get(key(task)) if task.duration is None else None
        if obs and (found := fit(obs, task, problem.time_scale_ms)) is not None:
            out[key(task)] = found
    return out


def pinned(problem: ProblemIn, recorded: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Estimate]:
    """Оценки, которые задачи без длительности получили в прогоне (`estimates` результата), с той же работой
    и тем же разбросом: проигрывание повторяет те же решения, даже если история с тех пор изменилась."""
    out: dict[str, Estimate] = {}
    for t in problem.all_tasks():
        rec = (recorded or {}).get(t.id)
        if t.duration is None and rec:
            out[key(t)] = Estimate(
                float(rec["median"]), float(rec["spread"]), rec["source"], int(rec["work"])
            )
    return out
