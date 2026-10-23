import time

import pytest

from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.domain.states import TaskStatus
from expsched.engine.simulator import Simulator

# Замер по настенным часам зависит от нагрузки машины, поэтому эти тесты не входят в `make test`:
# они запускаются `make perf` и отдельным шагом CI.
pytestmark = pytest.mark.slow

N = 10_000
WORKERS = {f"n{i}": Worker(f"n{i}", Resources({"gpu": 4})) for i in range(4)}


def chain() -> dict[str, Task]:
    return {
        f"t{i}": Task(f"t{i}", 1, Resources({"gpu": 1}), deps=(f"t{i - 1}",) if i else ()) for i in range(N)
    }


def wide() -> dict[str, Task]:
    """У каждой задачи до двух зависимостей из начала графа: много готовых задач одновременно."""
    return {
        f"t{i}": Task(
            f"t{i}",
            1 + i % 5,
            Resources({"gpu": 1 + i % 2}),
            deps=tuple(f"t{j}" for j in {i // 2, i // 3} if j < i),
        )
        for i in range(N)
    }


@pytest.mark.parametrize("shape", [chain, wide])
def test_ten_thousand_tasks_run_in_seconds(shape, greedy):
    sim = Simulator(Problem(shape(), WORKERS), greedy)
    start = time.perf_counter()
    sim.run_all()
    assert time.perf_counter() - start < 5
    assert set(sim.state.status.values()) == {TaskStatus.SUCCEEDED}
