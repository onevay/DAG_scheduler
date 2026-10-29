"""Клиент гипервизора чекпоинтов gravesentinel, протокол v3.

Сообщение — один JSON-пакет `SOCK_SEQPACKET` (до 256 KiB): запрос `{version, method, request_id, params}`,
ответ `{version, request_id, ok, result | error_code, close_connection}`. Первым на соединении идёт
`Handshake`; ответ с `close_connection` — последний на нём, следующий запрос откроет новое. Методы
`Submit*` асинхронны: ответ — id операции, её состояние — `GetOperation`; повтор с тем же `request_id`
отвечает той же операцией.
"""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

PROTOCOL = 3
SERVICE = "gravesentinel_hypervisor"
MAX_MESSAGE = 256 * 1024
SOCKET = "/run/gravesentinel/hypervisor.sock"
CPU_PAIRS = ((0, 1), (2, 3), (4, 5), (6, 7))  # (CPU контейнера, CPU чекпоинта)
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}")

Submit = Literal["SubmitCreateCheckpoint", "SubmitRestoreOrRestart", "SubmitStopContainer"]
OperationState = Literal["queued", "running", "succeeded", "failed", "indeterminate"]


class HypervisorError(Exception):
    """Гипервизор отказал: invalid_request, permission_denied, server_busy, request_failed, internal_error."""

    def __init__(self, method: str, code: str) -> None:
        super().__init__(f"{method}: {code}")
        self.code = code


class ProtocolError(Exception):
    """Ответ не по протоколу v3."""


class Transport(Protocol):
    def send(self, packet: bytes) -> None: ...
    def recv(self) -> bytes: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class ColdStart:
    """Как запустить контейнер с нуля, если восстанавливать не из чего."""

    image: str
    argv: Sequence[str] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    mounts: Sequence[Mount] = ()
    runtime_options: Sequence[str] = ()


@dataclass(frozen=True)
class Registration:
    container_id: str  # логический контейнер: поколения чекпоинтов хранятся по нему
    runtime_container: str  # имя контейнера Podman
    pair: tuple[int, int]  # из CPU_PAIRS
    interval_seconds: int = 365 * 24 * 3600  # периодические чекпоинты гипервизора; максимум — почти никогда
    keep_last: int = 2
    cold_start: ColdStart | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    state: Literal["absent", "running", "exited"]
    exit_code: int | None
    oom_killed: bool


@dataclass(frozen=True)
class Operation:
    operation_id: str
    state: OperationState
    error_code: str | None  # у failed и indeterminate: not_found, restore_failed, …

    @property
    def done(self) -> bool:
        return self.state in ("succeeded", "failed", "indeterminate")


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{value!r} is not a gravesentinel identifier: [A-Za-z0-9_.:-], up to 128")
    return value


def unix_transport(path: str = SOCKET, timeout_s: float = 3.0, server_uid: int | None = 0) -> Transport:
    """Соединение с демоном; `server_uid` — каким пользователем он обязан быть (по SO_PEERCRED)."""
    if sys.platform != "linux":
        raise OSError("gravesentinel speaks SOCK_SEQPACKET over a Unix socket: Linux only")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        sock.settimeout(timeout_s)
        sock.connect(path)
        if server_uid is not None:
            _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != server_uid:
                raise PermissionError(f"{path} is served by uid {uid}, expected {server_uid}")
    except BaseException:
        sock.close()
        raise
    return _SocketTransport(sock)


class _SocketTransport:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def send(self, packet: bytes) -> None:
        self.sock.send(packet)

    def recv(self) -> bytes:
        packet = self.sock.recv(MAX_MESSAGE + 1)
        if not packet:
            raise ConnectionError("gravesentinel closed the connection")
        return packet

    def close(self) -> None:
        self.sock.close()


class Client:
    """Вызовы по одному; соединение открывается при первом и после `close_connection` или сбоя связи."""

    def __init__(self, connect: Callable[[], Transport]) -> None:
        self.connect = connect
        self.transport: Transport | None = None
        self.sequence = 0
        self.prefix = f"expsched-{os.getpid()}-{os.urandom(4).hex()}"

    def request_id(self) -> str:
        self.sequence += 1
        return f"{self.prefix}-{self.sequence}"

    def call(self, method: str, params: Mapping[str, Any], request_id: str | None = None) -> dict[str, Any]:
        if self.transport is None:
            self.transport = self.connect()
            hello = self._exchange("Handshake", {}, self.request_id())
            if hello != {"protocol_version": PROTOCOL, "service": SERVICE}:
                self.close()
                raise ProtocolError(f"unexpected handshake {hello}")
        return self._exchange(method, params, request_id or self.request_id())

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None

    def _exchange(self, method: str, params: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        assert self.transport is not None
        packet = json.dumps(
            {"version": PROTOCOL, "method": method, "request_id": identifier(request_id), "params": params},
            separators=(",", ":"),
        ).encode()
        if len(packet) > MAX_MESSAGE:
            raise ValueError(f"{method} request exceeds {MAX_MESSAGE} bytes")
        try:
            self.transport.send(packet)
            raw = self.transport.recv()
        except OSError:
            self.close()  # ответа не было: следующий вызов откроет новое соединение
            raise
        reply = _envelope(raw, request_id)
        if reply["close_connection"]:
            self.close()
        if not reply["ok"]:
            raise HypervisorError(method, reply["error_code"])
        result: dict[str, Any] = reply["result"]
        return result

    # ---- методы ----

    def health(self) -> dict[str, Any]:
        return self.call("GetHealth", {})

    def register(self, r: Registration) -> None:
        if r.pair not in CPU_PAIRS:
            raise ValueError(f"CPU pair {r.pair} is not one of {CPU_PAIRS}")
        cold = r.cold_start
        self.call(
            "Register",
            {
                "container_id": identifier(r.container_id),
                "runtime_container": identifier(r.runtime_container),
                "checkpoint": {
                    "interval_seconds": r.interval_seconds,
                    "keep_last": r.keep_last,
                    "container_cpu": r.pair[0],
                    "checkpoint_cpu": r.pair[1],
                },
                "cold_start": None
                if cold is None
                else {
                    "image_reference": cold.image,
                    "argv": list(cold.argv),
                    "environment": [{"name": k, "value": v} for k, v in sorted(cold.env.items())],
                    "mounts": [
                        {"source": m.source, "target": m.target, "read_only": m.read_only}
                        for m in cold.mounts
                    ],
                    "runtime_options": list(cold.runtime_options),
                },
                "runtime_fingerprint": None,
            },
        )

    def unregister(self, container_id: str) -> None:
        self.call("Unregister", {"container_id": identifier(container_id)})

    def status(self, container_id: str) -> RuntimeStatus:
        r = self.call("GetRuntimeStatus", {"container_id": identifier(container_id)})
        state, code, oom = r.get("state"), r.get("exit_code"), r.get("oom_killed")
        if (
            set(r) != {"state", "exit_code", "oom_killed"}
            or state not in ("absent", "running", "exited")
            or (state == "exited") != isinstance(code, int)
            or not isinstance(oom, bool)
            or (oom and state != "exited")
        ):
            raise ProtocolError(f"invalid runtime status {r}")
        return RuntimeStatus(state, code, oom)

    def submit(self, method: Submit, container_id: str, request_id: str) -> tuple[str, bool]:
        """Операция над контейнером; `request_id` — ключ повтора: тот же — та же операция."""
        r = self.call(method, {"container_id": identifier(container_id)}, request_id)
        if not isinstance(r.get("operation_id"), str) or not isinstance(r.get("duplicate"), bool):
            raise ProtocolError(f"invalid submission reply {r}")
        return r["operation_id"], r["duplicate"]

    def operation(self, operation_id: str) -> Operation:
        r = self.call("GetOperation", {"operation_id": identifier(operation_id)})
        if r.get("state") not in ("queued", "running", "succeeded", "failed", "indeterminate"):
            raise ProtocolError(f"invalid operation {r}")
        return Operation(r["operation_id"], r["state"], r.get("error_code"))


def _envelope(raw: bytes, request_id: str) -> dict[str, Any]:
    if not raw or len(raw) > MAX_MESSAGE:
        raise ProtocolError("reply size is outside the protocol bounds")
    try:
        reply = json.loads(raw)
    except ValueError as error:
        raise ProtocolError(f"reply is not JSON: {error}") from None
    keys = {"version", "request_id", "ok", "close_connection"}
    if (
        not isinstance(reply, dict)
        or reply.get("version") != PROTOCOL
        or reply.get("request_id") != request_id
        or not isinstance(reply.get("ok"), bool)
        or not isinstance(reply.get("close_connection"), bool)
        or set(reply) != keys | ({"result"} if reply["ok"] else {"error_code"})
        or (reply["ok"] and not isinstance(reply["result"], dict))
        or (not reply["ok"] and not isinstance(reply["error_code"], str))
    ):
        raise ProtocolError(f"invalid reply envelope {reply!r}"[:500])
    return reply
