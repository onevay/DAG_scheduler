"""Агенты глазами планировщика: кто на связи, какие команды им отданы, какие события от них приняты."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter

from expsched.contract.agent import (
    AgentEvent,
    Command,
    EventsAck,
    EventsBatch,
    Hello,
    HelloAck,
    Poll,
    PollReply,
    Usage,
)

COMMAND: TypeAdapter[Command] = TypeAdapter(Command)
Listener = Callable[[AgentEvent], None]
Telemetry = Callable[[str, int, Usage | None, list[str]], None]  # задача, попытка, потребление, строки


class EventGap(ValueError):
    """В пачке пропущен номер события: агент должен дослать с `expected`."""

    def __init__(self, expected: int, got: int) -> None:
        super().__init__(f"expected event {expected}, got {got}")
        self.expected = expected


@dataclass
class _Agent:
    hello: Hello | None = None
    last_seen: float = -math.inf
    next_command: int = 1
    commands: list[Command] = field(default_factory=list)  # отданные, но не подтверждённые
    next_event: int = 0


class AgentRegistry:
    """Состояние агентов в памяти сервера. Команды ждут, пока агент их подтвердит; события доставляются
    подписчику по `run_id` строго по номерам, повтор уже принятых пропускается."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, heartbeat_s: int = 15, poll_s: int = 25
    ) -> None:
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self.poll_s = poll_s
        self.agents: dict[str, _Agent] = {}
        self.listeners: dict[str, Listener] = {}
        self.telemetry: dict[str, Telemetry] = {}  # по run_id: потребление и строки журналов попыток

    def hello(self, agent_id: str, hello: Hello) -> HelloAck:
        """Первый hello агента с запуска сервера: нумерация событий и команд — агента (сервер мог
        перезапуститься, а агент — нет); команды, отданные до него, — после последней записанной агентом."""
        agent = self.agents.setdefault(agent_id, _Agent())
        if agent.hello is None:
            agent.next_event = hello.next_event_seq
            first = hello.acked_command_id + 1
            agent.commands = [
                c.model_copy(update={"command_id": first + i}) for i, c in enumerate(agent.commands)
            ]
            agent.next_command = first + len(agent.commands)
        agent.hello, agent.last_seen = hello, self.clock()
        return HelloAck(
            agent=agent_id,
            poll_timeout_s=self.poll_s,
            heartbeat_timeout_s=self.heartbeat_s,
            next_event_seq=agent.next_event,
        )

    def poll(self, agent_id: str, poll: Poll) -> PollReply:
        agent = self.agents.setdefault(agent_id, _Agent())
        agent.last_seen = self.clock()
        agent.commands = [c for c in agent.commands if c.command_id > poll.acked_command_id]
        return PollReply(commands=list(agent.commands))

    def report(self, poll: Poll) -> None:
        """Потребление и строки журналов из опроса — прогонам, которым они принадлежат. Один раз на опрос:
        долгий опрос повторяет `poll`, пока ждёт команд, а строки повторяться не должны."""
        for key in sorted(set(poll.usage) | set(poll.logs)):
            run_id, task, attempt = key.split(":")
            if sink := self.telemetry.get(run_id):
                sink(task, int(attempt), poll.usage.get(key), poll.logs.get(key, []))

    def events(self, agent_id: str, batch: EventsBatch) -> EventsAck:
        agent = self.agents.setdefault(agent_id, _Agent())
        agent.last_seen = self.clock()
        for event in batch.events:
            if event.seq < agent.next_event:
                continue  # уже принято: ответ на прошлую пачку потерялся
            if event.seq > agent.next_event:
                raise EventGap(agent.next_event, event.seq)
            agent.next_event += 1
            if listener := self.listeners.get(event.run_id):
                listener(event)
        return EventsAck(next_event_seq=agent.next_event)

    def send(self, agent_id: str, command: dict[str, Any]) -> None:
        """Команда агенту; номер ей даёт реестр. Агент заберёт её при следующем опросе."""
        agent = self.agents.setdefault(agent_id, _Agent())
        agent.commands.append(COMMAND.validate_python({**command, "command_id": agent.next_command}))
        agent.next_command += 1

    def alive(self, agent_id: str, since: float) -> bool:
        """На связи: опрашивал не дольше `heartbeat_s` назад. Не бывший на связи с `since` — ещё в запасе."""
        last = self.agents[agent_id].last_seen if agent_id in self.agents else -math.inf
        return self.clock() - max(last, since) <= self.heartbeat_s

    def pending(self, agent_id: str) -> bool:
        return bool(agent_id in self.agents and self.agents[agent_id].commands)
