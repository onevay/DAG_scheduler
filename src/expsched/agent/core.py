"""Ядро агента: команды планировщика → контейнеры, состояние контейнеров → события, всё — через журнал.

Журнал `state.jsonl` — единственное состояние агента: записанная команда, id контейнера, событие,
подтверждение событий. После перезапуска агент проигрывает журнал и сверяется со средой: свою попытку без
контейнера запускает заново (команда уже подтверждена и повторно не придёт), чужие или законченные контейнеры
останавливает, пропавшие замечает при слежении как `lost`.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from expsched.agent.runtime import Runtime, read_progress
from expsched.contract.agent import (
    MAX_LOG_LINE,
    MAX_LOG_LINES,
    PROTOCOL,
    AgentEvent,
    AttemptRef,
    Command,
    EventsAck,
    EventsBatch,
    Hello,
    HelloAck,
    Poll,
    PollReply,
    StartCommand,
    StopCommand,
    Usage,
)

VERSION = "0.1.0"
TERMINAL = frozenset({"rejected", "exited", "oom", "killed", "lost"})
USAGE_EVERY_S = 5  # замер потребления: `docker stats` ждёт около секунды
LOGS_EVERY_S = 2
LOG_BYTES = 64 * 1024  # строк журналов на опрос, всех попыток вместе
COMMAND: TypeAdapter[Command] = TypeAdapter(Command)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class Attempt:
    command: StartCommand
    container_id: str | None = None
    progress: int = 0  # последний сообщённый чекпоинт
    events: int = 0  # сколько событий о попытке уже было
    done: bool = False


class Agent:
    def __init__(
        self,
        agent_id: str,
        runtime: Runtime,
        state_dir: Path,
        capacity: Mapping[str, int],
        max_containers: int = 4,
        allowed_images: Collection[str] = (),
        now: Callable[[], str] = utc_now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.agent_id = agent_id
        self.runtime = runtime
        self.capacity = dict(capacity)
        self.max_containers = max_containers
        self.allowed_images = frozenset(allowed_images)
        self.now = now
        self.checkpoints = state_dir / "checkpoints"
        self.journal = state_dir / "state.jsonl"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.last_command = 0
        self.next_seq = 0
        self.outbox: list[AgentEvent] = []
        self.attempts: dict[str, Attempt] = {}
        # Потребление и строки журналов — для показа, не для решений: не в журнале, теряются вместе с опросом
        self.clock = clock
        self.usage: dict[str, Usage] = {}
        self.logs: dict[str, list[str]] = {}
        self.measured = self.read_logs = -float("inf")
        self._replay()

    # ---- обмен с планировщиком ----

    def hello(self) -> Hello:
        running = [
            AttemptRef.model_validate(a.command.model_dump(include={"run_id", "task", "attempt"}))
            for a in self.attempts.values()
            if not a.done
        ]
        return Hello(
            protocol=PROTOCOL,
            agent_version=VERSION,
            runtime=self.runtime.name,
            capacity=self.capacity,
            max_containers=self.max_containers,
            running=running,
            next_event_seq=self.outbox[0].seq if self.outbox else self.next_seq,
            acked_command_id=self.last_command,
        )

    def welcome(self, ack: HelloAck) -> None:
        """Ответ на hello. Сервер помнит этот id по прежнему агенту дальше нашего журнала (журнал новый или
        потерян) — нумерация событий продолжается с его номера, иначе он примет их за повторы."""
        if ack.next_event_seq > self.next_seq:
            self._write({"kind": "renumber", "next_event_seq": ack.next_event_seq})
            self.next_seq = ack.next_event_seq
        self.acked(EventsAck(next_event_seq=ack.next_event_seq))

    def poll(self, wait_s: int) -> Poll:
        """Опрос несёт накопленные строки журналов (они отдаются один раз) и последний замер потребления."""
        logs, self.logs = self.logs, {}
        return Poll(acked_command_id=self.last_command, wait_s=wait_s, usage=self.usage, logs=logs)

    def handle(self, reply: PollReply) -> None:
        for command in sorted(reply.commands, key=lambda c: c.command_id):
            if command.command_id <= self.last_command:
                continue  # уже записана: ответ на прошлый опрос мог потеряться
            self._write({"kind": "command", "command": command.model_dump(mode="json")})
            self.last_command = command.command_id
            if isinstance(command, StartCommand):
                self.attempts[command.key] = Attempt(command)
                self._start(self.attempts[command.key])
            elif isinstance(command, StopCommand):
                self._stop(command.key)
            elif command.key in self.attempts:
                self.runtime.checkpoint(command.key)

    def batch(self) -> EventsBatch | None:
        return EventsBatch(events=self.outbox[:1000]) if self.outbox else None

    def acked(self, ack: EventsAck) -> None:
        self._write({"kind": "ack", "next_event_seq": ack.next_event_seq})
        self.outbox = [e for e in self.outbox if e.seq >= ack.next_event_seq]

    def stop_all(self) -> None:
        """Остановить все свои контейнеры: агент уходит и не оставляет работу без присмотра."""
        for key, attempt in sorted(self.attempts.items()):
            if not attempt.done:
                self._stop(key)

    # ---- контейнеры ----

    def reconcile(self) -> None:
        """свою незапущенную попытку — запустить или подхватить, лишние контейнеры — убрать."""
        present = set(self.runtime.keys())
        for key in sorted(present):
            if key not in self.attempts or self.attempts[key].done:
                self.runtime.stop(key)
        for key, attempt in sorted(self.attempts.items()):
            if attempt.done or attempt.container_id is not None:
                continue
            if key in present:  # контейнер запущен, но агент упал, не успев его записать
                self._write({"kind": "container", "key": key, "container_id": "adopted"})
                attempt.container_id = "adopted"
                self._emit(attempt, "started", container_id="adopted")
            else:
                self._start(attempt)

    def watch(self) -> None:
        """Чекпоинты и окончания контейнеров — в события; законченный контейнер убирается из среды."""
        for key, attempt in sorted(self.attempts.items()):
            if attempt.done or attempt.container_id is None:
                continue
            progress = read_progress(self._dir(attempt.command))
            if progress is not None and progress > attempt.progress:
                self._emit(attempt, "checkpoint", progress=progress)
            state = self.runtime.inspect(key)
            if state.status == "exited":
                work = (
                    attempt.command.resume_from + attempt.command.work
                    if state.exit_code == 0
                    else attempt.progress
                )
                tail = self._tail(key) if state.exit_code != 0 else None
                self._emit(attempt, "exited", exit_code=state.exit_code, progress=work, detail=tail)
                self.runtime.stop(key)
            elif state.status == "oom":
                self._emit(attempt, "oom", progress=attempt.progress, detail=self._tail(key))
                self.runtime.stop(key)
            elif state.status == "missing":
                self._emit(attempt, "lost", progress=attempt.progress, detail="container disappeared")
        self._observe()

    def _tail(self, key: str) -> str | None:
        """Последние строки журнала упавшего контейнера — причина сбоя в его событии (и в журнале прогона)."""
        lines = [line[:MAX_LOG_LINE] for line in self.runtime.logs(key)]
        if lines:
            self.logs[key] = (self.logs.get(key, []) + lines)[-MAX_LOG_LINES:]
        tail = " | ".join(line.strip() for line in self.logs.get(key, [])[-3:] if line.strip())
        return f"log: {tail}"[:500] if tail else None

    def _observe(self) -> None:
        """Замер потребления и новые строки журналов идущих попыток — не чаще своих интервалов."""
        live = sorted(k for k, a in self.attempts.items() if not a.done and a.container_id is not None)
        now = self.clock()
        if now - self.measured >= USAGE_EVERY_S:
            self.measured = now
            self.usage = self.runtime.usage(live) if live else {}
        if now - self.read_logs >= LOGS_EVERY_S:
            self.read_logs = now
            for key in live:
                lines = [line[:MAX_LOG_LINE] for line in self.runtime.logs(key)]
                if lines:
                    self.logs[key] = (self.logs.get(key, []) + lines)[-MAX_LOG_LINES:]
            while sum(len(line) for lines in self.logs.values() for line in lines) > LOG_BYTES:
                longest = max(self.logs, key=lambda k: len(self.logs[k]))
                self.logs[longest] = self.logs[longest][len(self.logs[longest]) // 2 + 1 :]  # старые — прочь

    def _start(self, attempt: Attempt) -> None:
        command = attempt.command
        refusal = self._refusal(command)
        if refusal:
            self._emit(attempt, "rejected", detail=refusal)
            return
        self._emit(attempt, "accepted")
        try:
            container_id = self.runtime.start(command, self._dir(command))
        except Exception as error:  # среда не смогла: попытка отклонена, агент продолжает работу
            self._emit(attempt, "rejected", detail=f"runtime failed to start the container: {error}"[:1024])
            return
        self._write({"kind": "container", "key": command.key, "container_id": container_id})
        attempt.container_id = container_id
        here = read_progress(self._dir(command)) or 0
        elsewhere = (  # прежнюю попытку вёл другой агент: её чекпоинт остался на той машине
            f"checkpoint at {command.resume_from} is not on this machine (here: {here}); "
            "the built-in workload resumes from the scheduler's position, a task's own state starts over"
            if here < command.resume_from
            else None
        )
        self._emit(attempt, "started", container_id=container_id, detail=elsewhere)

    def _refusal(self, command: StartCommand) -> str | None:
        if command.container and command.container.image not in self.allowed_images:
            return f"image {command.container.image!r} is not allowed on this agent"
        busy = [a.command for a in self.attempts.values() if not a.done and a.command.key != command.key]
        if len(busy) >= self.max_containers:
            return f"agent runs {len(busy)} containers, the limit is {self.max_containers}"
        for resource, need in command.limits.items():
            used = sum(c.limits.get(resource, 0) for c in busy)
            if used + need > self.capacity.get(resource, 0):
                return (
                    f"not enough {resource}: {used} of {self.capacity.get(resource, 0)} in use, {need} needed"
                )
        return None

    def _stop(self, key: str) -> None:
        attempt = self.attempts.get(key)
        if attempt is None or attempt.done:
            return
        self.runtime.stop(key)
        self._emit(attempt, "killed", progress=attempt.progress)

    def _dir(self, command: StartCommand) -> Path:
        """Каталог чекпоинтов задачи — общий у всех её попыток в прогоне."""
        return self.checkpoints / command.run_id / command.task

    # ---- события и журнал ----

    def _emit(self, attempt: Attempt, kind: str, **fields: Any) -> None:
        ref = attempt.command.model_dump(include={"run_id", "task", "attempt"})
        event = AgentEvent.model_validate(
            {**ref, "seq": self.next_seq, "at": self.now(), "type": kind, **fields}
        )
        self._write({"kind": "event", "event": event.model_dump(mode="json")})
        self._account(event)

    def _account(self, event: AgentEvent) -> None:
        self.next_seq = event.seq + 1
        self.outbox.append(event)
        attempt = self.attempts.get(event.key)
        if attempt is None:
            return
        attempt.events += 1
        if event.type == "checkpoint" and event.progress is not None:
            attempt.progress = event.progress
        attempt.done = attempt.done or event.type in TERMINAL

    def _write(self, record: dict[str, Any]) -> None:
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())  # запись переживает падение агента

    def _replay(self) -> None:
        if not self.journal.exists():
            return
        lines = self.journal.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    break  # недописанная последняя строка: агент упал посреди записи
                raise
            if record["kind"] == "command":
                command = COMMAND.validate_python(record["command"])
                self.last_command = max(self.last_command, command.command_id)
                if isinstance(command, StartCommand):
                    self.attempts[command.key] = Attempt(command)
            elif record["kind"] == "container":
                self.attempts[record["key"]].container_id = record["container_id"]
            elif record["kind"] == "event":
                self._account(AgentEvent.model_validate(record["event"]))
            elif record["kind"] == "renumber":
                self.next_seq = max(self.next_seq, record["next_event_seq"])
            elif record["kind"] == "ack":
                self.outbox = [e for e in self.outbox if e.seq >= record["next_event_seq"]]
