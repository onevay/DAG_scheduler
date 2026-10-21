"""Фабрики для тестов: маленькие задачи и демонстрационный DAG из спецификации (раздел 17)."""

from collections.abc import Callable
from typing import Any

import pytest

from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources

TaskFactory = Callable[..., Task]
ProblemFactory = Callable[..., Problem]


@pytest.fixture
def make_task() -> TaskFactory:
    def make(key: str, duration: int = 1, *deps: str, gpu: int = 1, **fields: Any) -> Task:
        return Task(key, duration, Resources({"gpu": gpu} if gpu else {}), deps=tuple(deps), **fields)

    return make


@pytest.fixture
def make_problem(make_task: TaskFactory) -> ProblemFactory:
    """make_problem(tasks, gpus=(4,)): узлы n0, n1, ... с указанным числом GPU."""

    def make(tasks: list[Task], gpus: tuple[int, ...] = (4,), **fields: Any) -> Problem:
        workers = {f"n{i}": Worker(f"n{i}", Resources({"gpu": g})) for i, g in enumerate(gpus)}
        return Problem({t.id: t for t in tasks}, workers, **fields)

    return make


@pytest.fixture
def demo_problem(make_task: TaskFactory) -> Problem:
    """Демо спецификации в «офлайн» виде: срочная задача входит в набор с release = 9."""
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
