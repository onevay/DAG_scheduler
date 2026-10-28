"""Контракт входа (problem.json) на Pydantic v2 и единственный источник JSON Schema."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from expsched.contract.limits import MAX_BODY_BYTES, MAX_EDGES, MAX_TASKS, InputTooLarge
from expsched.domain.models import Event, EvType, Problem, Task, Worker
from expsched.domain.resources import Resources

ResMap = dict[
    Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")],
    Annotated[int, Field(ge=0)],
]
Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
PolicyName = Literal["fifo", "fifo_skip", "priority", "priority_backfill", "heft", "peft", "cpsat"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContainerSpec(Strict):
    """Что агент запускает для попытки задачи; ресурсы задачи становятся лимитами контейнера."""

    image: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._/:@-]{0,255}$")]
    command: Annotated[list[Annotated[str, Field(max_length=4096)]], Field(max_length=64)] = []
    env: Annotated[
        dict[
            Annotated[str, Field(pattern=r"^[A-Z_][A-Z0-9_]{0,63}$")], Annotated[str, Field(max_length=4096)]
        ],
        Field(max_length=64),
    ] = {}


class EstimateIn(Strict):
    """Работа задачи, когда точно она неизвестна: логнормальное с медианой и разбросом (σ логарифма)."""

    median: Annotated[float, Field(gt=0, le=10_000_000)]
    spread: Annotated[float, Field(ge=0, le=3)] | None = None  # None — по виду задачи


class ScalingIn(Strict):
    """Как длительность задачи зависит от числа её CPU/GPU: закон Амдала (доля последовательной части)
    или степенной (`n^-alpha`). Нужен, чтобы переносить наблюдения, сделанные при другом числе."""

    law: Literal["amdahl", "power"] = "amdahl"
    serial: Annotated[float, Field(ge=0, le=1)] | None = None
    alpha: Annotated[float, Field(ge=0, le=1.5)] | None = None


class TaskIn(Strict):
    id: Id
    kind: Literal["data", "train", "eval", "infer", "other"] = "other"
    # Работа: время на узле со скоростью 1. Нет — длительность неизвестна (только для прогона на агентах)
    duration: Annotated[int, Field(ge=1)] | None = None
    estimate: Annotated[float, Field(gt=0, le=10_000_000)] | EstimateIn | None = None
    scaling: ScalingIn | None = None
    resources: ResMap = {}
    priority: Annotated[int, Field(ge=0, le=10)] = 1
    deadline: Annotated[int, Field(ge=0)] | None = None
    release_time: Annotated[int, Field(ge=0)] = 0
    dependencies: list[Id] = []  # повторы и самоссылки здесь допустимы: о них сообщает validation
    preemptible: bool = False
    checkpoint_interval: Annotated[int, Field(ge=1)] | None = None
    max_retries: Annotated[int, Field(ge=0, le=20)] = 2
    constraints: dict[str, str] = {}
    container: ContainerSpec | None = None  # для прогона на агентах; без него — встроенная нагрузка-эмуляция


class WorkerIn(Strict):
    id: Id
    resources: ResMap
    labels: dict[str, str] = {}
    agent: Id | None = None  # агент, который запускает задачи этого узла при прогоне на агентах
    speed: Annotated[float, Field(gt=0, le=100)] = 1.0  # во сколько раз узел быстрее эталона


class EventIn(Strict):
    time: Annotated[int, Field(ge=0)]
    type: Literal["worker_down", "worker_up", "submit_task"]
    worker: Id | None = None
    task: TaskIn | None = None

    @model_validator(mode="after")
    def _payload(self) -> EventIn:
        need = "task" if self.type == "submit_task" else "worker"
        if getattr(self, need) is None:
            raise ValueError(f"{self.type} requires '{need}'")
        return self


class PolicyIn(Strict):
    name: PolicyName = "priority_backfill"
    objective: Literal["lexicographic", "weighted"] = "lexicographic"
    preemption: Literal["never", "deadline_only", "priority"] = "deadline_only"
    max_preemptions_per_task: Annotated[int, Field(ge=0)] = 1
    min_runtime_before_preempt: Annotated[int, Field(ge=0)] = 30
    aging: Annotated[int, Field(ge=1)] | None = 360
    # Прогноз перепланирования стоит как весь остаток прогона на каждом внешнем событии: на больших входах
    # он не строится. 0 — не строить никогда.
    max_forecast_tasks: Annotated[int, Field(ge=0)] = 1000
    # Только для cpsat: бюджет решателя на одно решение в детерминированном времени (условных секундах).
    # Бюджет, а не часы, делает журнал воспроизводимым на любой машине.
    time_limit: Annotated[float, Field(gt=0, le=300)] = 5.0
    validation_mode: Literal["strict", "lenient"] = "lenient"


class ClusterIn(Strict):
    workers: list[WorkerIn] = Field(min_length=1)


class ProblemIn(Strict):
    schema_version: Literal["1.0"] = "1.0"
    name: Annotated[str, Field(min_length=1, max_length=128)] | None = None  # как показывать вход в списках
    seed: int = 0
    time_unit: Literal["minute", "hour", "second"] = "minute"
    cluster: ClusterIn
    tasks: list[TaskIn] = Field(max_length=MAX_TASKS)
    events: list[EventIn] = []
    policy: PolicyIn = PolicyIn()
    mode: Literal["simulate", "emulate", "shadow", "replay"] = "simulate"
    # Для прогона на агентах: длительность единицы времени модели, мс (1000 — единица модели идёт секунду).
    time_scale_ms: Annotated[int, Field(ge=1, le=3_600_000)] = 1000

    @model_validator(mode="after")
    def _ids_and_limits(self) -> ProblemIn:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task id")
        if sum(len(t.dependencies) for t in self.tasks) > MAX_EDGES:
            raise ValueError("too many edges")
        if self.mode not in ("emulate", "shadow"):
            unknown = [t.id for t in self.all_tasks() if t.duration is None]
            if unknown:
                raise ValueError(
                    f"tasks without duration: {', '.join(unknown[:5])}; give a duration, "
                    "or run on agents (mode emulate or shadow) with an estimate"
                )
        return self

    def all_tasks(self) -> list[TaskIn]:
        return [*self.tasks, *(e.task for e in self.events if e.task)]


def to_task(t: TaskIn) -> Task:
    if t.duration is None:
        raise ValueError(f"task {t.id!r} has no duration: resolve estimates first (expsched.estimate)")
    return Task(
        id=t.id,
        duration=t.duration,
        resources=Resources(t.resources),
        kind=t.kind,
        priority=t.priority,
        deadline=t.deadline,
        release=t.release_time,
        deps=tuple(t.dependencies),
        preemptible=t.preemptible,
        ckpt=t.checkpoint_interval,
        max_retries=t.max_retries,
        constraints=tuple(sorted(t.constraints.items())),
    )


def to_domain(p: ProblemIn) -> Problem:
    events = [Event(e.time, EvType(e.type), e.worker, to_task(e.task) if e.task else None) for e in p.events]
    workers = {
        w.id: Worker(w.id, Resources(w.resources), tuple(sorted(w.labels.items())), w.speed)
        for w in p.cluster.workers
    }
    return Problem({t.id: to_task(t) for t in p.tasks}, workers, events, p.seed)


def load(raw: bytes, max_bytes: int = MAX_BODY_BYTES) -> ProblemIn:
    """Разбор недоверенного входа: сначала размер, затем JSON и схема. InputTooLarge при превышении лимита."""
    if len(raw) > max_bytes:
        raise InputTooLarge(f"input is {len(raw)} bytes, limit is {max_bytes}")
    return ProblemIn.model_validate_json(raw)


def dump_schema() -> str:
    return json.dumps(ProblemIn.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
