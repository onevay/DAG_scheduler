"""Протокол агента: сообщения между планировщиком и агентом, который запускает попытки задач в контейнерах.

Связь начинает агент — по HTTP к планировщику, поэтому он работает и на той же машине, и за NAT:
`POST /v1/agents/{id}/hello` (Hello → HelloAck), затем по кругу `POST …/poll` (Poll → PollReply: команды)
и `POST …/events` (EventsBatch → EventsAck). Команды и события пронумерованы, их повтор безопасен: команду
с уже записанным номером агент пропускает, событие с уже принятым номером планировщик пропускает.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic.json_schema import models_json_schema

from expsched.contract.io_models import ContainerSpec, Id, ResMap, Strict

PROTOCOL = 1
RunId = Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
Note = Annotated[str, Field(max_length=1024)]


class AttemptRef(Strict):
    """Попытка задачи в прогоне — то, чем управляет агент; `key` — метка её контейнера."""

    run_id: RunId
    task: Id
    attempt: Annotated[int, Field(ge=1)]

    @property
    def key(self) -> str:
        return f"{self.run_id}:{self.task}:{self.attempt}"


class Hello(Strict):
    """Агент представляется: что умеет и сколько ресурсов отдаёт; после своего перезапуска — что уже идёт."""

    protocol: Literal[1]
    agent_version: Annotated[str, Field(max_length=64)]
    runtime: Literal["docker", "podman", "fake", "gravesentinel"]
    capacity: ResMap
    max_containers: Annotated[int, Field(ge=1, le=64)]
    running: list[AttemptRef] = []
    # Нумерация агента: планировщик, ещё не знающий агента (например, после своего перезапуска), продолжает её
    next_event_seq: Annotated[int, Field(ge=0)] = 0  # первое неподтверждённое событие
    acked_command_id: Annotated[int, Field(ge=0)] = 0  # последняя записанная команда


class HelloAck(Strict):
    agent: Id
    poll_timeout_s: Annotated[int, Field(ge=1)]  # сколько планировщик держит пустой опрос
    heartbeat_timeout_s: Annotated[int, Field(ge=1)]  # без опроса дольше — узлы агента считаются упавшими
    next_event_seq: Annotated[int, Field(ge=0)]  # с этого номера агент досылает события


class StartCommand(AttemptRef):
    """Запустить попытку на узле `worker`. Без `container` — встроенная нагрузка: `work` единиц времени
    модели по `time_scale_ms` миллисекунд, с чекпоинтами каждые `checkpoint_interval` единиц."""

    kind: Literal["start"]
    command_id: Annotated[int, Field(ge=1)]
    worker: Id
    container: ContainerSpec | None = None
    limits: ResMap  # ресурсы задачи: cpu → --cpus, ram_mb → --memory, gpu → --gpus
    work: Annotated[int, Field(ge=1)]  # остаток работы
    resume_from: Annotated[int, Field(ge=0)] = 0  # сколько работы сохранено чекпоинтами прежних попыток
    checkpoint_interval: Annotated[int, Field(ge=1)] | None = None
    time_scale_ms: Annotated[int, Field(ge=1)]
    # Скорость узла из входа: настоящая среда её не меняет (узел быстр сам), fake ведёт по ней работу
    speed: Annotated[float, Field(gt=0)] = 1.0


class StopCommand(AttemptRef):
    kind: Literal["stop"]
    command_id: Annotated[int, Field(ge=1)]
    reason: Literal["preempt", "worker_down", "cancel"]


class CheckpointCommand(AttemptRef):
    """Сохранить чекпоинт сейчас, не дожидаясь интервала."""

    kind: Literal["checkpoint"]
    command_id: Annotated[int, Field(ge=1)]


class KillCommand(AttemptRef):
    """Убить контейнер попытки, как убивает его сбой: docker/podman — SIGKILL (код 137), gravesentinel —
    остановка без сохранения. Попытка кончается событием `exited` или `lost`, а не `killed`: это сбой."""

    kind: Literal["kill"]
    command_id: Annotated[int, Field(ge=1)]


Command = Annotated[StartCommand | StopCommand | CheckpointCommand | KillCommand, Field(discriminator="kind")]


AttemptKey = Annotated[str, Field(pattern=r"^[0-9a-f]{16}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}:[1-9][0-9]{0,5}$")]
MAX_LOG_LINES = 200  # строк одной попытки за опрос
MAX_LOG_LINE = 1024  # символов в строке; длиннее — обрезается агентом


class Usage(Strict):
    """Сколько контейнер потребляет сейчас."""

    cpu_milli: Annotated[int, Field(ge=0)]  # тысячные доли ядра: 1500 — полтора ядра
    ram_mb: Annotated[int, Field(ge=0)]


class Poll(Strict):
    """Опрос команд; заодно — потребление идущих попыток и новые строки их журналов (без гарантии доставки:
    потерянный опрос теряет и их)."""

    acked_command_id: Annotated[int, Field(ge=0)]  # команды до этого номера агент записал в свой журнал
    wait_s: Annotated[int, Field(ge=0, le=30)] = 25
    usage: Annotated[dict[AttemptKey, Usage], Field(max_length=64)] = {}
    logs: Annotated[
        dict[
            AttemptKey,
            Annotated[list[Annotated[str, Field(max_length=MAX_LOG_LINE)]], Field(max_length=MAX_LOG_LINES)],
        ],
        Field(max_length=64),
    ] = {}


class PollReply(Strict):
    commands: list[Command]  # с номерами больше `acked_command_id`, по возрастанию


EventType = Literal["accepted", "rejected", "started", "checkpoint", "exited", "oom", "killed", "lost"]
NEEDS: dict[str, str] = {
    "exited": "exit_code",
    "checkpoint": "progress",
    "rejected": "detail",
    "lost": "detail",
}


class AgentEvent(AttemptRef):
    """Что случилось с попыткой. accepted/rejected — ответ на команду start; started — контейнер запущен;
    checkpoint — сохранено `progress` единиц работы (из `total`, если задача знает всю); exited — контейнер
    завершился с `exit_code`; oom — убит за превышение памяти; killed — остановлен командой stop; lost — агент
    потерял контейнер (`detail`)."""

    seq: Annotated[int, Field(ge=0)]
    at: Annotated[str, Field(max_length=40)]  # время агента, ISO 8601 UTC — только для показа
    type: EventType
    container_id: Annotated[str, Field(max_length=128)] | None = None
    progress: Annotated[int, Field(ge=0)] | None = None
    total: Annotated[int, Field(ge=1)] | None = None  # checkpoint: вся работа задачи, если её знает задача
    exit_code: int | None = None
    detail: Note | None = None

    @model_validator(mode="after")
    def _payload(self) -> AgentEvent:
        need = NEEDS.get(self.type)
        if need and getattr(self, need) is None:
            raise ValueError(f"{self.type} event requires {need!r}")
        return self


class EventsBatch(Strict):
    """События подряд по номерам; повтор уже принятых безопасен, пропуск номера — ошибка."""

    events: Annotated[list[AgentEvent], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _consecutive(self) -> EventsBatch:
        seqs = [e.seq for e in self.events]
        if seqs != list(range(seqs[0], seqs[0] + len(seqs))):
            raise ValueError("event seq must be consecutive")
        return self


class EventsAck(Strict):
    next_event_seq: Annotated[int, Field(ge=0)]


MESSAGES = (Hello, HelloAck, Poll, PollReply, EventsBatch, EventsAck)


def dump_agent_schema() -> str:
    _, schema = models_json_schema([(m, "validation") for m in MESSAGES], title="AgentProtocol")
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True)
