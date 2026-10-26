"""Что если: вход с одним изменением и разница метрик с исходным прогоном."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import Field

from expsched.contract.io_models import Id, ProblemIn, Strict, TaskIn, WorkerIn


class AddWorker(Strict):
    kind: Literal["add_worker"]
    worker: WorkerIn


class SetPreemptible(Strict):
    kind: Literal["set_preemptible"]
    task: Id
    preemptible: bool = True


class SetPriority(Strict):
    kind: Literal["set_priority"]
    task: Id
    priority: Annotated[int, Field(ge=0, le=10)]


class SetDeadline(Strict):
    kind: Literal["set_deadline"]
    task: Id
    deadline: Annotated[int, Field(ge=0)] | None  # None — снять дедлайн


class SetCheckpoint(Strict):
    kind: Literal["set_checkpoint"]
    task: Id
    checkpoint_interval: Annotated[int, Field(ge=1)] | None  # None — без чекпоинтов


class RemoveFailures(Strict):
    """Убрать отказы и возвраты узла: как прошёл бы прогон, если бы узел не падал."""

    kind: Literal["remove_failures"]
    worker: Id


Intervention = Annotated[
    AddWorker | SetPreemptible | SetPriority | SetDeadline | SetCheckpoint | RemoveFailures,
    Field(discriminator="kind"),
]

# Метрики, по которым считается разница; меньше — лучше, кроме загрузки. Загрузка и потери — по ресурсам:
# в разнице они получают ключи utilization_<ресурс> и lost_work_<ресурс>.
COMPARED = ("makespan", "deadline_misses", "weighted_tardiness", "mean_wait")
PER_RESOURCE = ("utilization", "lost_work")


def apply(problem: ProblemIn, change: Intervention) -> ProblemIn:
    """Вход с изменением. ValueError — изменение ссылается на задачу или узел, которых во входе нет,
    или добавляет узел с уже занятым id.
    """
    workers = {w.id for w in problem.cluster.workers}
    match change:
        case AddWorker(worker=worker):
            if worker.id in workers:
                raise ValueError(f"worker {worker.id!r} already exists")
            cluster = problem.cluster.model_copy(update={"workers": [*problem.cluster.workers, worker]})
            return problem.model_copy(update={"cluster": cluster})
        case RemoveFailures(worker=worker_id):
            if worker_id not in workers:
                raise ValueError(f"no worker {worker_id!r}")
            kept = [e for e in problem.events if not (e.worker == worker_id and e.type != "submit_task")]
            return problem.model_copy(update={"events": kept})
        case SetPreemptible(task=task, preemptible=preemptible):
            return _with_task(problem, task, {"preemptible": preemptible})
        case SetPriority(task=task, priority=priority):
            return _with_task(problem, task, {"priority": priority})
        case SetDeadline(task=task, deadline=deadline):
            return _with_task(problem, task, {"deadline": deadline})
        case SetCheckpoint(task=task, checkpoint_interval=interval):
            return _with_task(problem, task, {"checkpoint_interval": interval})


def deltas(
    base: Mapping[str, Any] | None, variant: Mapping[str, Any] | None
) -> dict[str, int | float] | None:
    """Разница метрик варианта и исходного прогона; None, если один из них не симулировался."""
    if base is None or variant is None:
        return None
    pairs = [(key, _value(base, key), _value(variant, key)) for key in COMPARED]
    for metric in PER_RESOURCE:
        for resource in sorted(base.get(metric, {}).keys() & variant.get(metric, {}).keys()):
            pairs.append((f"{metric}_{resource}", base[metric][resource], variant[metric][resource]))
    out: dict[str, int | float] = {}
    for key, a, b in pairs:
        if a is not None and b is not None:
            out[key] = round(b - a, 4) if isinstance(a, float) or isinstance(b, float) else b - a
    return out


def _with_task(problem: ProblemIn, task_id: str, fields: dict[str, Any]) -> ProblemIn:
    """Меняет поля задачи входа или задачи, поданной событием."""

    def changed(task: TaskIn) -> TaskIn:
        return task.model_copy(update=fields) if task.id == task_id else task

    tasks = [changed(t) for t in problem.tasks]
    events = [e.model_copy(update={"task": changed(e.task)}) if e.task else e for e in problem.events]
    known = {t.id for t in problem.tasks} | {e.task.id for e in problem.events if e.task}
    if task_id not in known:
        raise ValueError(f"no task {task_id!r}")
    return problem.model_copy(update={"tasks": tasks, "events": events})


def _value(scores: Mapping[str, Any], key: str) -> int | float | None:
    value = scores.get(key)
    if isinstance(value, dict):
        return sum(value.values())  # пропуски дедлайнов — по классам
    return value if isinstance(value, int | float) else None
