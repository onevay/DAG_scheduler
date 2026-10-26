"""Агент на подделке среды: команды, события, отказы, журнал и перезапуск."""

import pytest

from expsched.agent.__main__ import machine_capacity
from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.contract.agent import EventsAck, PollReply

RUN = "0123456789abcdef"


def start(task="train", attempt=1, command_id=1, **fields):
    command = {
        "run_id": RUN,
        "task": task,
        "attempt": attempt,
        "kind": "start",
        "command_id": command_id,
        "worker": "laptop",
        "limits": {"cpu": 2, "ram_mb": 4096},
        "work": 30,
        "checkpoint_interval": 10,
        "time_scale_ms": 100,
    }
    return {**command, **fields}


def reply(*commands):
    return PollReply.model_validate({"commands": list(commands)})


def kinds(agent):
    return [(e.task, e.type) for e in agent.outbox]


@pytest.fixture
def runtime():
    return FakeRuntime()


@pytest.fixture
def make(tmp_path, runtime):
    def make(**settings):
        return Agent(
            "laptop", runtime, tmp_path / "agent", {"cpu": 8, "ram_mb": 16384}, now=lambda: "T", **settings
        )

    return make


def test_attempt_runs_checkpoints_and_finishes(make, runtime):
    agent = make()
    agent.handle(reply(start()))
    runtime.advance(1500)  # 15 единиц из 30: чекпоинт на 10
    agent.watch()
    runtime.advance(1500)
    agent.watch()
    events = [(e.type, e.progress, e.exit_code) for e in agent.outbox]
    assert events == [
        ("accepted", None, None),
        ("started", None, None),
        ("checkpoint", 10, None),
        ("checkpoint", 20, None),
        ("exited", 30, 0),
    ]
    assert [e.seq for e in agent.outbox] == [0, 1, 2, 3, 4]
    agent.acked(EventsAck(next_event_seq=3))
    assert [e.seq for e in agent.outbox] == [3, 4] and agent.batch() is not None


def test_repeated_commands_are_applied_once(make, runtime):
    agent = make()
    agent.handle(reply(start()))
    agent.handle(reply(start()))  # ответ на опрос потерялся, планировщик повторил
    assert runtime.started == 1 and agent.poll(1).acked_command_id == 1


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (start(container={"image": "evil/miner:latest"}), "is not allowed"),
        (start(limits={"cpu": 2, "ram_mb": 20000}), "not enough ram_mb"),
    ],
)
def test_attempt_is_rejected_when_the_agent_cannot_run_it(make, runtime, command, reason):
    agent = make()
    agent.handle(reply(command))
    (event,) = agent.outbox
    assert event.type == "rejected" and reason in (event.detail or "") and runtime.started == 0


def test_allowed_image_and_container_limit(make, runtime):
    agent = make(allowed_images=["python:3.12-slim"], max_containers=1)
    agent.handle(reply(start(container={"image": "python:3.12-slim"}), start(task="eval", command_id=2)))
    assert kinds(agent)[-1] == ("eval", "rejected") and runtime.started == 1


def test_stop_checkpoint_oom_and_lost(make, runtime):
    agent = make()
    agent.handle(reply(start(task="a"), start(task="b", command_id=2), start(task="c", command_id=3)))
    runtime.advance(500)
    stop = {"run_id": RUN, "task": "a", "attempt": 1, "kind": "stop", "command_id": 4, "reason": "preempt"}
    save = {"run_id": RUN, "task": "b", "attempt": 1, "kind": "checkpoint", "command_id": 5}
    agent.handle(reply(stop, save))
    runtime.oom(f"{RUN}:b:1")
    runtime.vanish(f"{RUN}:c:1")
    agent.watch()
    terminal = [(e.task, e.type, e.progress) for e in agent.outbox if e.type not in ("accepted", "started")]
    assert terminal == [("a", "killed", 0), ("b", "checkpoint", 5), ("b", "oom", 5), ("c", "lost", 0)]


def test_next_attempt_resumes_from_the_shared_checkpoint(make, runtime):
    agent = make()
    agent.handle(reply(start()))
    runtime.advance(1200)
    runtime.fail(f"{RUN}:train:1")
    agent.watch()
    agent.handle(reply(start(attempt=2, command_id=2, work=20, resume_from=10)))
    for _ in range(2):
        runtime.advance(1000)
        agent.watch()
    finished = [(e.attempt, e.type, e.progress) for e in agent.outbox if e.type in ("checkpoint", "exited")]
    assert finished == [(1, "checkpoint", 10), (1, "exited", 10), (2, "checkpoint", 20), (2, "exited", 30)]


def test_restart_replays_the_journal_and_reconciles(make, runtime, tmp_path):
    agent = make()
    agent.handle(reply(start(task="kept"), start(task="gone", command_id=2)))
    agent.acked(EventsAck(next_event_seq=1))
    runtime.vanish(f"{RUN}:gone:1")  # пока агента не было, контейнер удалили
    runtime.start(agent.attempts[f"{RUN}:kept:1"].command.model_copy(update={"task": "stray"}), tmp_path)
    # агент упал, записав команду, но не успев запустить контейнер
    agent._write({"kind": "command", "command": start(task="late", command_id=3)})

    again = make()
    assert again.last_command == 3 and again.next_seq == 4 and [e.seq for e in again.outbox] == [1, 2, 3]
    assert sorted(r.task for r in again.hello().running) == ["gone", "kept", "late"]
    again.reconcile()
    again.watch()
    assert f"{RUN}:stray:1" not in runtime.containers  # чужой контейнер остановлен
    assert kinds(again)[-3:] == [("late", "accepted"), ("late", "started"), ("gone", "lost")]


def test_torn_last_line_of_the_journal_is_ignored(make, tmp_path):
    agent = make()
    agent.handle(reply(start()))
    with agent.journal.open("a", encoding="utf-8") as f:
        f.write('{"kind": "event", "ev')
    assert make().next_seq == 2


def test_machine_capacity_leaves_room_for_the_machine():
    capacity = machine_capacity()
    assert capacity["cpu"] >= 1 and capacity.get("ram_mb", 512) >= 512
