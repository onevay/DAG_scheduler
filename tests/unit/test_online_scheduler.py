from __future__ import annotations

from collections.abc import Callable

import pytest

from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Action, Decision, Policy, Snapshot
from expsched.online import (
    EventConflictError,
    InvalidTransitionError,
    OnlineDecisionError,
    OnlineScheduler,
    TaskPhase,
    UnsupportedDecisionError,
)
from expsched.policies.fifo import FifoPolicy
from expsched.policies.priority import PriorityPolicy


def make_problem(*tasks: Task, capacity: Resources | None = None) -> Problem:
    worker = Worker("worker", capacity or Resources({"cpu": 8, "license": 2, "ram_mb": 32}))
    return Problem({task.id: task for task in tasks}, {worker.id: worker})


def test_two_phase_start_holds_arbitrary_resources_until_real_exit() -> None:
    task = Task("train", 3, Resources({"license": 1, "ram_mb": 12}))
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())

    (intent,) = scheduler.plan(0)
    assert (
        intent.task,
        intent.worker,
        intent.attempt,
        intent.duration_estimate,
        intent.end_pred,
    ) == ("train", "worker", 1, 3, 3)
    assert scheduler.statuses["train"] is TaskPhase.STARTING
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 8, "license": 1, "ram_mb": 20})

    scheduler.ack_start("ack-1", intent, now=1)
    assert scheduler.statuses["train"] is TaskPhase.RUNNING
    run = scheduler.snapshot().running["train"]
    assert (run.worker, run.start, run.attempt, run.end_pred) == ("worker", 1, 1, 4)

    assert scheduler.plan(100) == ()
    assert scheduler.statuses["train"] is TaskPhase.RUNNING
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 8, "license": 1, "ram_mb": 20})

    scheduler.task_exited("exit-1", intent, success=True, now=101)
    assert scheduler.statuses["train"] is TaskPhase.SUCCEEDED
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity


def test_overdue_running_task_remains_a_positive_forecast_without_finishing() -> None:
    task = Task("task", 2, Resources({"cpu": 1}))
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())
    (intent,) = scheduler.plan(0)
    scheduler.ack_start("ack", intent, now=0)

    assert scheduler.plan(10) == ()
    snapshot = scheduler.snapshot()
    assert scheduler.statuses["task"] is TaskPhase.RUNNING
    assert snapshot.rem["task"] == 1
    assert snapshot.running["task"].end_pred == 11


def test_rejected_start_returns_to_ready_and_stale_replay_cannot_release_new_attempt() -> None:
    task = Task("task", 5, Resources({"cpu": 8}))
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())

    (first,) = scheduler.plan(0)
    scheduler.reject_start("reject-1", first, now=0)
    assert scheduler.statuses["task"] is TaskPhase.READY
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity

    (second,) = scheduler.plan(0)
    assert second.attempt == 2
    scheduler.reject_start("reject-1", first, now=0)
    assert scheduler.statuses["task"] is TaskPhase.STARTING
    assert not task.resources.fits(scheduler.snapshot().free["worker"])

    with pytest.raises(EventConflictError, match="already used"):
        scheduler.reject_start("reject-1", second, now=0)


def test_success_releases_dependency_only_after_observed_exit() -> None:
    parent = Task("parent", 2, Resources({"cpu": 8}))
    child = Task("child", 1, Resources({"cpu": 8}), deps=("parent",))
    scheduler = OnlineScheduler(make_problem(parent, child), FifoPolicy())

    (parent_intent,) = scheduler.plan(0)
    scheduler.ack_start("ack-parent", parent_intent, now=0)
    assert scheduler.plan(2) == ()
    assert scheduler.statuses == {"child": TaskPhase.PENDING, "parent": TaskPhase.RUNNING}

    scheduler.task_exited("exit-parent", parent_intent, success=True, now=7)
    assert scheduler.statuses["child"] is TaskPhase.READY
    (child_intent,) = scheduler.plan(7)
    assert (child_intent.task, child_intent.issued_at) == ("child", 7)


def test_failed_exit_blocks_all_unfinished_descendants() -> None:
    root = Task("a", 1, Resources({"cpu": 1}))
    child = Task("b", 1, Resources({"cpu": 1}), deps=("a",))
    grandchild = Task("c", 1, Resources({"cpu": 1}), deps=("b",))
    scheduler = OnlineScheduler(make_problem(root, child, grandchild), FifoPolicy())

    (intent,) = scheduler.plan(0)
    scheduler.ack_start("ack-a", intent, now=0)
    scheduler.task_exited("exit-a", intent, success=False, now=1)

    assert scheduler.statuses == {
        "a": TaskPhase.FAILED,
        "b": TaskPhase.BLOCKED,
        "c": TaskPhase.BLOCKED,
    }
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity


class StaticPolicy:
    name = "static-test"

    def __init__(self, build: Callable[[Snapshot], list[Decision]]) -> None:
        self._build = build

    def decide(self, snapshot: Snapshot) -> list[Decision]:
        return self._build(snapshot)


def test_plan_has_canonical_order_even_if_policy_returns_reverse_order() -> None:
    tasks = [Task(key, 1, Resources({"cpu": 1})) for key in ("a", "z")]
    policy = StaticPolicy(
        lambda snapshot: [
            Decision("START", "z", "worker", {"code": "SECOND_ID"}),
            Decision("START", "a", "worker", {"code": "FIRST_ID"}),
        ]
    )
    scheduler = OnlineScheduler(make_problem(*tasks), policy)

    intents = scheduler.plan(0)
    assert [intent.task for intent in intents] == ["a", "z"]
    assert [intent.reason["code"] for intent in intents] == ["FIRST_ID", "SECOND_ID"]


def test_invalid_multi_start_plan_is_atomic() -> None:
    tasks = [Task(key, 1, Resources({"cpu": 1})) for key in ("a", "b")]
    policy = StaticPolicy(
        lambda snapshot: [
            Decision("START", "a", "worker"),
            Decision("START", "b", "worker"),
        ]
    )
    scheduler = OnlineScheduler(make_problem(*tasks, capacity=Resources({"cpu": 1})), policy)

    with pytest.raises(OnlineDecisionError, match="not enough free resources"):
        scheduler.plan(0)
    assert scheduler.statuses == {"a": TaskPhase.READY, "b": TaskPhase.READY}
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 1})


@pytest.mark.parametrize("action", ["PREEMPT", "RESERVE"])
def test_unsupported_policy_actions_fail_explicitly(action: Action) -> None:
    task = Task("task", 1, Resources({"cpu": 1}))
    policy = StaticPolicy(lambda snapshot: [Decision(action, "task", "worker")])
    scheduler = OnlineScheduler(make_problem(task), policy)

    with pytest.raises(UnsupportedDecisionError, match=f"does not support {action}"):
        scheduler.plan(0)
    assert scheduler.statuses["task"] is TaskPhase.READY


@pytest.mark.parametrize("policy", [FifoPolicy(), PriorityPolicy()])
def test_existing_non_preemptive_policies_create_start_intents(policy: Policy) -> None:
    task = Task("task", 4, Resources({"license": 1}))
    scheduler = OnlineScheduler(make_problem(task), policy)

    (intent,) = scheduler.plan(0)
    assert (intent.task, intent.worker) == ("task", "worker")


def test_event_id_replay_is_idempotent_and_conflicting_reuse_is_rejected() -> None:
    task = Task("task", 1, Resources({"cpu": 2}))
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())
    (intent,) = scheduler.plan(0)

    scheduler.ack_start("event", intent, now=0)
    scheduler.plan(5)
    scheduler.ack_start("event", intent, now=0)
    assert scheduler.statuses["task"] is TaskPhase.RUNNING

    with pytest.raises(EventConflictError, match="already used"):
        scheduler.task_exited("event", intent, success=True, now=5)

    scheduler.task_exited("exit", intent, success=True, now=5)
    scheduler.task_exited("exit", intent, success=True, now=5)
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity


def test_submit_task_is_idempotent_and_joins_the_live_dag() -> None:
    root = Task("root", 1, Resources({"cpu": 1}))
    scheduler = OnlineScheduler(make_problem(root), FifoPolicy())
    (root_intent,) = scheduler.plan(0)
    scheduler.ack_start("ack-root", root_intent, now=0)
    child = Task("child", 1, Resources({"cpu": 1}), deps=("root",))

    scheduler.submit_task("submit-child", child, now=1)
    scheduler.submit_task("submit-child", child, now=1)
    assert scheduler.statuses["child"] is TaskPhase.PENDING

    scheduler.task_exited("exit-root", root_intent, success=True, now=2)
    assert scheduler.statuses["child"] is TaskPhase.READY
    assert scheduler.plan(2)[0].task == "child"


def test_invalid_submit_is_not_recorded_and_may_be_corrected_with_same_event_id() -> None:
    scheduler = OnlineScheduler(make_problem(), FifoPolicy())
    invalid = Task("task", 1, Resources({"cpu": 1}), deps=("missing",))

    with pytest.raises(InvalidTransitionError, match="unknown dependencies"):
        scheduler.submit_task("submit", invalid, now=0)
    scheduler.submit_task("submit", Task("task", 1, Resources({"cpu": 1})), now=0)
    assert scheduler.statuses["task"] is TaskPhase.READY


def test_worker_down_and_up_never_assume_a_running_task_exited() -> None:
    task = Task("task", 1, Resources({"cpu": 3}))
    scheduler = OnlineScheduler(make_problem(task, capacity=Resources({"cpu": 4})), FifoPolicy())
    (intent,) = scheduler.plan(0)
    scheduler.ack_start("ack", intent, now=0)

    scheduler.worker_down("down", "worker", now=1)
    assert scheduler.statuses["task"] is TaskPhase.RUNNING
    assert scheduler.snapshot().free["worker"] == Resources()

    scheduler.worker_up("up", "worker", now=2)
    assert scheduler.statuses["task"] is TaskPhase.RUNNING
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 1})

    scheduler.task_exited("exit", intent, success=True, now=3)
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 4})


def test_intent_on_a_down_worker_must_be_rejected_not_acknowledged() -> None:
    task = Task("task", 1, Resources({"cpu": 1}))
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())
    (intent,) = scheduler.plan(0)
    scheduler.worker_down("down", "worker", now=1)

    with pytest.raises(InvalidTransitionError, match="down worker"):
        scheduler.ack_start("ack", intent, now=1)
    scheduler.reject_start("reject", intent, now=1)
    assert scheduler.statuses["task"] is TaskPhase.READY
    assert scheduler.snapshot().free["worker"] == Resources()

    scheduler.worker_up("up", "worker", now=2)
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity
