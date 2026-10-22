import dataclasses
import random

import pytest

from expsched.contract.io_models import PolicyIn
from expsched.domain.models import Event, EvType, Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.engine.simulator import Simulator
from expsched.policies.backfill import BackfillPolicy
from expsched.policies.fifo import FifoPolicy
from expsched.policies.preemption import PreemptivePolicy
from expsched.policies.priority import PriorityPolicy
from expsched.policies.registry import make_policy
from expsched.verify.invariants import verify


def finishes(log):
    return {r["task"]: r["t"] for r in log if r["type"] == "FINISH"}


def preempted(log):
    return [(r["t"], r["task"], r["reason"]["by"]) for r in log if r.get("cause") == "PREEMPT"]


def run(problem, **settings):
    return Simulator(problem, PreemptivePolicy(PriorityPolicy(), **settings)).run_all()


@pytest.fixture
def urgent(make_task, make_problem):
    """Узел 2 GPU: L (low, 10 ч, чекпоинт 2) идёт с 0; U (critical, 2 ч, дедлайн 6) появляется в 3."""

    def make(**low):
        tasks = [
            make_task("L", 10, gpu=2, **{"priority": 0, "ckpt": 2, "preemptible": True, **low}),
            make_task("U", 2, gpu=2, priority=10, deadline=6, release=3),
        ]
        return make_problem(tasks, gpus=(2,))

    return make


def test_preemption_saves_deadline(urgent):
    problem = urgent()
    assert finishes(Simulator(problem, PriorityPolicy()).run_all())["U"] == 12  # без вытеснения
    log = run(problem)
    assert finishes(log) == {"U": 5, "L": 13}  # L теряет час после чекпоинта в 2
    assert preempted(log) == [(3, "L", "U")]
    start_u = next(r for r in log if r["type"] == "START" and r["task"] == "U")
    assert start_u["reason"] == {"code": "PREEMPTION_START", "victims": ["L"]}
    assert verify(problem, log, max_preemptions=1) == []


def test_no_preemption_when_the_deadline_is_met_anyway(urgent):
    problem = urgent()
    late = dataclasses.replace(problem.tasks["U"], deadline=20)
    assert preempted(run(dataclasses.replace(problem, tasks={**problem.tasks, "U": late}))) == []


def test_no_preemption_when_the_deadline_is_lost_anyway(urgent):
    problem = urgent()
    hopeless = dataclasses.replace(problem.tasks["U"], deadline=4)  # даже стартовав в 3, закончит в 5
    assert preempted(run(dataclasses.replace(problem, tasks={**problem.tasks, "U": hopeless}))) == []


@pytest.mark.parametrize(
    ("low", "settings"),
    [
        ({"priority": 8}, {}),  # жертва того же класса
        ({}, {"min_runtime": 4}),  # L проработала только 3
        ({}, {"max_preemptions": 0}),
    ],
    ids=["same-class", "min-runtime", "limit"],
)
def test_victim_rules(urgent, low, settings):
    assert preempted(run(urgent(**low), **settings)) == []


def test_ancestor_of_an_equally_important_deadline_task_is_kept(urgent, make_task):
    problem = urgent()
    report = make_task("report", 1, "L", gpu=0, priority=9, deadline=30)
    problem = dataclasses.replace(problem, tasks={**problem.tasks, "report": report})
    assert preempted(run(problem)) == []


def test_task_is_preempted_at_most_the_allowed_number_of_times(urgent, make_task):
    problem = urgent()
    second = make_task("U2", 2, gpu=2, priority=10, deadline=9, release=6)
    problem = dataclasses.replace(problem, tasks={**problem.tasks, "U2": second})
    assert preempted(run(problem)) == [(3, "L", "U")]  # L уже вытесняли: U2 ждёт и опаздывает
    assert preempted(run(problem, max_preemptions=2)) == [(3, "L", "U"), (6, "L", "U2")]


def test_cheapest_victim_is_chosen(make_task, make_problem):
    tasks = [
        make_task("A", 10, gpu=2, ckpt=1, preemptible=True),  # к t = 3 всё сохранено чекпоинтами
        make_task("B", 10, gpu=2, preemptible=True),  # без чекпоинта: потеряется 3 ч × 2 GPU
        make_task("U", 2, gpu=2, priority=10, deadline=6, release=3),
    ]
    log = run(make_problem(tasks, gpus=(2, 2)))
    assert preempted(log) == [(3, "A", "U")]


def test_registry_wraps_the_policy_by_default():
    policy = make_policy(PolicyIn())
    assert isinstance(policy, PreemptivePolicy) and isinstance(policy.base, BackfillPolicy)
    assert (policy.name, policy.max_preemptions, policy.min_runtime) == ("priority_backfill+preempt", 1, 30)
    with pytest.raises(NotImplementedError, match="preemption 'priority'"):
        make_policy(PolicyIn(preemption="priority"))


def _background_and_urgent(seed):
    """Длинные фоновые задачи, которые можно вытеснять, и срочные с тесными дедлайнами; node a падает."""
    rnd = random.Random(seed)
    tasks = {}
    for i in range(rnd.randint(3, 8)):
        tasks[f"bg{i}"] = Task(
            f"bg{i}",
            rnd.randint(4, 12),
            Resources({"gpu": rnd.choice([1, 2, 4])}),
            priority=rnd.randint(0, 4),
            preemptible=True,
            ckpt=rnd.choice([None, 1, 2, 3]),
        )
    background = sorted(tasks)
    for i in range(rnd.randint(2, 6)):
        release, duration = rnd.randint(1, 10), rnd.randint(1, 3)
        tasks[f"u{i}"] = Task(
            f"u{i}",
            duration,
            Resources({"gpu": rnd.choice([1, 2, 4])}),
            priority=rnd.randint(5, 10),
            deadline=release + duration + rnd.randint(0, 2),
            release=release,
            deps=(rnd.choice(background),) if rnd.random() < 0.2 else (),
        )
    workers = {w: Worker(w, Resources({"gpu": 4})) for w in ("a", "b")}
    down = rnd.randint(2, 12)
    events = [Event(down, EvType.WORKER_DOWN, worker="a"), Event(down + 3, EvType.WORKER_UP, worker="a")]
    return Problem(tasks, workers, events)


BASES = [FifoPolicy(), PriorityPolicy(), BackfillPolicy()]


@pytest.mark.parametrize("base", BASES, ids=lambda p: p.name)
@pytest.mark.parametrize("seed", range(30))
def test_random_schedules_with_preemption_are_valid(seed, base):
    problem = _background_and_urgent(seed)
    log = Simulator(problem, PreemptivePolicy(base)).run_all()
    assert verify(problem, log, max_preemptions=1) == []


@pytest.mark.parametrize("base", BASES, ids=lambda p: p.name)
def test_random_schedules_do_preempt(base):
    total = sum(
        len(preempted(Simulator(_background_and_urgent(s), PreemptivePolicy(base)).run_all()))
        for s in range(30)
    )
    assert total >= 10  # иначе проверка выше ничего не проверяет
