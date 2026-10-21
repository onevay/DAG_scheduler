from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.engine.snapshot import build_snapshot
from expsched.engine.state import SimState
from expsched.policies.base import best_fit


def snapshot_of(task: Task, workers: dict[str, dict[str, int]], labels=None, down=()):
    """Снимок с одной готовой задачей; workers: id -> ёмкость."""
    problem = Problem(
        {task.id: task},
        {
            w: Worker(w, Resources(cap), tuple((labels or {}).get(w, {}).items()))
            for w, cap in workers.items()
        },
    )
    state = SimState.from_problem(problem)
    for w in down:
        state.up[w] = False
    return build_snapshot(state, 0)


def pick(task, workers, free=None, **kwargs):
    snap = snapshot_of(task, workers, **kwargs)
    return best_fit(snap, task.id, free if free is not None else snap.free)


def test_tightest_node_wins():
    task = Task("t", 1, Resources({"gpu": 2}))
    assert pick(task, {"a": {"gpu": 4}, "b": {"gpu": 2}, "c": {"gpu": 3}}) == "b"


def test_ties_are_broken_by_total_leftover_then_by_id():
    # остаток gpu одинаковый (2), по сумме остатков ресурсов задачи b теснее: 2 + 1 против 2 + 7
    task = Task("t", 1, Resources({"gpu": 2, "cpu": 1}))
    assert pick(task, {"a": {"gpu": 4, "cpu": 8}, "b": {"gpu": 4, "cpu": 2}}) == "b"
    assert pick(Task("t", 1, Resources({"gpu": 2})), {"b": {"gpu": 4}, "a": {"gpu": 4}}) == "a"


def test_dominant_resource_decides():
    # ram — 32 из 64 (половина), gpu — 1 из 4 (четверть): доминирует ram, по нему и выбираем самый тесный
    task = Task("t", 1, Resources({"gpu": 1, "ram_gb": 32}))
    workers = {"gpu_tight": {"gpu": 1, "ram_gb": 64}, "ram_tight": {"gpu": 4, "ram_gb": 32}}
    assert pick(task, workers) == "ram_tight"


def test_down_and_ineligible_workers_are_skipped():
    task = Task("t", 1, Resources({"gpu": 1}), constraints=(("gpu_type", "a100"),))
    workers = {"a": {"gpu": 1}, "b": {"gpu": 1}, "c": {"gpu": 8}}
    labels = {"a": {"gpu_type": "a100"}, "b": {"gpu_type": "a100"}}
    assert pick(task, workers, labels=labels, down=("a",)) == "b"


def test_free_resources_already_taken_in_this_epoch_are_respected():
    task = Task("t", 1, Resources({"gpu": 2}))
    workers = {"a": {"gpu": 2}, "b": {"gpu": 4}}
    assert pick(task, workers, free={"a": Resources(), "b": Resources({"gpu": 4})}) == "b"


def test_none_when_the_task_fits_nowhere_now():
    task = Task("t", 1, Resources({"gpu": 4}))
    assert pick(task, {"a": {"gpu": 4}}, free={"a": Resources({"gpu": 3})}) is None


def test_task_without_resources_takes_the_first_eligible_worker():
    assert pick(Task("t", 1), {"b": {"gpu": 1}, "a": {"gpu": 1}}) == "a"
