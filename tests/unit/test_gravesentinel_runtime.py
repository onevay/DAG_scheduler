"""Агент со средой gravesentinel на поддельном гипервизоре: пары CPU, снимки CRIU, отказы, перезапуски."""

import pytest

from expsched.agent.core import Agent
from expsched.agent.gravesentinel.client import Client
from expsched.agent.gravesentinel.fake import FakeHypervisor
from expsched.agent.gravesentinel.runtime import GravesentinelRuntime
from expsched.contract.agent import PollReply

RUN = "0123456789abcdef"


def start(task="train", attempt=1, command_id=1, **fields):
    command = {
        "run_id": RUN,
        "task": task,
        "attempt": attempt,
        "kind": "start",
        "command_id": command_id,
        "worker": "pair",
        "limits": {"cpu": 1, "ram_mb": 512},
        "work": 30,
        "checkpoint_interval": 10,
        "time_scale_ms": 100,
    }
    return {**command, **fields}


def reply(*commands):
    return PollReply.model_validate({"commands": list(commands)})


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Stand:
    """Гипервизор, среда и агент; `restart_agent` — новый агент и среда на прежнем каталоге."""

    def __init__(self, tmp_path, **hypervisor):
        self.tmp_path = tmp_path
        self.hv = FakeHypervisor(**hypervisor)
        self.clock = Clock()
        self.restart_agent()

    def restart_agent(self):
        self.runtime = GravesentinelRuntime(
            Client(self.hv.transport), self.tmp_path, clock=self.clock, sleep=self.clock.sleep
        )
        self.agent = Agent("stand", self.runtime, self.tmp_path, {"cpu": 4, "ram_mb": 16384}, now=lambda: "T")
        self.agent.reconcile()

    def events(self):
        return [(e.task, e.type, e.progress, e.exit_code) for e in self.agent.outbox]

    def run(self, seconds, steps=10):
        for _ in range(steps):
            self.clock.now += seconds / steps
            self.agent.watch()


def test_attempt_starts_on_a_pair_checkpoints_by_interval_and_finishes(tmp_path):
    s = Stand(tmp_path)
    s.agent.handle(reply(start()))
    registration = s.hv.registrations[f"{RUN}.train"]
    assert registration["checkpoint"]["container_cpu"] == 0
    assert registration["cold_start"]["image_reference"] == "localhost/expsched-workload:local"
    assert {"name": "EXPSCHED_CHECKPOINT_DIR", "value": ""} in registration["cold_start"]["environment"]
    assert s.hv.container(f"{RUN}.train").state == "running"
    s.run(2.5)  # 25 единиц по 100 мс: снимки на 10 и 20
    assert len(s.hv.container(f"{RUN}.train").generations) == 2
    s.hv.exit(f"{RUN}.train", 0)
    s.agent.watch()
    assert s.events() == [
        ("train", "accepted", None, None),
        ("train", "started", None, None),
        ("train", "checkpoint", 10, None),
        ("train", "checkpoint", 20, None),
        ("train", "exited", 30, 0),
    ]
    assert s.runtime.keys() == [] and s.hv.registrations == {}  # пара свободна, регистрация снята


def test_retry_restores_the_process_from_the_last_snapshot(tmp_path):
    s = Stand(tmp_path)
    s.agent.handle(reply(start()))
    s.hv.container(f"{RUN}.train").memory["progress"] = 12
    s.run(1.5)  # снимок на 10 единицах (а по памяти — 12)
    s.hv.exit(f"{RUN}.train", 137, oom=True)
    s.agent.watch()
    assert s.events()[-1] == ("train", "oom", 10, None)
    s.agent.handle(reply(start(attempt=2, command_id=2, resume_from=10, work=20)))
    c = s.hv.container(f"{RUN}.train")
    assert c.starts == 2 and c.memory == {"progress": 12}  # прежний процесс убран, новый — из снимка
    s.run(1.5)
    assert s.events()[-1] == ("train", "checkpoint", 20, None)  # отсчёт от сохранённого, 10 + 10


def test_tasks_the_hypervisor_cannot_run_are_rejected(tmp_path):
    s = Stand(tmp_path)
    s.agent.max_containers, s.agent.capacity["cpu"] = 8, 8  # проверка самой среды: пары кончаются
    s.agent.handle(reply(start("gpu", limits={"cpu": 1, "gpu": 1})))
    s.agent.handle(reply(start("wide", command_id=2, limits={"cpu": 2})))
    for i in range(5):
        s.agent.handle(reply(start(f"t{i}", command_id=3 + i)))
    rejected = [(e.task, e.detail) for e in s.agent.outbox if e.type == "rejected"]
    assert [t for t, _ in rejected] == ["gpu", "wide", "t4"]
    # GPU нет в ёмкости агента; больше одного CPU и пятую пару отклоняет среда
    assert "gpu" in rejected[0][1] and "one CPU" in rejected[1][1] and "pairs are busy" in rejected[2][1]
    pairs = {r["checkpoint"]["container_cpu"] for r in s.hv.registrations.values()}
    assert pairs == {0, 2, 4, 6}


def test_requested_checkpoint_and_stop_command(tmp_path):
    s = Stand(tmp_path)
    s.agent.handle(reply(start()))
    s.run(0.4)
    s.agent.handle(
        reply({"run_id": RUN, "task": "train", "attempt": 1, "kind": "checkpoint", "command_id": 2})
    )
    s.run(0.3, steps=3)  # снимок запрошен на 5, удался, агент видит сохранённое
    s.agent.handle(
        reply(
            {
                "run_id": RUN,
                "task": "train",
                "attempt": 1,
                "kind": "stop",
                "command_id": 3,
                "reason": "preempt",
            }
        )
    )
    kinds = [(k, p) for _, k, p, _ in s.events()]
    # снимок — по отсчёту от замеченного запуска (оценка снизу), остановка сохраняет его же
    assert kinds[2:] in ([("checkpoint", 4), ("killed", 4)], [("checkpoint", 5), ("killed", 5)])
    assert s.hv.container(f"{RUN}.train").state == "absent" and s.hv.registrations == {}


def test_stop_waits_out_maintenance_before_freeing_the_pair(tmp_path):
    s = Stand(tmp_path)
    s.agent.handle(reply(start()))
    s.hv.busy_unregisters = 2  # гипервизор дважды отвечает конфликтом
    s.runtime.stop(f"{RUN}:train:1")
    assert s.hv.registrations == {} and s.hv.methods.count("Unregister") == 3
    s.agent.handle(reply(start("next", command_id=2)))
    assert s.hv.registrations[f"{RUN}.next"]["checkpoint"]["container_cpu"] == 0  # пара снова свободна


def test_failed_start_and_vanished_container(tmp_path):
    s = Stand(tmp_path)
    s.hv.fail_next("SubmitRestoreOrRestart", "cold_start_failed")
    s.agent.handle(reply(start("broken")))
    s.agent.handle(reply(start("gone", command_id=2)))
    s.hv.vanish(f"{RUN}.gone")
    s.agent.watch()
    ends = {t: (k, c) for t, k, _, c in s.events() if k in ("exited", "lost")}
    assert ends == {"broken": ("exited", 125), "gone": ("lost", None)}


def test_slow_operations_and_a_daemon_restart(tmp_path):
    s = Stand(tmp_path, auto=False)
    s.agent.handle(reply(start()))
    s.run(1.0)
    assert [k for _, k, _, _ in s.events()] == ["accepted", "started"]  # гипервизор ещё запускает
    s.hv.step()
    s.run(1.2)  # запуск замечен; до первого снимка 10 единиц
    s.hv.step()
    s.agent.watch()
    s.agent.watch()
    assert s.events()[-1][1:3] == ("checkpoint", 10)
    s.hv.restart()  # регистрации забыты, контейнер живёт
    s.agent.watch()
    assert f"{RUN}.train" in s.hv.registrations and s.events()[-1][1] == "checkpoint"


def test_agent_restart_keeps_attempts_pairs_and_pending_operations(tmp_path):
    s = Stand(tmp_path, auto=False)
    s.agent.handle(reply(start()))
    s.agent.handle(reply(start("other", command_id=2)))
    s.restart_agent()
    assert s.runtime.keys() == [f"{RUN}:other:1", f"{RUN}:train:1"]
    s.hv.step()
    s.agent.handle(reply(start("third", command_id=3)))
    assert s.hv.registrations[f"{RUN}.third"]["checkpoint"]["container_cpu"] == 4
    assert [k for t, k, _, _ in s.events() if t == "third"] == ["accepted", "started"]


@pytest.mark.parametrize("dir_value", ["", None])
def test_workload_without_checkpoint_files_keeps_progress_in_memory(tmp_path, monkeypatch, dir_value):
    from expsched.agent import workload

    if dir_value is None:
        monkeypatch.setenv("EXPSCHED_CHECKPOINT_DIR", str(tmp_path / "ckpt"))
    else:
        monkeypatch.setenv("EXPSCHED_CHECKPOINT_DIR", dir_value)
    monkeypatch.setenv("EXPSCHED_WORK", "3")
    monkeypatch.setenv("EXPSCHED_CHECKPOINT_INTERVAL", "1")
    monkeypatch.setenv("EXPSCHED_TIME_SCALE_MS", "1")
    monkeypatch.chdir(tmp_path)
    assert workload.main() == 0
    assert (tmp_path / "ckpt" / "checkpoint.json").exists() == (dir_value is None)
