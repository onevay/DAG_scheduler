"""Тела запросов и ответов REST API. Из них FastAPI строит OpenAPI-схему для клиентов."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from expsched.contract.io_models import EventIn, Id, PolicyIn, PolicyName, ProblemIn, Strict
from expsched.contract.output_models import (
    AttemptOut,
    ChainLink,
    Decomposition,
    DiagnosticOut,
    EventRecord,
    IdleInterval,
    MetricsOut,
    ResultOut,
    ShadowOut,
    WaitInterval,
)
from expsched.contract.whatif import Intervention

ErrorCode = Literal[
    "INPUT_TOO_LARGE",  # тело больше лимита
    "SCHEMA_INVALID",  # вход не соответствует схеме; подробности — в diagnostics
    "INVALID_REQUEST",  # неверные параметры запроса
    "POLICY_NOT_IMPLEMENTED",
    "RUN_TIMEOUT",  # прогон не уложился в отведённое время
    "RUN_QUEUE_FULL",  # прогонов в очереди уже предельное число; повторить позже
    "RUN_FAILED",  # прогон упал в рабочем процессе; подробности только в журнале сервера
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "FORBIDDEN",  # агентский API выключен или токен неверный
    "EVENT_GAP",  # агент пропустил номер события; дослать с указанного
    "RUN_IN_PROGRESS",  # прогон на агентах ещё идёт: результата пока нет
    "INTERNAL",  # ошибка сервера; подробности только в его журнале
]


class RunRequest(Strict):
    problem: ProblemIn
    policy: PolicyIn | None = None  # по умолчанию — политика из входа


class EventRequest(Strict):
    event: EventIn  # отказ или возврат узла, подача задачи


class PoliciesOut(Strict):
    policies: list[PolicyName]  # реализованные
    defaults: PolicyIn


class InputAnalysis(Strict):
    lower_bound: int  # нижняя граница makespan
    critical_path: list[str]  # задачи от начала к концу


class ValidationOut(Strict):
    valid: bool
    diagnostics: list[DiagnosticOut]
    analysis: InputAnalysis | None  # планируемой части входа; None — планировать нечего


class ScenarioInfo(Strict):
    name: str
    tasks: int  # вместе с подаваемыми событиями
    workers: int
    events: int
    policy: PolicyName
    time_unit: str


class ScenariosOut(Strict):
    scenarios: list[ScenarioInfo]


class RunListItem(Strict):
    run_id: str
    name: str | None
    policy: PolicyName
    status: Literal["OK", "PARTIAL", "INFEASIBLE"]
    tasks: int
    makespan: int | None
    created_at: str  # ISO 8601, UTC


class RunsOut(Strict):
    runs: list[RunListItem]  # новые первыми


class RunOut(Strict):
    run_id: str  # по нему читаются вход, результат и журнал прогона
    result: ResultOut


class EventsPage(Strict):
    events: list[EventRecord]
    total: int  # записей в журнале всего
    next_seq: int | None  # с какого номера читать следующую страницу; None — журнал прочитан


class WaitView(WaitInterval):
    task: str


class ExplainView(Strict):
    """Объяснение прогона, суженное фильтрами запроса."""

    chain: list[ChainLink]
    decomposition: Decomposition
    attempts: list[AttemptOut]
    waits: list[WaitView]
    idle: list[IdleInterval]


class WhatIfRequest(Strict):
    problem: ProblemIn
    policy: PolicyIn | None = None  # по умолчанию — политика из входа
    interventions: Annotated[list[Intervention], Field(min_length=1, max_length=16)]


class RunSummary(Strict):
    run_id: str
    status: Literal["OK", "PARTIAL", "INFEASIBLE"]
    metrics: MetricsOut | None


class WhatIfVariant(RunSummary):
    intervention: Intervention
    delta: dict[str, int | float] | None  # вариант минус исходный; None, если один из них не симулировался


class WhatIfOut(Strict):
    baseline: RunSummary
    variants: list[WhatIfVariant]


class ErrorInfo(Strict):
    code: ErrorCode
    message: str


class ErrorOut(Strict):
    error: ErrorInfo
    diagnostics: list[DiagnosticOut] | None = None


# ---- прогон на агентах ----


class LiveStarted(Strict):
    run_id: str
    status: Literal["running", "done", "cancelled"]


class ContainerOut(Strict):
    """Попытка задачи на агенте: где идёт, что с ней и сколько работы сохранено."""

    task: str
    attempt: int
    worker: str
    agent: str
    state: str  # sent, accepted, started или тип последнего события агента
    container_id: str | None
    resume_from: int
    progress: int  # сохранено по последнему чекпоинту, в единицах модели
    detail: str | None


class WorkerLive(Strict):
    agent: str
    up: bool
    dropped: bool  # уронен вручную


class LiveOut(Strict):
    run_id: str
    status: Literal["running", "done", "cancelled"]
    now: int  # часы модели
    time_scale_ms: int
    workers: dict[str, WorkerLive]
    containers: list[ContainerOut]
    shadow: ShadowOut | None = None  # режим shadow: план рядом с фактом на сейчас


class CheckpointAction(Strict):
    kind: Literal["checkpoint"]
    task: Id


class WorkerAction(Strict):
    kind: Literal["worker_down", "worker_up"]
    worker: Id


class CancelAction(Strict):
    kind: Literal["cancel"]


Action = Annotated[CheckpointAction | WorkerAction | CancelAction, Field(discriminator="kind")]
