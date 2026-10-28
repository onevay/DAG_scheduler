"""Оценка по ходу прогона на агентах: работа из прогресса задачи, перебор ожидаемого, резервы по квантилю."""

import json

from test_emulation import Loop, laptop

from expsched.agent.core import Agent
from expsched.agent.runtime import WORKLOAD_IMAGE, FakeRuntime, read_total, write_progress
from expsched.contract.agent import PollReply
from expsched.contract.io_models import ProblemIn
from expsched.domain.models import p80_work
from expsched.emulate.replay import replay
from expsched.estimate.history import pinned

RUN = "0123456789abcdef"


def hidden(truth, **estimate):
    """Задачи laptop без длительности: планировщик знает оценку, настоящую работу знает только нагрузка."""
    raw = laptop(mode="emulate").model_dump(mode="json")
    for t in raw["tasks"]:
        real = truth.get(t["id"], t["duration"])
        t.update(duration=None, estimate=estimate.get(t["id"], t["duration"]))
        t["container"] = {"image": WORKLOAD_IMAGE, "env": {"EXPSCHED_TOTAL": str(real)}}
    return ProblemIn.model_validate(raw)


def records(emulation, kind):
    return [r for r in emulation.result.events if r["type"] == kind]


def test_underestimated_tasks_are_learned_from_progress_and_the_run_finishes(tmp_path):
    problem = hidden({"train_small": 70}, train_small=30)  # думают 30, на деле 70
    emulation = Loop(tmp_path, problem).run()
    assert emulation.status == "done" and emulation.result.result["status"] == "OK"
    learned = [r for r in records(emulation, "ESTIMATE") if r["task"] == "train_small"]
    assert learned, "the scheduler never revised train_small"
    assert (
        learned[0]["work"] > 30 and abs(learned[-1]["work"] - 70) <= 3
    )  # ближе к правде с каждым чекпоинтом
    assert learned[-1]["spread"] < learned[0]["spread"] or learned[0]["spread"] == 0
    done = next(a for a in emulation.result.result["attempts"] if a["task"] == "train_small")
    assert done["end"] - done["start"] >= 70

    # проигрывание с той же работой и теми же уточнениями — тот же журнал
    stored = emulation.result.result
    same = replay(problem, emulation.result.events, pinned(problem, stored["estimates"]))
    assert same["same"] and same["same_journal"]


def test_overdue_attempt_without_progress_waits_for_its_conditional_expectation(tmp_path):
    tasks = [
        t.model_copy(update={"checkpoint_interval": None})
        for t in hidden({"load_data": 25}, load_data=8).tasks
    ]
    emulation = Loop(tmp_path, laptop(tasks=tasks, mode="emulate")).run()
    assert emulation.result.result["status"] == "OK"
    overdue = [r for r in records(emulation, "ESTIMATE") if r["task"] == "load_data"]
    assert overdue and all(b["work"] > a["work"] for a, b in zip(overdue, overdue[1:], strict=False))
    assert overdue[0]["end"] > overdue[0]["t"]  # новое окончание — впереди, а не в прошлом


def test_reservations_wait_for_the_cautious_end_of_an_uncertain_task(tmp_path):
    tasks = [
        {
            "id": "big",
            "kind": "train",
            "priority": 5,
            "resources": {"cpu": 8},
            "estimate": {"median": 20, "spread": 0.5},
        },
        {"id": "next", "kind": "eval", "priority": 1, "duration": 5, "resources": {"cpu": 8}},
    ]
    problem = laptop(mode="emulate")
    problem = problem.model_validate({**problem.model_dump(mode="json"), "tasks": tasks})
    loop = Loop(tmp_path, problem)
    loop.emulation.tick()
    reserve = [r for r in loop.emulation.sim.journal.records if r["type"] == "RESERVE"]
    work = loop.emulation.prepared.estimates["big"].work()
    assert work == 23 and p80_work(work, 0.5) == 31
    assert [(r["task"], r["t_res"]) for r in reserve] == [
        ("next", 31)
    ]  # не 23: так узел не обещан раньше времени


def test_agent_reports_the_whole_work_the_workload_knows(tmp_path):
    write_progress(tmp_path, 10, 40)
    assert json.loads((tmp_path / "checkpoint.json").read_text()) == {"progress": 10, "total": 40}
    assert read_total(tmp_path) == 40
    write_progress(tmp_path, 12)
    assert read_total(tmp_path) is None

    runtime = FakeRuntime()
    agent = Agent("box", runtime, tmp_path / "agent", {"cpu": 8, "ram_mb": 16384}, now=lambda: "T")
    start = {
        "run_id": RUN, "task": "train", "attempt": 1, "kind": "start", "command_id": 1, "worker": "w",
        "limits": {"cpu": 1}, "work": 20, "checkpoint_interval": 10, "time_scale_ms": 100,
        "container": {"image": WORKLOAD_IMAGE, "env": {"EXPSCHED_TOTAL": "45"}},
    }  # fmt: skip
    # образ встроенной нагрузки агент запускает и без --allow-image
    agent.handle(PollReply.model_validate({"commands": [start]}))
    runtime.advance(2500)
    agent.watch()
    events = [(e.type, e.progress, e.total) for e in agent.outbox]
    assert events == [("accepted", None, None), ("started", None, None), ("checkpoint", 20, 45)]
    runtime.advance(1000)  # 35 из 45, хотя планировщик прислал 20: работа — настоящая, а не присланная
    agent.watch()
    assert (agent.outbox[-1].type, agent.outbox[-1].progress) == ("checkpoint", 30)
    runtime.advance(1000)
    agent.watch()
    assert (agent.outbox[-1].type, agent.outbox[-1].progress) == ("exited", 45)
