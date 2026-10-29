"""«Убить контейнер» и хаос: команда агента kill во всех средах, сбои по seed, причины в журнале, replay."""

from test_docker_runtime import Cli
from test_emulation import Loop, laptop
from test_gravesentinel_runtime import Stand, reply, start

from expsched.agent.core import Agent
from expsched.agent.docker import DockerRuntime
from expsched.agent.runtime import FakeRuntime
from expsched.contract.agent import PollReply
from expsched.contract.io_models import ChaosIn, ProblemIn
from expsched.emulate.chaos import Chaos
from expsched.emulate.replay import replay
from expsched.validation import validate
from expsched.validation.chaos import retries_needed

RUN = "0123456789abcdef"
KILL = {"run_id": RUN, "task": "train", "attempt": 1, "kind": "kill", "command_id": 2}


def records(emulation, kind):
    return [r for r in emulation.result.events if r["type"] == kind]


def test_kill_is_a_crash_in_every_runtime(tmp_path):
    runtime = FakeRuntime()
    agent = Agent("box", runtime, tmp_path, {"cpu": 4, "ram_mb": 16384}, now=lambda: "T")
    agent.handle(PollReply.model_validate({"commands": [start()]}))
    agent.watch()
    agent.handle(reply(KILL))
    agent.watch()
    assert [(e.type, e.exit_code) for e in agent.outbox][-1] == ("exited", 137)

    cli = Cli()
    DockerRuntime("box", run=cli).kill(f"{RUN}:train:1")
    assert cli.calls == [["docker", "kill", f"expsched-{RUN}-train-1"]]

    s = Stand(tmp_path / "gs")  # у гипервизора сигнала нет: контейнер снимается, попытка потеряна
    s.agent.handle(reply(start()))
    s.run(0.4)
    s.agent.handle(reply(KILL))
    s.run(0.2, steps=2)
    assert s.events()[-1][1] == "lost" and s.hv.registrations == {}


def test_manual_kill_crashes_the_attempt_and_the_task_is_retried(tmp_path):
    loop = Loop(tmp_path, laptop(mode="emulate"))
    while not any(c.state == "started" for c in loop.emulation.containers.values()):
        loop.step()
    task = sorted(loop.emulation.sim.state.run)[0]
    assert loop.emulation.kill(task) and not loop.emulation.kill("no_such_task")
    emulation = loop.run()
    assert emulation.result.result["status"] == "OK"
    crash = next(r for r in records(emulation, "INTERRUPTED") if r["task"] == task)
    assert crash["cause"] == "FAILED" and crash["reason"]["detail"].startswith("kill: вручную (exit code 137")
    assert sum(a["task"] == task for a in emulation.result.result["attempts"]) == 2


def test_schedule_depends_only_on_the_seed():
    def strikes(seed):
        chaos = Chaos(ChaosIn(crash_every=10, node_down_every=30, seed=seed))
        return chaos.due(100) + chaos.due(200)

    first = strikes(7)
    assert first == strikes(7) and first != strikes(8)
    assert {s.kind for s in first} == {"crash", "down"}
    assert all(1 <= s.t <= 200 for s in first) and first[:3] == sorted(first[:3], key=lambda s: s.t)
    chaos = Chaos(ChaosIn(crash_every=10, seed=7))
    assert chaos.due(100) and chaos.due(100) == []  # каждый сбой отдаётся один раз
    assert 5 <= len(strikes(7)) // 2 <= 25  # в среднем раз в 10 на 200 единиц — около двадцати


def chaotic(tmp_path, seed=1):
    tasks = [t.model_copy(update={"max_retries": 20}) for t in laptop().tasks]
    chaos = ChaosIn(crash_every=15, node_down_every=40, node_down_for=5, seed=seed)
    problem = laptop(mode="emulate", tasks=tasks, chaos=chaos)
    return problem, Loop(tmp_path, problem).run()


def test_chaos_run_recovers_and_names_its_failures(tmp_path):
    problem, emulation = chaotic(tmp_path / "a")
    result = emulation.result.result
    assert result["status"] == "OK" and result["violations"] == []
    killed = [r for r in records(emulation, "INTERRUPTED") if r["cause"] == "FAILED"]
    assert killed and all(r["reason"]["detail"].startswith("chaos: контейнер убит") for r in killed)
    down = records(emulation, "WORKER_DOWN")
    assert down and all(r["reason"].startswith("chaos: узел упал до") for r in down)
    assert all(r.get("reason") == "chaos: узел вернулся" for r in records(emulation, "WORKER_UP"))
    assert result["metrics"]["makespan"] > 90  # без сбоев — 90

    # сбои — факты журнала: проигрывание без хаоса даёт тот же журнал; тот же seed — те же сбои
    same = replay(problem, emulation.result.events)
    assert same["same"] and same["same_journal"]
    _, again = chaotic(tmp_path / "b")
    assert [r for r in again.result.events if r["type"] != "START"] == [
        r for r in emulation.result.events if r["type"] != "START"
    ]


def test_warns_when_chaos_may_exhaust_the_retries():
    def problem(**changes):
        raw = laptop(mode="emulate").model_dump(mode="json")
        raw["chaos"] = {"crash_every": 10}
        return ProblemIn.model_validate({**raw, **changes})

    p = problem()
    assert retries_needed(p.tasks[0], p.chaos) == -(-p.tasks[0].duration // 10)
    _, diagnostics = validate(p.model_dump_json().encode(), "lenient")
    warning = next(d for d in diagnostics if d.code == "W080")
    assert warning.level == "warning" and "max_retries" in warning.hint
    assert all(v["max_retries"] < v["needed"] for v in warning.detail["tasks"].values())

    many = [t | {"max_retries": 20} for t in p.model_dump(mode="json")["tasks"]]
    _, diagnostics = validate(problem(tasks=many).model_dump_json().encode(), "lenient")
    assert not [d for d in diagnostics if d.code.startswith("W08")]
    _, diagnostics = validate(problem(mode="simulate").model_dump_json().encode(), "lenient")
    assert [d.code for d in diagnostics if d.code.startswith("W08")] == ["W081"]
