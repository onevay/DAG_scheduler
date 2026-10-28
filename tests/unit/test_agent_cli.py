"""Эксплуатация агента: настройки из файла и командной строки, повтор связи, остановка, проверка, журнал."""

import json
import logging
import threading
import urllib.error
from pathlib import Path

import pytest

from expsched.agent.__main__ import build, check, serve
from expsched.agent.config import Backoff, JsonFormatter, settings
from expsched.agent.core import Agent
from expsched.agent.runtime import FakeRuntime
from expsched.contract.agent import EventsAck, HelloAck, PollReply

RUN = "0123456789abcdef"
START = {
    "run_id": RUN,
    "task": "train",
    "attempt": 1,
    "kind": "start",
    "command_id": 1,
    "worker": "laptop",
    "limits": {"cpu": 1},
    "work": 30,
    "time_scale_ms": 100,
}


def toml(tmp_path, text):
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_file_overrides_defaults_and_command_line_overrides_the_file(tmp_path):
    path = toml(
        tmp_path,
        '[agent]\nid = "box"\nruntime = "fake"\nstate = "/var/lib/a"\ncpu = 6\nallow_images = ["a/b:1"]\n',
    )
    s = settings(path, {"cpu": 2, "ram_mb": None, "id": None})
    assert (s.id, s.runtime, s.state, s.cpu, s.allow_images) == (
        "box",
        "fake",
        Path("/var/lib/a"),
        2,
        ("a/b:1",),
    )
    assert s.scheduler == "http://127.0.0.1:8000" and s.max_containers == 4 and not s.stop_on_exit


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[agent]\nid = "box"\ncpus = 4\n', "unknown settings cpus"),
        ("[agent]\n", "agent id is required"),
        ('[agent]\nid = "box"\nruntime = "lxc"\n', "unknown runtime"),
    ],
)
def test_wrong_settings_are_errors(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        settings(toml(tmp_path, text), {})


def test_token_comes_from_the_environment_or_a_file(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("from-file\n", encoding="utf-8")
    s = settings(None, {"id": "box", "token_file": str(tmp_path / "token")})
    monkeypatch.delenv("EXPSCHED_AGENT_TOKEN", raising=False)
    assert s.token() == "from-file"
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "from-env")
    assert s.token() == "from-env"


def test_gravesentinel_agent_is_limited_to_its_cpu_pairs(tmp_path):
    s = settings(
        None, {"id": "stand", "runtime": "gravesentinel", "state": str(tmp_path), "cpu": 16, "gpu": 2}
    )
    agent = build(s)
    assert agent.runtime.name == "gravesentinel"
    assert agent.capacity["cpu"] == 4 and "gpu" not in agent.capacity and agent.max_containers == 4


def test_backoff_grows_to_its_limit_with_jitter_and_resets():
    backoff = Backoff(maximum=10, jitter=lambda: 0.5)  # середина разброса: ровно база
    assert [backoff.next() for _ in range(6)] == [1, 2, 4, 8, 10, 10]
    backoff.reset()
    assert backoff.next() == 1
    assert Backoff(jitter=lambda: 0.0).next() == pytest.approx(0.8)
    assert Backoff(jitter=lambda: 1.0).next() == pytest.approx(1.2)


class Scheduler:
    """Сервер-подделка: сначала `down` раз недоступен, потом отвечает; после `polls` опросов агент уходит."""

    def __init__(self, stop, down=0, polls=3, commands=()):
        self.stop, self.down, self.polls, self.commands = stop, down, polls, list(commands)
        self.paths = []

    def call(self, path, body, reply, timeout=60):
        self.paths.append(path)
        if self.down:
            self.down -= 1
            raise urllib.error.URLError("connection refused")
        if path == "hello":
            return HelloAck(agent="box", poll_timeout_s=25, heartbeat_timeout_s=15, next_event_seq=0)
        if path == "poll":
            self.polls -= 1
            if self.polls <= 0:
                self.stop.set()  # как SIGTERM посреди работы
            commands, self.commands = self.commands, []
            return PollReply.model_validate({"commands": commands})
        return EventsAck(next_event_seq=body.events[-1].seq + 1)


class Stop(threading.Event):
    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.is_set()


def agent(tmp_path, runtime):
    return Agent("box", runtime, tmp_path / "agent", {"cpu": 4}, now=lambda: "T")


def test_unreachable_scheduler_is_retried_with_growing_pauses_until_stopped(tmp_path):
    stop = Stop()
    scheduler = Scheduler(stop, down=3)
    serve(agent(tmp_path, FakeRuntime()), scheduler, stop, Backoff(jitter=lambda: 0.5))
    assert stop.waits == [1, 2, 4]
    assert scheduler.paths == ["hello"] * 4 + ["poll"] * 3


def test_stop_leaves_containers_running_unless_asked_to_stop_them(tmp_path):
    for stop_on_exit, left in ((False, ["0123456789abcdef:train:1"]), (True, [])):
        runtime = FakeRuntime()
        stop = Stop()
        scheduler = Scheduler(stop, polls=2, commands=[START])
        a = agent(tmp_path / str(stop_on_exit), runtime)
        serve(a, scheduler, stop, Backoff(), stop_on_exit=stop_on_exit)
        assert runtime.keys() == left
        if stop_on_exit:  # об остановленном сервер узнал перед выходом
            assert scheduler.paths[-1] == "events" and a.outbox == []
            assert a.attempts[f"{RUN}:train:1"].done  # в журнале агента попытка закончена (killed)


def test_check_reports_each_part_and_fails_on_any(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("EXPSCHED_AGENT_TOKEN", "t")
    s = settings(None, {"id": "box", "runtime": "fake", "state": str(tmp_path)})
    a = build(s)
    assert check(s, a, Scheduler(threading.Event()), lambda: "fake runtime") == 0
    ok = capsys.readouterr().out
    assert "ok   runtime: fake runtime" in ok and "ok   scheduler" in ok

    def broken():
        raise RuntimeError("docker daemon is not running")

    assert check(s, a, Scheduler(threading.Event(), down=1), broken) == 1
    failed = capsys.readouterr().out
    assert "FAIL runtime fake: docker daemon is not running" in failed
    assert "FAIL scheduler http://127.0.0.1:8000: connection refused" in failed


def test_json_log_lines():
    record = logging.LogRecord(
        "expsched.agent", logging.WARNING, __file__, 1, "scheduler: %s", ("down",), None
    )
    line = json.loads(JsonFormatter().format(record))
    assert (
        line["level"] == "warning" and line["message"] == "scheduler: down" and line["at"].endswith("+00:00")
    )


def test_check_without_hello_does_not_introduce_the_agent(tmp_path, capsys):
    s = settings(None, {"id": "box", "runtime": "fake", "state": str(tmp_path)})
    scheduler = Scheduler(threading.Event())
    assert check(s, build(s), scheduler, lambda: "fake runtime", hello=False) == 0
    out = capsys.readouterr().out
    assert scheduler.paths == [] and "ok   scheduler" not in out and "FAIL scheduler" not in out
