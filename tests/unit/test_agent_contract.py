"""Протокол агента: команды, события, их номера и описание контейнера во входе."""

import pytest
from pydantic import TypeAdapter, ValidationError

from expsched.contract.agent import (
    AgentEvent,
    Command,
    EventsBatch,
    Hello,
    Poll,
    PollReply,
    StartCommand,
)
from expsched.contract.io_models import ContainerSpec, ProblemIn

RUN = {"run_id": "0123456789abcdef", "task": "train", "attempt": 2}


def event(seq, kind, **fields):
    return {**RUN, "seq": seq, "at": "2026-10-26T12:00:00.000Z", "type": kind, **fields}


def test_hello_from_a_laptop_after_a_restart():
    hello = Hello.model_validate(
        {
            "protocol": 1,
            "agent_version": "0.1.0",
            "runtime": "docker",
            "capacity": {"cpu": 8, "ram_mb": 16384},
            "max_containers": 4,
            "running": [RUN],
        }
    )
    assert hello.running[0].key == "0123456789abcdef:train:2"
    with pytest.raises(ValidationError):
        Hello.model_validate({**hello.model_dump(), "protocol": 2})


def test_commands_are_told_apart_by_kind():
    start = {
        **RUN,
        "kind": "start",
        "command_id": 1,
        "worker": "laptop",
        "limits": {"cpu": 2, "ram_mb": 4096},
        "work": 30,
        "resume_from": 10,
        "checkpoint_interval": 10,
        "time_scale_ms": 500,
    }
    stop = {**RUN, "kind": "stop", "command_id": 2, "reason": "worker_down"}
    reply = PollReply.model_validate(
        {"commands": [start, stop, {**RUN, "kind": "checkpoint", "command_id": 3}]}
    )
    assert [type(c).__name__ for c in reply.commands] == ["StartCommand", "StopCommand", "CheckpointCommand"]
    assert isinstance(reply.commands[0], StartCommand) and reply.commands[0].container is None
    with pytest.raises(ValidationError):
        TypeAdapter(Command).validate_python({**stop, "reason": "bored"})
    with pytest.raises(ValidationError):
        Poll.model_validate({"acked_command_id": 0, "wait_s": 60})


@pytest.mark.parametrize(
    ("kind", "fields", "ok"),
    [
        ("exited", {"exit_code": 0, "progress": 30}, True),
        ("exited", {}, False),
        ("checkpoint", {"progress": 20}, True),
        ("checkpoint", {}, False),
        ("rejected", {"detail": "image is not allowed"}, True),
        ("lost", {}, False),
        ("oom", {}, True),
        ("teleported", {}, False),
    ],
)
def test_event_carries_what_its_type_needs(kind, fields, ok):
    if ok:
        assert AgentEvent.model_validate(event(0, kind, **fields)).type == kind
    else:
        with pytest.raises(ValidationError):
            AgentEvent.model_validate(event(0, kind, **fields))


def test_events_in_a_batch_go_one_after_another():
    batch = [event(5, "accepted"), event(6, "started", container_id="c1"), event(7, "oom")]
    assert [e.seq for e in EventsBatch.model_validate({"events": batch}).events] == [5, 6, 7]
    with pytest.raises(ValidationError, match="consecutive"):
        EventsBatch.model_validate({"events": [event(5, "accepted"), event(7, "oom")]})
    with pytest.raises(ValidationError):
        EventsBatch.model_validate({"events": []})


@pytest.mark.parametrize(
    "spec",
    [
        {"image": "Ubuntu:latest"},  # заглавные в образе Docker не принимает
        {"image": "python:3.12", "env": {"lower": "x"}},
        {"image": "python:3.12", "command": ["x"] * 65},
    ],
)
def test_container_spec_rejects_what_docker_would(spec):
    with pytest.raises(ValidationError):
        ContainerSpec.model_validate(spec)


def test_task_container_and_worker_agent_in_the_input():
    problem = ProblemIn.model_validate(
        {
            "cluster": {"workers": [{"id": "laptop", "resources": {"cpu": 8}, "agent": "my-laptop"}]},
            "tasks": [
                {
                    "id": "train",
                    "duration": 3,
                    "resources": {"cpu": 2},
                    "container": {
                        "image": "python:3.12-slim",
                        "command": ["python", "train.py"],
                        "env": {"SEED": "1"},
                    },
                }
            ],
        }
    )
    assert problem.cluster.workers[0].agent == "my-laptop"
    assert problem.tasks[0].container is not None and problem.tasks[0].container.command == [
        "python",
        "train.py",
    ]
