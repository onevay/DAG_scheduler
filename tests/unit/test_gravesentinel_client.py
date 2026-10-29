"""Клиент протокола gravesentinel v3 на поддельном гипервизоре; на Linux — и через SOCK_SEQPACKET."""

import json
import os
import socket
import sys
import threading

import pytest

from expsched.agent.gravesentinel.client import (
    Client,
    ColdStart,
    HypervisorError,
    Mount,
    ProtocolError,
    Registration,
    RuntimeStatus,
    unix_transport,
)
from expsched.agent.gravesentinel.fake import FakeHypervisor

TRAIN = Registration(
    "run1.train",
    "expsched-run1-train",
    (2, 3),
    keep_last=2,
    cold_start=ColdStart(
        "localhost/expsched-workload:local",
        ["python", "/workload.py"],
        {"EXPSCHED_WORK": "40"},
        [Mount("/var/lib/expsched/run1/train", "/checkpoint")],
    ),
)


def connected(**options):
    hypervisor = FakeHypervisor(**options)
    return hypervisor, Client(hypervisor.transport)


def test_register_start_checkpoint_and_restore_from_the_latest_generation():
    hv, client = connected()
    client.register(TRAIN)
    sent = hv.registrations["run1.train"]
    assert sent["checkpoint"] == {
        "interval_seconds": 31536000,
        "keep_last": 2,
        "container_cpu": 2,
        "checkpoint_cpu": 3,
    }
    assert sent["cold_start"]["environment"] == [{"name": "EXPSCHED_WORK", "value": "40"}]
    assert sent["cold_start"]["mounts"] == [
        {"source": "/var/lib/expsched/run1/train", "target": "/checkpoint", "read_only": False}
    ]
    assert client.status("run1.train") == RuntimeStatus("absent", None, False)

    op, duplicate = client.submit("SubmitRestoreOrRestart", "run1.train", "run1.train.start.1")
    assert not duplicate and client.operation(op).state == "succeeded"
    assert client.status("run1.train").state == "running"
    hv.container("run1.train").memory["progress"] = 10
    op, _ = client.submit("SubmitCreateCheckpoint", "run1.train", "run1.train.ckpt.1")
    assert client.operation(op).done
    hv.container("run1.train").memory["progress"] = 17  # после снимка — потеряется

    hv.exit("run1.train", 137, oom=True)
    assert client.status("run1.train") == RuntimeStatus("exited", 137, True)
    client.submit("SubmitRestoreOrRestart", "run1.train", "run1.train.start.2")
    assert hv.container("run1.train").memory == {"progress": 10}  # из поколения, не с нуля
    assert hv.container("run1.train").starts == 2


def test_repeated_request_id_is_the_same_operation_and_a_different_payload_is_refused():
    hv, client = connected(auto=False)
    client.register(TRAIN)
    first = client.submit("SubmitRestoreOrRestart", "run1.train", "req-1")
    assert client.submit("SubmitRestoreOrRestart", "run1.train", "req-1") == (first[0], True)
    assert client.operation(first[0]).state == "queued"
    hv.step()
    assert client.operation(first[0]).state == "succeeded"
    with pytest.raises(HypervisorError) as refused:
        client.submit("SubmitStopContainer", "run1.train", "req-1")
    assert refused.value.code == "request_failed"


def test_failed_operations_carry_their_code():
    hv, client = connected()
    client.register(Registration("run1.eval", "expsched-run1-eval", (0, 1)))  # без холодного старта
    op, _ = client.submit("SubmitRestoreOrRestart", "run1.eval", "req-eval")
    assert (client.operation(op).state, client.operation(op).error_code) == ("failed", "not_found")
    hv.fail_next("SubmitStopContainer", "internal", indeterminate=True)
    op, _ = client.submit("SubmitStopContainer", "run1.eval", "req-stop")
    assert client.operation(op).state == "indeterminate"


def test_cpu_pairs_are_exclusive_and_checked_before_sending():
    hv, client = connected()
    client.register(TRAIN)
    client.register(TRAIN)  # повтор той же регистрации допустим
    with pytest.raises(HypervisorError):
        client.register(Registration("run1.other", "expsched-run1-other", (2, 3)))
    with pytest.raises(ValueError, match="CPU pair"):
        client.register(Registration("run1.other", "expsched-run1-other", (1, 2)))
    with pytest.raises(ValueError, match="identifier"):
        client.status("run1/train")
    assert hv.methods.count("Register") == 3


def test_handshake_first_and_a_new_connection_after_close_connection():
    hv, client = connected(requests_per_connection=3)
    client.register(TRAIN)  # Handshake, Register
    client.status("run1.train")  # третий запрос — последний на соединении
    client.status("run1.train")
    assert hv.connections == 2
    assert hv.methods == ["Handshake", "Register", "GetRuntimeStatus", "Handshake", "GetRuntimeStatus"]


def test_daemon_restart_forgets_registrations_and_interrupted_operations_are_indeterminate():
    hv, client = connected(auto=False)
    client.register(TRAIN)
    queued, _ = client.submit("SubmitRestoreOrRestart", "run1.train", "req-a")
    hv.operations[queued].state = "running"
    hv.restart()
    assert client.operation(queued).state == "indeterminate"
    with pytest.raises(HypervisorError):
        client.status("run1.train")
    client.register(TRAIN)  # клиент повторяет регистрации
    assert client.status("run1.train").state == "absent"


class Scripted:
    """Транспорт с заранее заданными ответами: на каждый запрос — следующий из списка."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, packet):
        self.sent.append(json.loads(packet))

    def recv(self):
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(self.sent[-1])
        return json.dumps(reply).encode() if isinstance(reply, dict) else reply

    def close(self):
        pass


def ok(result, close=False):
    return lambda r: {
        "version": 3,
        "request_id": r["request_id"],
        "ok": True,
        "result": result,
        "close_connection": close,
    }


HANDSHAKE = ok({"protocol_version": 3, "service": "gravesentinel_hypervisor"})


@pytest.mark.parametrize(
    "reply",
    [
        b"not json",
        lambda r: {
            "version": 2,
            "request_id": r["request_id"],
            "ok": True,
            "result": {},
            "close_connection": False,
        },
        lambda r: {"version": 3, "request_id": "other", "ok": True, "result": {}, "close_connection": False},
        lambda r: {"version": 3, "request_id": r["request_id"], "ok": True, "close_connection": False},
        ok({"state": "running", "exit_code": None, "oom_killed": True}),
        ok({"state": "exited", "exit_code": None, "oom_killed": False}),
        ok({"state": "exited", "exit_code": 1}),
    ],
)
def test_replies_off_the_protocol_are_errors(reply):
    client = Client(lambda: Scripted(HANDSHAKE, reply))
    with pytest.raises(ProtocolError):
        client.status("run1.train")


def test_envelope_error_and_wrong_handshake():
    refused = lambda r: {  # noqa: E731
        "version": 3,
        "request_id": r["request_id"],
        "ok": False,
        "error_code": "server_busy",
        "close_connection": False,
    }
    with pytest.raises(HypervisorError) as error:
        Client(lambda: Scripted(HANDSHAKE, refused)).health()
    assert error.value.code == "server_busy"
    with pytest.raises(ProtocolError, match="handshake"):
        Client(lambda: Scripted(ok({"protocol_version": 2, "service": "x"}))).health()


@pytest.mark.skipif(sys.platform != "linux", reason="SOCK_SEQPACKET over a Unix socket: Linux only")
def test_real_seqpacket_socket_with_peer_credentials(tmp_path):
    hv = FakeHypervisor()
    path = str(tmp_path / "hypervisor.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    server.bind(path)
    server.listen(1)

    def serve():
        peer, _ = server.accept()
        with peer:
            served = 0
            while packet := peer.recv(256 * 1024 + 1):
                peer.send(hv.handle(packet, served))
                served += 1

    threading.Thread(target=serve, daemon=True).start()
    client = Client(lambda: unix_transport(path, server_uid=os.geteuid()))
    client.register(TRAIN)
    assert client.status("run1.train").state == "absent"
    client.close()
    # демон обязан быть заданным пользователем: чужой сокет отвергается до первого запроса
    with pytest.raises(PermissionError):
        unix_transport(path, server_uid=os.geteuid() + 1)
    server.close()
