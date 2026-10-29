"""Среда Docker/Podman и встроенная нагрузка: аргументы CLI, состояния, нагрузка как процесс и в Docker."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from expsched.agent import workload
from expsched.agent.core import Agent
from expsched.agent.docker import DockerRuntime
from expsched.agent.runtime import read_progress
from expsched.contract.agent import PollReply, StartCommand

RUN = "0123456789abcdef"
KEY = f"{RUN}:train:1"
WORKLOAD = Path(workload.__file__)


def command(**fields):
    base = {
        "run_id": RUN, "task": "train", "attempt": 1, "kind": "start", "command_id": 1, "worker": "laptop",
        "limits": {"cpu": 2, "ram_mb": 4096}, "work": 6, "checkpoint_interval": 2, "time_scale_ms": 5,
    }  # fmt: skip
    return StartCommand.model_validate({**base, **fields})


class Cli:
    """Запоминает вызовы CLI и отвечает заготовками по подкоманде."""

    def __init__(self, **replies):
        self.calls = []
        self.replies = replies

    def __call__(self, args):
        self.calls.append(list(args))
        code, out, err = self.replies.get(args[1], (0, "", ""))
        return subprocess.CompletedProcess(args, code, out, err)


def test_start_runs_the_workload_with_limits_labels_and_the_checkpoint_mount(tmp_path):
    cli = Cli(run=(0, "0123456789abcdef0123\n", ""))
    container = DockerRuntime("laptop", "podman", run=cli).start(command(resume_from=2), tmp_path / "ckpt")
    args = cli.calls[0]
    assert container == "0123456789ab" and args[:5] == [
        "podman",
        "run",
        "-d",
        "--name",
        "expsched-0123456789abcdef-train-1",
    ]
    pairs = {(a, b) for a, b in zip(args, args[1:], strict=False)}
    assert {
        ("--cpus", "2"),
        ("--memory", "4096m"),
        ("--memory-swap", "4096m"),
        ("--network", "none"),
    } <= pairs
    assert {
        ("--label", "expsched.agent=laptop"),
        ("--label", f"expsched.attempt={KEY}"),
        ("--cap-drop", "ALL"),
    } <= pairs
    assert ("-v", f"{(tmp_path / 'ckpt').resolve()}:/checkpoint") in pairs
    assert {
        ("-e", "EXPSCHED_RESUME_FROM=2"),
        ("-e", "EXPSCHED_WORK=6"),
        ("-e", "EXPSCHED_TIME_SCALE_MS=5"),
    } <= pairs
    assert args[-1] == "expsched-workload:local" and ("--user" in args) is (sys.platform != "win32")


def test_task_container_brings_its_image_command_and_env(tmp_path):
    cli = Cli(run=(0, "abc\n", ""))
    spec = {"image": "python:3.12-slim", "command": ["python", "train.py"], "env": {"SEED": "1"}}
    DockerRuntime("laptop", run=cli).start(command(container=spec, limits={"gpu": 1}), tmp_path)
    args = cli.calls[0]
    assert args[-3:] == ["python:3.12-slim", "python", "train.py"] and "--network" not in args
    assert args[args.index("--gpus") : args.index("--gpus") + 2] == ["--gpus", "1"] and "SEED=1" in args


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ((0, json.dumps({"Status": "running"}), ""), ("running", None)),
        ((0, json.dumps({"Status": "exited", "ExitCode": 3}), ""), ("exited", 3)),
        ((0, json.dumps({"Status": "exited", "ExitCode": 137, "OOMKilled": True}), ""), ("oom", None)),
        ((1, "", "Error: No such object"), ("missing", None)),
    ],
)
def test_inspect_reads_the_container_state(reply, expected):
    state = DockerRuntime("laptop", run=Cli(inspect=reply)).inspect(KEY)
    assert (state.status, state.exit_code) == expected


def test_keys_stop_and_checkpoint_go_by_labels_and_names():
    cli = Cli(ps=(0, f"{KEY}\n{RUN}:eval:2\n", ""))
    runtime = DockerRuntime("laptop", run=cli)
    assert runtime.keys() == [f"{RUN}:eval:2", KEY]
    runtime.stop(KEY)
    runtime.checkpoint(KEY)
    assert "label=expsched.agent=laptop" in cli.calls[0]
    assert cli.calls[1] == ["docker", "rm", "-f", "expsched-0123456789abcdef-train-1"]
    assert cli.calls[2] == ["docker", "kill", "--signal", "USR1", "expsched-0123456789abcdef-train-1"]


def test_runtime_that_cannot_start_turns_into_a_rejected_attempt(tmp_path):
    runtime = DockerRuntime(
        "laptop", run=Cli(run=(125, "", "Unable to find image 'expsched-workload:local'"))
    )
    agent = Agent("laptop", runtime, tmp_path, {"cpu": 8, "ram_mb": 16384})
    agent.handle(PollReply(commands=[command()]))
    assert [e.type for e in agent.outbox] == ["accepted", "rejected"]
    assert "Unable to find image" in (agent.outbox[-1].detail or "")


# ---- нагрузка как обычный процесс ----


def run_workload(directory, **env):
    values = {"EXPSCHED_CHECKPOINT_DIR": str(directory), "EXPSCHED_TIME_SCALE_MS": "5", **env}
    return subprocess.Popen(
        [sys.executable, str(WORKLOAD)], env={**os.environ, **values}, stdout=subprocess.PIPE
    )


def test_workload_works_checkpoints_and_resumes(tmp_path):
    done = run_workload(tmp_path, EXPSCHED_WORK="7", EXPSCHED_CHECKPOINT_INTERVAL="3")
    assert done.wait(timeout=30) == 0 and read_progress(tmp_path) == 6
    again = run_workload(tmp_path, EXPSCHED_WORK="7", EXPSCHED_RESUME_FROM="0")  # чекпоинт больше: с 6
    assert again.wait(timeout=30) == 0 and again.stdout and b"done: 7 units" in again.stdout.read()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGUSR1 есть только на POSIX")
def test_workload_saves_on_request(tmp_path):
    process = run_workload(tmp_path, EXPSCHED_WORK="1000", EXPSCHED_TIME_SCALE_MS="20")
    time.sleep(0.5)
    process.send_signal(signal.SIGUSR1)
    deadline = time.monotonic() + 10
    while read_progress(tmp_path) is None and time.monotonic() < deadline:
        time.sleep(0.05)
    process.kill()
    assert (read_progress(tmp_path) or 0) > 0


# ---- настоящий Docker: образ нагрузки собран (make workload-image) ----


def docker_ready() -> bool:
    return (
        shutil.which("docker") is not None
        and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    )


def wait_for(runtime, key, timeout=60):
    deadline = time.monotonic() + timeout
    while (state := runtime.inspect(key)).status == "running" and time.monotonic() < deadline:
        time.sleep(0.2)
    return state


@pytest.mark.docker
def test_workload_in_docker_finishes_and_leaves_a_checkpoint(tmp_path):
    if not docker_ready():
        pytest.skip("Docker недоступен")
    runtime = DockerRuntime("pytest")
    runtime.start(command(time_scale_ms=200), tmp_path)
    try:
        state = wait_for(runtime, KEY)
        assert (state.status, state.exit_code) == ("exited", 0) and read_progress(tmp_path) == 4
    finally:
        runtime.stop(KEY)


@pytest.mark.docker
def test_container_over_its_memory_limit_is_oom(tmp_path):
    if not docker_ready():
        pytest.skip("Docker недоступен")
    runtime = DockerRuntime("pytest")
    hungry = {"image": "expsched-workload:local", "env": {"EXPSCHED_USE_RAM_MB": "256"}}
    runtime.start(command(container=hungry, limits={"cpu": 1, "ram_mb": 64}, time_scale_ms=500), tmp_path)
    try:
        assert wait_for(runtime, KEY).status == "oom"
    finally:
        runtime.stop(KEY)
