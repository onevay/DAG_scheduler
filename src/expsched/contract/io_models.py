"""Контракт входа (problem.json) на Pydantic v2 и единственный источник JSON Schema."""

from __future__ import annotations

import json
import sys
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


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskIn(Strict):
    id: Id
    kind: Literal["data", "train", "eval", "infer", "other"] = "other"
    duration: Annotated[int, Field(ge=1)]  # E070
    resources: ResMap = {}
    priority: Annotated[int, Field(ge=0, le=10)] = 1
    deadline: Annotated[int, Field(ge=0)] | None = None
    release_time: Annotated[int, Field(ge=0)] = 0
    dependencies: list[Id] = []  # дубликаты и самоссылки разбирает валидация графа (W060, E011)
    preemptible: bool = False
    checkpoint_interval: Annotated[int, Field(ge=1)] | None = None
    max_retries: Annotated[int, Field(ge=0, le=20)] = 2
    constraints: dict[str, str] = {}


class WorkerIn(Strict):
    id: Id
    resources: ResMap
    labels: dict[str, str] = {}


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
    name: Literal["fifo", "fifo_skip", "priority", "priority_backfill", "cpsat"] = "priority_backfill"
    objective: Literal["lexicographic", "weighted"] = "lexicographic"
    preemption: Literal["never", "deadline_only", "priority"] = "deadline_only"
    max_preemptions_per_task: Annotated[int, Field(ge=0)] = 1
    min_runtime_before_preempt: Annotated[int, Field(ge=0)] = 30
    aging: Annotated[int, Field(ge=1)] | None = 360
    validation_mode: Literal["strict", "lenient"] = "lenient"


class ClusterIn(Strict):
    workers: list[WorkerIn] = Field(min_length=1)


class ProblemIn(Strict):
    schema_version: Literal["1.0"] = "1.0"
    seed: int = 0
    time_unit: Literal["minute", "hour", "second"] = "minute"
    cluster: ClusterIn
    tasks: list[TaskIn] = Field(max_length=MAX_TASKS)
    events: list[EventIn] = []
    policy: PolicyIn = PolicyIn()
    mode: Literal["simulate", "emulate", "shadow", "replay"] = "simulate"

    @model_validator(mode="after")
    def _ids_and_limits(self) -> ProblemIn:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task id")
        if sum(len(t.dependencies) for t in self.tasks) > MAX_EDGES:
            raise ValueError("too many edges")
        return self


def to_task(t: TaskIn) -> Task:
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
        w.id: Worker(w.id, Resources(w.resources), tuple(sorted(w.labels.items()))) for w in p.cluster.workers
    }
    return Problem({t.id: to_task(t) for t in p.tasks}, workers, events, p.seed)


def load(raw: bytes, max_bytes: int = MAX_BODY_BYTES) -> ProblemIn:
    """Граница доверия: размер -> JSON -> схема. Глубину вложенности ограничивают сами модели."""
    if len(raw) > max_bytes:
        raise InputTooLarge(f"input is {len(raw)} bytes, limit is {max_bytes}")
    return ProblemIn.model_validate_json(raw)


def dump_schema() -> str:
    return json.dumps(ProblemIn.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    # Пишем байты: CI сравнивает схему через diff.
    sys.stdout.buffer.write((dump_schema() + "\n").encode("utf-8"))
