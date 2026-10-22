"""Общие фикстуры: фабрики задач и экземпляров, демонстрационный DAG, тестовая политика."""

import random
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from expsched.domain.models import Event, EvType, Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Snapshot

TaskFactory = Callable[..., Task]
ProblemFactory = Callable[..., Problem]


class GreedyPolicy:
    """Простейшая политика для тестов движка: готовые задачи по id, каждой — первый подходящий узел."""

    name = "greedy-test"

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        free = dict(snapshot.free)
        out = []
        for key in snapshot.ready:
            need = snapshot.tasks[key].resources
            if need and not any(free.values()):
                break  # все узлы заполнены: задачам с требованиями места нет
            for worker in snapshot.eligible[key]:
                if snapshot.up[worker] and need.fits(free[worker]):
                    free[worker] = free[worker] - need
                    out.append(Decision("START", key, worker, {"code": "GREEDY"}))
                    break
        return out


@pytest.fixture
def greedy() -> GreedyPolicy:
    return GreedyPolicy()


def _assert_capacity_respected(problem: Problem, log: list[dict[str, Any]]) -> None:
    tasks = problem.all_tasks()
    load = {w: Resources() for w in problem.workers}
    for r in log:
        if r["type"] == "START":
            load[r["worker"]] = load[r["worker"]] + tasks[r["task"]].resources
            assert load[r["worker"]].fits(problem.workers[r["worker"]].capacity), r
        elif r["type"] == "FINISH":
            load[r["worker"]] = load[r["worker"]] - tasks[r["task"]].resources


def _random_problem(seed: int, failures: bool = False) -> Problem:
    """Случайный DAG (рёбра только к задачам с меньшим номером) на узлах a, b (4 GPU) и c (2 GPU).

    С `failures=True` у задач случайные чекпоинты и max_retries, узлы a и b по разу падают и возвращаются.
    """
    rnd = random.Random(seed)
    tasks = {}
    for i in range(rnd.randint(5, 20)):
        tasks[f"t{i}"] = Task(
            f"t{i}",
            rnd.randint(1, 5),
            Resources({"gpu": rnd.choice([1, 1, 2, 4])}),
            priority=rnd.randint(0, 10),
            deadline=rnd.choice([None, rnd.randint(5, 30)]),
            release=rnd.choice([0, 0, rnd.randint(1, 8)]),
            deps=tuple(f"t{j}" for j in range(i) if rnd.random() < 0.2),
        )
    workers = {w: Worker(w, Resources({"gpu": g})) for w, g in (("a", 4), ("b", 4), ("c", 2))}
    if not failures:
        return Problem(tasks, workers)
    tasks = {
        k: replace(t, ckpt=rnd.choice([None, 1, 2]), max_retries=rnd.choice([0, 1, 2]))
        for k, t in tasks.items()
    }
    events = []
    for worker in ("a", "b"):
        start = rnd.randint(1, 10)
        events += [
            Event(start, EvType.WORKER_DOWN, worker=worker),
            Event(start + rnd.randint(1, 8), EvType.WORKER_UP, worker=worker),
        ]
    return Problem(tasks, workers, events)


@pytest.fixture
def random_problem() -> Callable[..., Problem]:
    return _random_problem


@pytest.fixture
def check_capacity() -> Callable[[Problem, list[dict[str, Any]]], None]:
    """check_capacity(problem, log): после каждого START загрузка узла не больше его ёмкости."""
    return _assert_capacity_respected


@pytest.fixture
def make_task() -> TaskFactory:
    def make(key: str, duration: int = 1, *deps: str, gpu: int = 1, **fields: Any) -> Task:
        return Task(key, duration, Resources({"gpu": gpu} if gpu else {}), deps=tuple(deps), **fields)

    return make


@pytest.fixture
def make_problem(make_task: TaskFactory) -> ProblemFactory:
    """make_problem(tasks, gpus=(4,)) создаёт узлы n0, n1, ... с указанным числом GPU."""

    def make(tasks: list[Task], gpus: tuple[int, ...] = (4,), **fields: Any) -> Problem:
        workers = {f"n{i}": Worker(f"n{i}", Resources({"gpu": g})) for i, g in enumerate(gpus)}
        return Problem({t.id: t for t in tasks}, workers, **fields)

    return make


@pytest.fixture
def demo_problem(make_task: TaskFactory) -> Problem:
    """Демо-DAG: 11 задач на узлах с 4, 4 и 2 GPU; срочная urgent_eval появляется в t = 9 с дедлайном 14."""
    tasks = [
        make_task("prep_data", 2),
        make_task("pretrain_vit", 8, "prep_data", gpu=4, priority=2, ckpt=2),
        make_task("train_asr", 6, "prep_data", gpu=2),
        make_task("ft_det", 4, "pretrain_vit", gpu=2, priority=2),
        make_task("ft_seg", 5, "pretrain_vit", gpu=2),
        make_task("eval_asr", 2, "train_asr"),
        make_task("distill", 4, "ft_det", gpu=2),
        make_task("eval_det", 2, "ft_det", priority=2),
        make_task("eval_seg", 2, "ft_seg"),
        make_task("infer_bench", 3, "distill", "eval_det"),
        make_task("urgent_eval", 3, "eval_asr", gpu=2, priority=10, deadline=14, release=9),
    ]
    workers = {
        "node-A": Worker("node-A", Resources({"gpu": 4})),
        "node-B": Worker("node-B", Resources({"gpu": 4})),
        "node-C": Worker("node-C", Resources({"gpu": 2})),
    }
    return Problem({t.id: t for t in tasks}, workers)
