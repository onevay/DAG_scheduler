import dataclasses
from pathlib import Path

import pytest

from expsched.analysis import analyze
from expsched.contract.io_models import load
from expsched.domain.models import Event, EvType
from expsched.engine.simulator import Simulator
from expsched.explain.waits import wait_intervals
from expsched.metrics.metrics import metrics, score
from expsched.pipeline import run
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"

# ---- метрики ----


def test_demo_metrics(demo_problem):
    log = Simulator(demo_problem, PriorityPolicy()).run_all()
    m = metrics(demo_problem, log, analyze(demo_problem).lb)
    assert (m["makespan"], m["lower_bound"], m["gap_to_lb"]) == (21, 21, 0.0)
    assert m["deadline_misses"] == {"critical": 0, "high": 0, "normal": 0, "low": 0}
    assert m["weighted_tardiness"] == 0
    assert m["utilization"]["gpu"] == round(87 / (10 * 21), 4)  # полезные GPU·ч / ёмкость за прогон


def test_failure_metrics_count_lost_work_and_exclude_downtime(demo_problem):
    # node-A (4 GPU) лежит с 5 до 15; pretrain_vit теряет 1 час на 4 GPU
    events = [Event(5, EvType.WORKER_DOWN, worker="node-A"), Event(15, EvType.WORKER_UP, worker="node-A")]
    problem = dataclasses.replace(demo_problem, events=events)
    m = metrics(problem, Simulator(problem, PriorityPolicy()).run_all(), analyze(problem).lb)
    assert (m["makespan"], m["lost_work"]["gpu"]) == (22, 4)
    assert m["utilization"]["gpu"] == round(87 / (4 * (22 - 10) + 4 * 22 + 2 * 22), 4)  # 87 / 180


def test_late_task_wait_and_utilization(make_task, make_problem):
    # один GPU: критическая a идёт первой, низкая b ждёт 2 и опаздывает на 1
    tasks = [make_task("a", 2, priority=10), make_task("b", 2, priority=1, deadline=3)]
    problem = make_problem(tasks, gpus=(1,))
    m = metrics(problem, Simulator(problem, PriorityPolicy()).run_all(), lower_bound=4)
    assert m["deadline_misses"]["low"] == 1
    assert m["weighted_tardiness"] == 1  # 2^0 × 1
    assert m["mean_wait"] == 1.0  # (0 + 2) / 2
    assert m["utilization"]["gpu"] == 1.0 and m["gap_to_lb"] == 0.0


def test_unfinished_task_misses_its_deadline(make_task, make_problem, greedy):
    problem = make_problem([make_task("a", 1, "ghost", priority=6, deadline=5)])
    m = metrics(problem, Simulator(problem, greedy).run_all(), lower_bound=None)
    assert m["deadline_misses"]["high"] == 1 and m["weighted_tardiness"] == 0
    assert m["makespan"] == 0 and m["utilization"]["gpu"] is None and m["gap_to_lb"] is None


# ---- оценка по цели ----


def test_score_by_objective(make_task, make_problem):
    tasks = [make_task("a", 2, priority=10), make_task("b", 2, priority=1, deadline=3)]
    problem = make_problem(tasks, gpus=(1,))
    log = Simulator(problem, PriorityPolicy()).run_all()
    assert metrics(problem, log, None)["score"] == {"objective": "lexicographic", "key": [0, 0, 0, 1, 1, 4]}
    assert metrics(problem, log, None, "weighted")["score"] == {"objective": "weighted", "key": [5]}


def test_lexicographic_puts_one_critical_miss_above_anything_else():
    critical = {"critical": 1, "high": 0, "normal": 0, "low": 0}
    many_low = {"critical": 0, "high": 0, "normal": 0, "low": 9}
    assert score(many_low, 900, 1000, "lexicographic")["key"] < score(critical, 8, 10, "lexicographic")["key"]
    assert score(critical, 8, 10, "weighted")["key"] < score(many_low, 900, 1000, "weighted")["key"]


@pytest.mark.parametrize("objective", ["lexicographic", "weighted"])
def test_objective_ranks_meeting_deadlines_above_a_shorter_schedule(objective):
    """rush_hour: FIFO короче (18 против 21), но пропускает два критических дедлайна — priority лучше."""
    problem = load((SCENARIOS / "rush_hour.json").read_bytes())

    def outcome(name):
        return run(problem, problem.policy.model_copy(update={"name": name, "objective": objective})).result

    fifo, priority = outcome("fifo")["metrics"], outcome("priority")["metrics"]
    assert fifo["makespan"] < priority["makespan"]
    assert priority["score"]["key"] < fifo["score"]["key"]
    assert priority["score"]["objective"] == objective


# ---- интервалы ожидания ----


def test_wait_intervals_follow_changes_of_the_reason(make_task, make_problem):
    tasks = [make_task("a", 3, gpu=2), make_task("b", 1, gpu=2, release=1)]
    intervals = wait_intervals(Simulator(make_problem(tasks, gpus=(2,)), FifoPolicy()).run_all())
    assert [(i["code"], i["from"], i["to"]) for i in intervals["b"]] == [
        ("WAIT_RELEASE", 0, 1),
        ("WAIT_RESOURCES", 1, 3),  # до старта b
    ]
    assert "a" not in intervals  # стартовала сразу


def test_wait_of_a_task_that_never_starts_lasts_until_the_end(make_task, make_problem, greedy):
    log = Simulator(make_problem([make_task("a", 4), make_task("b", 1, "ghost")]), greedy).run_all()
    assert wait_intervals(log)["b"] == [
        {"code": "WAIT_DEPS", "from": 0, "to": 4, "detail": {"pending": ["ghost"]}}
    ]


def test_zero_length_intervals_are_dropped():
    log = [
        {"t": 2, "type": "WAIT", "task": "a", "code": "WAIT_DEPS", "detail": {}},
        {"t": 2, "type": "START", "task": "a"},
    ]
    assert wait_intervals(log) == {}
