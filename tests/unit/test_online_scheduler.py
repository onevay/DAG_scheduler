from __future__ import annotations

from collections.abc import Callable

import pytest

from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, Snapshot
from expsched.online import (
    EventConflictError,
    InvalidTransitionError,
    OnlineDecisionError,
    OnlineScheduler,
    TaskPhase,
)
from expsched.policies.backfill import BackfillPolicy
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


def test_retryable_exit_waits_for_backoff_and_uses_a_new_attempt_identity() -> None:
    root = Task(
        "a",
        1,
        Resources({"cpu": 1}),
        max_retries=1,
        retry_exit_codes=(42,),
        retry_backoff=3,
    )
    child = Task("b", 1, Resources({"cpu": 1}), deps=("a",))
    scheduler = OnlineScheduler(make_problem(root, child), FifoPolicy())

    (first,) = scheduler.plan(0)
    assert first.attempt_id == "a:1"
    scheduler.ack_start("ack-a-1", first, now=0)
    scheduler.task_exited("exit-a-1", first, success=False, retryable=True, now=1)

    assert scheduler.statuses == {"a": TaskPhase.RETRY_WAIT, "b": TaskPhase.PENDING}
    assert scheduler.retry_ready_at == {"a": 4}
    assert scheduler.plan(3) == ()

    (second,) = scheduler.plan(4)
    assert (second.attempt, second.attempt_id) == (2, "a:2")
    scheduler.ack_start("ack-a-2", second, now=4)
    scheduler.task_exited("exit-a-2", second, success=False, retryable=True, now=5)

    assert scheduler.statuses == {"a": TaskPhase.FAILED, "b": TaskPhase.BLOCKED}
    assert scheduler.retry_ready_at == {}


def test_rejected_starts_consume_the_same_retry_budget() -> None:
    task = Task("task", 1, Resources({"cpu": 1}), max_retries=1)
    scheduler = OnlineScheduler(make_problem(task), FifoPolicy())

    (first,) = scheduler.plan(0)
    scheduler.reject_start("reject-1", first, now=0, retryable=True)
    assert scheduler.statuses["task"] is TaskPhase.READY

    (second,) = scheduler.plan(0)
    scheduler.reject_start("reject-2", second, now=0, retryable=True)
    assert scheduler.statuses["task"] is TaskPhase.FAILED
    assert scheduler.plan(0) == ()


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


def test_preemption_holds_resources_until_checkpointed_stop_is_acknowledged() -> None:
    low = Task("low", 10, Resources({"cpu": 1}), priority=0, preemptible=True, ckpt=2)
    urgent = Task("urgent", 2, Resources({"cpu": 1}), priority=9, release=3)

    def decide(snapshot: Snapshot) -> list[Decision]:
        if "low" in snapshot.running and "urgent" in snapshot.ready:
            return [
                Decision(
                    "PREEMPT",
                    "low",
                    "worker",
                    {"code": "PREEMPTED_BY", "by": "urgent"},
                ),
                Decision("START", "urgent", "worker", {"code": "PREEMPTION_START"}),
            ]
        for task_id in ("urgent", "low"):
            if task_id in snapshot.ready:
                return [Decision("START", task_id, "worker")]
        return []

    scheduler = OnlineScheduler(
        make_problem(low, urgent, capacity=Resources({"cpu": 1})),
        StaticPolicy(decide),
    )
    (low_start,) = scheduler.plan(0)
    scheduler.ack_start("ack-low", low_start, now=0)

    assert scheduler.plan(3) == ()
    (preemption,) = scheduler.preemptions
    assert (preemption.task, preemption.worker, preemption.attempt_id) == (
        "low",
        "worker",
        "low:1",
    )
    assert scheduler.statuses == {
        "low": TaskPhase.PREEMPTING,
        "urgent": TaskPhase.READY,
    }
    assert scheduler.snapshot().free["worker"] == Resources()
    with pytest.raises(InvalidTransitionError, match="preemption is in flight"):
        scheduler.plan(3)

    scheduler.complete_preemption("preempted-low-1", preemption, now=4)
    assert scheduler.statuses == {"low": TaskPhase.READY, "urgent": TaskPhase.READY}
    assert scheduler.snapshot().free["worker"] == Resources({"cpu": 1})
    assert scheduler.snapshot().preempt_count == {"low": 1}

    (urgent_start,) = scheduler.plan(4)
    assert urgent_start.task == "urgent"
    scheduler.ack_start("ack-urgent", urgent_start, now=4)
    scheduler.task_exited("exit-urgent", urgent_start, success=True, now=6)

    (resumed,) = scheduler.plan(6)
    assert (resumed.task, resumed.attempt, resumed.duration_estimate, resumed.worker) == (
        "low",
        2,
        6,
        "worker",
    )


def test_invalid_reservation_makes_the_whole_policy_epoch_atomic() -> None:
    tasks = [Task(key, 1, Resources({"cpu": 1})) for key in ("a", "b")]
    policy = StaticPolicy(
        lambda snapshot: [
            Decision("START", "a", "worker"),
            Decision("RESERVE", "b", "worker", {"t_res": snapshot.t - 1}),
        ]
    )
    scheduler = OnlineScheduler(make_problem(*tasks), policy)

    with pytest.raises(OnlineDecisionError, match="t_res"):
        scheduler.plan(1)
    assert scheduler.statuses == {"a": TaskPhase.READY, "b": TaskPhase.READY}
    assert scheduler.reservations == ()
    assert scheduler.snapshot().free["worker"] == scheduler.problem.workers["worker"].capacity


def test_priority_backfill_reservation_recomputes_when_a_real_task_overruns() -> None:
    tasks = [
        Task("a", 4, Resources({"cpu": 2}), priority=6),
        Task("big", 2, Resources({"cpu": 4}), priority=3),
        Task("short", 3, Resources({"cpu": 1})),
        Task("long", 6, Resources({"cpu": 1})),
    ]
    scheduler = OnlineScheduler(
        make_problem(*tasks, capacity=Resources({"cpu": 4})),
        BackfillPolicy(),
    )

    intents = scheduler.plan(0)
    assert [intent.task for intent in intents] == ["a", "short"]
    assert [(item.task, item.worker, item.t_res) for item in scheduler.reservations] == [
        ("big", "worker", 4)
    ]
    assert next(intent for intent in intents if intent.task == "short").reason == {
        "code": "BACKFILL",
        "head": "big",
        "t_res": 4,
    }
    for intent in intents:
        scheduler.ack_start(f"ack-{intent.task}", intent, now=0)

    short = next(intent for intent in intents if intent.task == "short")
    scheduler.task_exited("exit-short", short, success=True, now=3)
    assert scheduler.plan(4) == ()
    assert [(item.task, item.t_res) for item in scheduler.reservations] == [("big", 5)]

    running = next(intent for intent in intents if intent.task == "a")
    scheduler.task_exited("exit-a", running, success=True, now=5)
    next_intents = scheduler.plan(5)
    assert [intent.task for intent in next_intents] == ["big"]
    assert [(item.task, item.t_res) for item in scheduler.reservations] == [("long", 7)]


def test_worker_change_clears_a_stale_reservation_before_replanning() -> None:
    task = Task("task", 1, Resources({"cpu": 1}))
    policy = StaticPolicy(
        lambda snapshot: [
            Decision("RESERVE", "task", "worker", {"t_res": snapshot.t + 5})
        ]
    )
    scheduler = OnlineScheduler(make_problem(task), policy)

    assert scheduler.plan(0) == ()
    assert [(item.task, item.t_res) for item in scheduler.reservations] == [("task", 5)]
    scheduler.worker_down("down", "worker", now=1)
    assert scheduler.reservations == ()


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
