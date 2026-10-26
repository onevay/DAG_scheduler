"""Контракт выхода: результат прогона (result.json) и записи журнала (events.jsonl).

Модели описывают то, что отдаёт `pipeline.run`, и служат источником JSON Schema для клиентов. Поле, которого
нет в модели, — ошибка: так формат не расползается незаметно.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from expsched.contract.io_models import PolicyName, Strict
from expsched.domain.states import TaskStatus

Time = Annotated[int, Field(ge=0)]
Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Detail = dict[str, Any]

# ---- журнал ----


class Record(Strict):
    seq: Annotated[int, Field(ge=0)]  # номер записи по порядку
    t: Time


class SubmitRecord(Record):
    type: Literal["SUBMIT"]
    task: str
    priority: int
    deadline: int | None


class ReadyRecord(Record):
    type: Literal["READY"]
    task: str


class WaitRecord(Record):
    """Новая причина ожидания задачи; действует до следующей записи WAIT этой задачи или до её старта."""

    type: Literal["WAIT"]
    task: str
    code: Literal[
        "WAIT_DEPS", "WAIT_RELEASE", "WAIT_WORKER_DOWN", "WAIT_RESOURCES", "WAIT_RESERVATION", "WAIT_PRIORITY"
    ]
    detail: Detail


class StartRecord(Record):
    type: Literal["START"]
    task: str
    worker: str
    attempt: Annotated[int, Field(ge=1)]
    resume_from: Time  # с какой позиции продолжается работа
    reason: Detail  # почему политика выбрала задачу; `code` — код причины


class FinishRecord(Record):
    type: Literal["FINISH"]
    task: str
    attempt: Annotated[int, Field(ge=1)]
    worker: str


class WorkerRecord(Record):
    type: Literal["WORKER_DOWN", "WORKER_UP"]
    worker: str


class InterruptedRecord(Record):
    type: Literal["INTERRUPTED"]
    task: str
    attempt: Annotated[int, Field(ge=1)]
    worker: str
    cause: Literal["WORKER_DOWN", "PREEMPT", "FAILED", "OOM"]  # FAILED, OOM — сбой попытки на агенте
    saved: Time  # работа до последнего чекпоинта
    lost: Time
    remaining: Time
    reason: Detail | None = None  # причина вытеснения от политики или подробности сбоя


class BlockedRecord(Record):
    type: Literal["BLOCKED"]
    task: str
    by: str  # задача, которая не будет выполнена


class DiagnosticRecord(Record):
    """Проблема, найденная во время прогона: STUCK, DUPLICATE_TASK_ID или код диагностики входа."""

    type: Literal["DIAGNOSTIC"]
    code: str
    task: str
    level: Literal["error", "warning"] | None = None
    detail: Detail | None = None
    hint: str | None = None


class ReserveRecord(Record):
    type: Literal["RESERVE"]
    task: str
    worker: str
    t_res: Time  # задача стартует на узле не позже этого момента


class ReplanEvent(Strict):
    type: Literal["worker_down", "worker_up", "submit_task"]
    worker: str | None = None
    task: str | None = None


class StartShift(Strict):
    task: str
    old_start: Time
    new_start: Time
    delta: int


class ReplanRecord(Record):
    """Как внешние события момента сдвинули прогнозные старты задач."""

    type: Literal["REPLAN"]
    trigger: Literal["worker_down", "worker_up", "submit_task"]
    events: list[ReplanEvent]
    changes: list[StartShift]
    new_tasks: dict[str, Time]  # задачи, которых не было в прежнем прогнозе: поданные и прерванные
    dropped: list[str]  # задачи, которые больше не стартуют


EventRecord = Annotated[
    SubmitRecord
    | ReadyRecord
    | WaitRecord
    | StartRecord
    | FinishRecord
    | WorkerRecord
    | InterruptedRecord
    | BlockedRecord
    | DiagnosticRecord
    | ReserveRecord
    | ReplanRecord,
    Field(discriminator="type"),
]
EVENT = TypeAdapter(EventRecord)

# ---- результат ----


class AttemptOut(Strict):
    task: str
    attempt: Annotated[int, Field(ge=1)]
    worker: str
    start: Time
    resume_from: Time
    end: Time | None  # None — попытка не закончилась к концу прогона
    outcome: Literal["DONE", "INT", "PRE"] | None  # INT — прервана отказом узла, PRE — вытеснена
    saved: Time


class WaitInterval(Strict):
    code: str
    from_: Time = Field(alias="from")
    to: Time
    detail: Detail


class TaskOut(Strict):
    status: TaskStatus
    start: Time | None  # старт первой попытки
    finish: Time | None
    deadline: Time | None
    tardiness: Time | None
    waits: list[WaitInterval]


class DeadlineMisses(Strict):
    critical: int
    high: int
    normal: int
    low: int


class Score(Strict):
    """Оценка по цели политики: `key` сравнивается поэлементно, меньше — лучше."""

    objective: Literal["lexicographic", "weighted"]
    key: list[int] = Field(min_length=1)


class MetricsOut(Strict):
    makespan: Time
    lower_bound: Time | None
    gap_to_lb: float | None
    deadline_misses: DeadlineMisses
    weighted_tardiness: int
    utilization: dict[str, float | None]  # по ресурсам кластера
    lost_work: dict[str, int]  # по ресурсам кластера, единицы ресурса × время
    mean_wait: float
    score: Score


class ChainLink(Strict):
    """Звено критической цепочки: попытка и то, что сделало её старт возможным."""

    task: str
    attempt: Annotated[int, Field(ge=1)]
    worker: str
    start: Time
    end: Time
    work: Time  # полезная работа: вся попытка или сохранённое до чекпоинта
    lost: Time  # работа прерванной попытки после последнего чекпоинта
    cause: Literal["origin", "dep", "fail", "pre", "res", "rel", "up", "wait"]
    by: str | None = None  # задача, после которой стал возможен старт


class Decomposition(Strict):
    """makespan = dep + res + lost + release + wait."""

    dep: Time
    res: Time
    lost: Time
    release: Time
    wait: Time


IdleCode = Literal["IDLE_DOWN", "IDLE_NO_READY", "IDLE_FRAGMENTATION", "IDLE_RESERVED", "IDLE_POLICY"]


class IdleInterval(Strict):
    """На узле свободны ресурсы `free` с `from` до `to` по причине `code`."""

    worker: str
    from_: Time = Field(alias="from")
    to: Time
    code: IdleCode
    free: Annotated[dict[str, Annotated[int, Field(ge=1)]], Field(min_length=1)]
    detail: Detail  # для IDLE_RESERVED: head, t_res


class IdleOut(Strict):
    by_code: dict[str, dict[IdleCode, int]]  # по ресурсам: единицы ресурса × время простоя по причинам
    intervals: list[IdleInterval]


class ExplainOut(Strict):
    chain: list[ChainLink]  # от начала прогона к последнему окончанию
    decomposition: Decomposition
    idle: IdleOut


class DiagnosticOut(Strict):
    code: Annotated[str, Field(pattern=r"^[EW]\d{3}$")]
    level: Literal["error", "warning"]
    detail: Detail
    hint: str


class ShadowTask(Strict):
    plan_start: Time | None
    plan_finish: Time | None
    fact_start: Time | None
    fact_finish: Time | None
    finish_delta: int | None  # факт минус план
    cause: Literal["manual", "failure", "duration", "upstream", "decision", "same", "waiting"]


class ShadowOut(Strict):
    """План (симуляция того же входа) рядом с фактом прогона на агентах."""

    plan_makespan: Time
    fact_makespan: Time
    causes: dict[str, int]
    tasks: dict[str, ShadowTask]


class ResultOut(Strict):
    schema_version: Literal["1.1"]
    input_hash: Hash  # вход вместе с действующей политикой
    result_hash: Hash | None  # хеш журнала; None, если симуляции не было
    policy: PolicyName
    status: Literal["OK", "PARTIAL", "INFEASIBLE"]
    attempts: list[AttemptOut]
    tasks: dict[str, TaskOut]
    metrics: MetricsOut | None
    explain: ExplainOut | None  # None — симуляции не было
    diagnostics: list[DiagnosticOut]  # ошибки и предупреждения входа
    violations: list[str]  # нарушения, найденные проверкой расписания
    replans: list[ReplanRecord] | None  # None — прогноз не строился (вход больше max_forecast_tasks)
    shadow: ShadowOut | None = None  # только у прогона в режиме shadow


def dump_result_schema() -> str:
    return json.dumps(ResultOut.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)


def dump_event_schema() -> str:
    schema = EVENT.json_schema()
    schema["title"] = "EventRecord"
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
