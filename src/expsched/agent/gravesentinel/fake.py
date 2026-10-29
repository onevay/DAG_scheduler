"""Поддельный гипервизор gravesentinel в памяти: тот же протокол v3, без Podman и CRIU — для тестов и Windows.

Память процесса контейнера — словарь `memory`: чекпоинт копирует его в новое поколение, восстановление
возвращает последнее, холодный старт начинает с пустого, как CRIU с настоящим процессом. Операции
выполняются при `step()` (или сразу, с `auto=True`); `restart()` — перезапуск демона: регистрации забыты,
незапущенные операции — failed, идущие — indeterminate, контейнеры живут дальше, как в Podman.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from expsched.agent.gravesentinel.client import CPU_PAIRS, IDENTIFIER, MAX_MESSAGE, PROTOCOL, SERVICE

SUBMITS = ("SubmitCreateCheckpoint", "SubmitRestoreOrRestart", "SubmitStopContainer")


@dataclass
class Container:
    """Контейнер Podman и хранилище поколений одного логического контейнера."""

    state: str = "absent"  # absent | running | exited
    exit_code: int | None = None
    oom_killed: bool = False
    memory: dict[str, Any] = field(default_factory=dict)
    generations: list[dict[str, Any]] = field(default_factory=list)
    starts: int = 0  # сколько раз запускался: холодно или из поколения


@dataclass
class Op:
    operation_id: str
    request_id: str
    method: str
    container_id: str
    state: str = "queued"
    error_code: str | None = None


class FakeHypervisor:
    def __init__(self, auto: bool = True, requests_per_connection: int = 64) -> None:
        self.auto = auto
        self.requests_per_connection = requests_per_connection
        self.registrations: dict[str, dict[str, Any]] = {}
        self.containers: dict[str, Container] = {}
        self.operations: dict[str, Op] = {}
        self.by_request: dict[str, str] = {}
        self.failures: dict[str, tuple[str, bool]] = {}  # метод → (код ошибки, indeterminate), один раз
        self.busy_unregisters = 0  # столько Unregister подряд откажут: идёт обслуживание поколений
        self.connections = 0
        self.methods: list[str] = []  # журнал вызовов, для проверок

    # ---- управление подделкой ----

    def container(self, container_id: str) -> Container:
        return self.containers.setdefault(container_id, Container())

    def exit(self, container_id: str, code: int = 0, oom: bool = False) -> None:
        c = self.container(container_id)
        c.state, c.exit_code, c.oom_killed = "exited", code, oom

    def vanish(self, container_id: str) -> None:
        c = self.container(container_id)
        c.state, c.exit_code, c.oom_killed = "absent", None, False

    def fail_next(self, method: str, code: str, indeterminate: bool = False) -> None:
        self.failures[method] = (code, indeterminate)

    def step(self) -> None:
        for op in self.operations.values():
            if op.state == "queued":
                self._execute(op)

    def restart(self) -> None:
        self.registrations.clear()
        for op in self.operations.values():
            if op.state == "queued":
                op.state, op.error_code = "failed", "unavailable"
            elif op.state == "running":
                op.state, op.error_code = "indeterminate", "internal"

    def transport(self) -> _Connection:
        self.connections += 1
        return _Connection(self)

    # ---- протокол ----

    def handle(self, packet: bytes, served: int) -> bytes:
        """Ответ на пакет запроса; `served` — сколько запросов уже было на соединении."""
        request = json.loads(packet)
        request_id = request["request_id"]
        assert request["version"] == PROTOCOL and len(packet) <= MAX_MESSAGE
        assert IDENTIFIER.fullmatch(request_id), request_id
        assert served > 0 or request["method"] == "Handshake", "Handshake must come first"
        self.methods.append(request["method"])
        last = served + 1 >= self.requests_per_connection
        try:
            result = self._dispatch(request["method"], request["params"], request_id)
            reply: dict[str, Any] = {"ok": True, "result": result}
        except _Refused as refused:
            reply = {"ok": False, "error_code": refused.code}
        return json.dumps(
            {"version": PROTOCOL, "request_id": request_id, **reply, "close_connection": last}
        ).encode()

    def _dispatch(self, method: str, params: dict[str, Any], request_id: str) -> dict[str, Any]:
        if method == "Handshake":
            return {"protocol_version": PROTOCOL, "service": SERVICE}
        if method == "GetHealth":
            return {"status": "healthy", "queue": "available", "protocol_version": PROTOCOL}
        if method == "Register":
            return self._register(params)
        if method == "GetOperation":
            op = self.operations.get(params["operation_id"])
            if op is None:
                raise _Refused()
            return {"operation_id": op.operation_id, "state": op.state, "error_code": op.error_code}
        container_id = params["container_id"]
        if container_id not in self.registrations:
            raise _Refused()  # как у демона: незарегистрированный контейнер — request_failed
        if method == "Unregister":
            if self.busy_unregisters:
                self.busy_unregisters -= 1
                raise _Refused()
            del self.registrations[container_id]
            return {}
        if method == "GetRuntimeStatus":
            c = self.container(container_id)
            return {"state": c.state, "exit_code": c.exit_code, "oom_killed": c.oom_killed}
        if method in SUBMITS:
            return self._submit(method, container_id, request_id)
        raise _Refused("invalid_request")

    def _register(self, params: dict[str, Any]) -> dict[str, Any]:
        cpu = params["checkpoint"]
        pair = (cpu["container_cpu"], cpu["checkpoint_cpu"])
        if pair not in CPU_PAIRS or not 1 <= cpu["keep_last"] <= 3:
            raise _Refused("invalid_request")
        taken = {
            (r["checkpoint"]["container_cpu"], r["checkpoint"]["checkpoint_cpu"]): k
            for k, r in self.registrations.items()
        }
        if taken.get(pair, params["container_id"]) != params["container_id"]:
            raise _Refused()  # пара CPU занята другим контейнером
        self.registrations[params["container_id"]] = copy.deepcopy(params)
        return {}

    def _submit(self, method: str, container_id: str, request_id: str) -> dict[str, Any]:
        if request_id in self.by_request:
            op = self.operations[self.by_request[request_id]]
            if (op.method, op.container_id) != (method, container_id):
                raise _Refused()  # тот же request_id с другим запросом — конфликт
            return {"operation_id": op.operation_id, "duplicate": True}
        op = Op("op-" + hashlib.sha256(request_id.encode()).hexdigest(), request_id, method, container_id)
        self.operations[op.operation_id] = op
        self.by_request[request_id] = op.operation_id
        if self.auto:
            self._execute(op)
        return {"operation_id": op.operation_id, "duplicate": False}

    def _execute(self, op: Op) -> None:
        if op.method in self.failures:
            code, indeterminate = self.failures.pop(op.method)
            op.state, op.error_code = ("indeterminate" if indeterminate else "failed"), code
            return
        c = self.container(op.container_id)
        registration = self.registrations.get(op.container_id)
        op.state, op.error_code = "succeeded", None
        if op.method == "SubmitCreateCheckpoint":
            if c.state != "running" or registration is None:
                op.state, op.error_code = "failed", "not_found"
                return
            c.generations.append(copy.deepcopy(c.memory))
            del c.generations[: -registration["checkpoint"]["keep_last"]]
        elif op.method == "SubmitStopContainer":
            c.state, c.exit_code, c.oom_killed = "absent", None, False
        elif registration is None:
            op.state, op.error_code = "failed", "not_found"
        elif c.generations:  # прежний экземпляр убран (fence), процесс — из последнего поколения
            c.memory = copy.deepcopy(c.generations[-1])
            c.state, c.exit_code, c.oom_killed, c.starts = "running", None, False, c.starts + 1
        elif registration["cold_start"] is not None:
            c.memory = {}
            c.state, c.exit_code, c.oom_killed, c.starts = "running", None, False, c.starts + 1
        else:
            op.state, op.error_code = "failed", "not_found"


class _Refused(Exception):
    def __init__(self, code: str = "request_failed") -> None:
        self.code = code


class _Connection:
    """Транспорт клиента: пакет туда — пакет обратно."""

    def __init__(self, hypervisor: FakeHypervisor) -> None:
        self.hypervisor = hypervisor
        self.served = 0
        self.reply: bytes | None = None
        self.closed = False

    def send(self, packet: bytes) -> None:
        if self.closed:
            raise ConnectionError("connection is closed")
        self.reply = self.hypervisor.handle(packet, self.served)
        self.served += 1

    def recv(self) -> bytes:
        reply, self.reply = self.reply, None
        if reply is None:
            raise ConnectionError("no reply")
        return reply

    def close(self) -> None:
        self.closed = True
