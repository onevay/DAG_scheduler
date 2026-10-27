"""Online coordinator over the existing scheduling policy contract.

Unlike the simulator, this module never manufactures completion events. A policy
produces start intents, workers acknowledge or reject them, and only an observed
process exit makes a running task terminal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from expsched.analysis import analyze
from expsched.domain.models import Problem, Task
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, RunInfo, Snapshot


class OnlineError(ValueError):
    """Base class for invalid online events and policy decisions."""


class EventConflictError(OnlineError):
    """An event id was reused for a different event."""


class InvalidTransitionError(OnlineError):
    """An event does not match the task's current phase or start attempt."""


class OnlineDecisionError(OnlineError):
    """A decision returned by a policy is not valid for the current snapshot."""

    def __init__(self, decision: object, reason: str) -> None:
        super().__init__(f"{reason}: {decision}")
        self.decision = decision


class UnsupportedDecisionError(OnlineDecisionError):
    """A policy action is not implemented by the online engine."""


class TaskPhase(StrEnum):
    """Online task lifecycle; STARTING is the unacknowledged start intent."""

    PENDING = "PENDING"
    READY = "READY"
    RETRY_WAIT = "RETRY_WAIT"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PREEMPTING = "PREEMPTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


_TERMINAL_PHASES = frozenset({TaskPhase.SUCCEEDED, TaskPhase.FAILED, TaskPhase.BLOCKED})
_UNSUCCESSFUL_PHASES = frozenset({TaskPhase.FAILED, TaskPhase.BLOCKED})


@dataclass(frozen=True)
class ReservationIntent:
    """Current policy promise to start a READY task on one worker by ``t_res``."""

    task: str
    worker: str
    t_res: int
    reason: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", MappingProxyType(dict(self.reason)))


@dataclass(frozen=True)
class PreemptionIntent:
    """A durable request to checkpoint and stop one running attempt."""

    task: str
    worker: str
    attempt: int
    requested_at: int
    reason: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", MappingProxyType(dict(self.reason)))

    @property
    def attempt_id(self) -> str:
        return f"{self.task}:{self.attempt}"


@dataclass(frozen=True)
class StartIntent:
    """A command to start one task attempt on one worker.

    ``duration_estimate`` and ``end_pred`` are estimates for policy planning;
    reaching ``end_pred`` never changes task state.
    """

    task: str
    worker: str
    attempt: int
    issued_at: int
    duration_estimate: int
    end_pred: int
    reason: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", MappingProxyType(dict(self.reason)))

    @property
    def attempt_id(self) -> str:
        """Stable logical identity for this task attempt."""

        return f"{self.task}:{self.attempt}"


class OnlineScheduler:
    """Single-writer online adapter for existing scheduling policies.

    ``plan`` validates START/RESERVE/PREEMPT batches. START reserves resources,
    while PREEMPT keeps them allocated until ``complete_preemption`` confirms the
    executor's checkpoint/stop boundary. All external events are idempotent by
    ``event_id``.
    """

    def __init__(self, problem: Problem, policy: Policy) -> None:
        self._validate_problem(problem)
        self._problem = replace(problem, tasks=dict(sorted(problem.tasks.items())), events=[])
        self._policy = policy
        self._now = 0
        self._tasks: dict[str, Task] = dict(self._problem.tasks)
        self._phase = {key: TaskPhase.PENDING for key in self._tasks}
        self._ready: set[str] = set()
        self._ready_since: dict[str, int] = {}
        self._submitted_at = {key: 0 for key in self._tasks}
        self._completed_at: dict[str, int] = {}
        self._attempt = {key: 0 for key in self._tasks}
        self._remaining = {key: task.duration for key, task in self._tasks.items()}
        self._retry_at: dict[str, int] = {}
        self._preempt_count: dict[str, int] = {}
        self._resume_worker: dict[str, str] = {}
        self._eligible = {key: self._problem.eligible(task) for key, task in self._tasks.items()}
        self._up = {worker: True for worker in sorted(self._problem.workers)}
        self._free = {
            worker: self._problem.workers[worker].capacity for worker in sorted(self._problem.workers)
        }
        self._intents: dict[str, StartIntent] = {}
        self._reservations: tuple[ReservationIntent, ...] = ()
        self._preemptions: dict[str, PreemptionIntent] = {}
        self._running_intents: dict[str, StartIntent] = {}
        self._running: dict[str, RunInfo] = {}
        self._allocations: dict[str, str] = {}
        self._successors = self._build_successors(self._tasks)
        self._events: dict[str, tuple[object, ...]] = {}
        self._refresh_ready()

    @property
    def now(self) -> int:
        """Current logical time."""

        return self._now

    @property
    def problem(self) -> Problem:
        """Current problem, including tasks accepted by ``submit_task``."""

        return self._problem

    @property
    def statuses(self) -> Mapping[str, TaskPhase]:
        """An immutable copy of task phases, ordered by task id."""

        return MappingProxyType(dict(sorted(self._phase.items())))

    @property
    def reservations(self) -> tuple[ReservationIntent, ...]:
        """Canonical reservations emitted by the most recent policy epoch."""

        return self._reservations

    @property
    def preemptions(self) -> tuple[PreemptionIntent, ...]:
        """Checkpoint/stop requests currently awaiting executor acknowledgement."""

        return tuple(self._preemptions[key] for key in sorted(self._preemptions))

    @property
    def attempts(self) -> Mapping[str, int]:
        """Latest allocated attempt number for each task."""

        return MappingProxyType(dict(sorted(self._attempt.items())))

    @property
    def retry_ready_at(self) -> Mapping[str, int]:
        """Logical retry deadlines for tasks currently waiting for backoff."""

        return MappingProxyType(dict(sorted(self._retry_at.items())))

    def snapshot(self) -> Snapshot:
        """Build an immutable policy snapshot at the current logical time."""

        return self._build_snapshot()

    def plan(self, now: int) -> tuple[StartIntent, ...]:
        """Ask the policy for starts, validate atomically, and create intents.

        Output is sorted by ``(task, worker, attempt)``. START, RESERVE, and PREEMPT
        are validated as one atomic decision batch. A batch containing PREEMPT creates
        only preemption intents; starts are reconsidered after the executor proves the
        victims stopped.
        """

        self._advance(now)
        if self._preemptions:
            raise InvalidTransitionError("cannot plan while preemption is in flight")
        decisions = list(self._policy.decide(self._build_snapshot()))
        planned, reservations, preemptions = self._validate_decisions(decisions)
        if preemptions:
            self._reservations = ()
            for decision, reason in preemptions:
                run = self._running[decision.task]
                preemption_intent = PreemptionIntent(
                    task=decision.task,
                    worker=decision.worker,
                    attempt=run.attempt,
                    requested_at=self._now,
                    reason=reason,
                )
                self._phase[decision.task] = TaskPhase.PREEMPTING
                self._preemptions[decision.task] = preemption_intent
            return ()
        intents: list[StartIntent] = []
        for decision, reason in planned:
            task = self._tasks[decision.task]
            self._free[decision.worker] = self._free[decision.worker] - task.resources
            self._attempt[task.id] += 1
            start_intent = StartIntent(
                task=task.id,
                worker=decision.worker,
                attempt=self._attempt[task.id],
                issued_at=self._now,
                duration_estimate=self._remaining[task.id],
                end_pred=self._now + self._remaining[task.id],
                reason=reason,
            )
            self._phase[task.id] = TaskPhase.STARTING
            self._ready.discard(task.id)
            self._intents[task.id] = start_intent
            self._allocations[task.id] = decision.worker
            intents.append(start_intent)
        self._reservations = tuple(
            ReservationIntent(
                task=decision.task,
                worker=decision.worker,
                t_res=t_res,
                reason=reason,
            )
            for decision, t_res, reason in reservations
        )
        return tuple(intents)

    def ack_start(self, event_id: str, intent: StartIntent, *, now: int) -> None:
        """Acknowledge that an emitted intent started; replay is a no-op."""

        fingerprint = ("ack_start", now, *self._intent_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        current = self._require_intent(intent)
        if not self._up[current.worker]:
            raise InvalidTransitionError(
                f"cannot acknowledge {current.task!r} on down worker {current.worker!r}; reject the intent"
            )
        self._advance(now)
        self._intents.pop(current.task)
        self._running_intents[current.task] = current
        self._phase[current.task] = TaskPhase.RUNNING
        self._resume_worker.pop(current.task, None)
        self._running[current.task] = RunInfo(
            worker=current.worker,
            start=now,
            attempt=current.attempt,
            end_pred=now + current.duration_estimate,
        )
        self._commit_event(event_id, fingerprint)

    def reject_start(
        self,
        event_id: str,
        intent: StartIntent,
        *,
        now: int,
        retryable: bool = True,
    ) -> None:
        """Reject an emitted intent and either retry it within budget or fail it."""

        if not isinstance(retryable, bool):
            raise OnlineError(f"retryable must be a boolean, got {retryable!r}")
        fingerprint = ("reject_start", now, retryable, *self._intent_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        current = self._require_intent(intent)
        self._advance(now)
        self._intents.pop(current.task)
        self._release_allocation(current.task)
        self._reservations = ()
        self._retry_or_fail(current.task, retryable=retryable, now=now)
        self._commit_event(event_id, fingerprint)

    def task_exited(
        self,
        event_id: str,
        intent: StartIntent,
        *,
        success: bool,
        retryable: bool = False,
        now: int,
    ) -> None:
        """Record an observed process exit and release resources.

        A successful exit releases dependencies. A failed retryable attempt enters
        ``RETRY_WAIT`` while budget remains; otherwise it fails and blocks descendants.
        """

        if not isinstance(success, bool):
            raise OnlineError(f"success must be a boolean, got {success!r}")
        if not isinstance(retryable, bool):
            raise OnlineError(f"retryable must be a boolean, got {retryable!r}")
        if success and retryable:
            raise OnlineError("a successful exit cannot be retryable")
        fingerprint = ("task_exited", now, success, retryable, *self._intent_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        self._require_running(intent)
        self._advance(now)
        self._running.pop(intent.task)
        self._running_intents.pop(intent.task)
        self._preemptions.pop(intent.task, None)
        self._resume_worker.pop(intent.task, None)
        self._release_allocation(intent.task)
        self._reservations = ()
        if success:
            self._phase[intent.task] = TaskPhase.SUCCEEDED
            self._remaining[intent.task] = 0
            self._completed_at[intent.task] = now
            self._refresh_ready()
        else:
            self._retry_or_fail(intent.task, retryable=retryable, now=now)
        self._commit_event(event_id, fingerprint)

    def complete_preemption(
        self,
        event_id: str,
        intent: PreemptionIntent,
        *,
        now: int,
    ) -> None:
        """Release a checkpointed/stopped victim and make it READY to restore."""

        fingerprint = ("complete_preemption", now, *self._preemption_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        current = self._require_preemption(intent)
        run = self._require_running(self._running_intents[current.task])
        self._advance(now)
        self._preemptions.pop(current.task)
        self._running.pop(current.task)
        self._running_intents.pop(current.task)
        self._release_allocation(current.task)
        self._remaining[current.task] = max(1, run.end_pred - now)
        self._preempt_count[current.task] = self._preempt_count.get(current.task, 0) + 1
        self._resume_worker[current.task] = current.worker
        self._phase[current.task] = TaskPhase.READY
        self._ready.add(current.task)
        self._ready_since[current.task] = now
        self._reservations = ()
        self._commit_event(event_id, fingerprint)

    def submit_task(self, event_id: str, task: Task, *, now: int) -> None:
        """Add one task whose dependencies already exist in the coordinator."""

        fingerprint = ("submit_task", now, task)
        if not self._begin_event(event_id, fingerprint, now):
            return
        if task.id in self._tasks:
            raise InvalidTransitionError(f"task {task.id!r} already exists")
        unknown = sorted(dep for dep in set(task.deps) if dep not in self._tasks)
        if unknown:
            raise InvalidTransitionError(f"task {task.id!r} has unknown dependencies: {unknown}")
        eligible = self._problem.eligible(task)
        if not eligible:
            raise InvalidTransitionError(f"task {task.id!r} has no eligible worker")
        tasks = dict(self._tasks)
        tasks[task.id] = task
        candidate = replace(self._problem, tasks=dict(sorted(tasks.items())))
        analyze(candidate)

        self._advance(now)
        self._reservations = ()
        self._tasks[task.id] = task
        self._phase[task.id] = TaskPhase.PENDING
        self._submitted_at[task.id] = now
        self._attempt[task.id] = 0
        self._remaining[task.id] = task.duration
        self._eligible[task.id] = eligible
        self._successors.setdefault(task.id, [])
        for dependency in sorted(set(task.deps)):
            self._successors.setdefault(dependency, []).append(task.id)
            self._successors[dependency].sort()
        self._problem = candidate
        if any(self._phase[dependency] in _UNSUCCESSFUL_PHASES for dependency in set(task.deps)):
            self._phase[task.id] = TaskPhase.BLOCKED
        else:
            self._refresh_ready()
        self._commit_event(event_id, fingerprint)

    def worker_down(self, event_id: str, worker: str, *, now: int) -> None:
        """Mark a worker unavailable without guessing that its processes exited."""

        fingerprint = ("worker_down", now, worker)
        if not self._begin_event(event_id, fingerprint, now):
            return
        self._require_worker(worker)
        self._advance(now)
        self._reservations = ()
        if self._up[worker]:
            self._up[worker] = False
            self._free[worker] = Resources()
        self._commit_event(event_id, fingerprint)

    def worker_up(self, event_id: str, worker: str, *, now: int) -> None:
        """Mark a worker available, preserving allocations not yet terminated/rejected."""

        fingerprint = ("worker_up", now, worker)
        if not self._begin_event(event_id, fingerprint, now):
            return
        self._require_worker(worker)
        self._advance(now)
        self._reservations = ()
        if not self._up[worker]:
            available = self._problem.workers[worker].capacity
            for task_id, allocated_worker in sorted(self._allocations.items()):
                if allocated_worker == worker:
                    available = available - self._tasks[task_id].resources
            self._free[worker] = available
            self._up[worker] = True
        self._commit_event(event_id, fingerprint)

    @staticmethod
    def _validate_problem(problem: Problem) -> None:
        if problem.events:
            raise OnlineError("online scheduling accepts live events; Problem.events must be empty")
        for key, task in problem.tasks.items():
            if key != task.id:
                raise OnlineError(f"task mapping key {key!r} does not match task id {task.id!r}")
        for key, worker in problem.workers.items():
            if key != worker.id:
                raise OnlineError(f"worker mapping key {key!r} does not match worker id {worker.id!r}")
        for key, task in problem.tasks.items():
            unknown = sorted(dep for dep in set(task.deps) if dep not in problem.tasks)
            if unknown:
                raise OnlineError(f"task {key!r} has unknown dependencies: {unknown}")
            if key in task.deps:
                raise OnlineError(f"task {key!r} depends on itself")
            if not problem.eligible(task):
                raise OnlineError(f"task {key!r} has no eligible worker")
        analyze(problem)

    @staticmethod
    def _build_successors(tasks: Mapping[str, Task]) -> dict[str, list[str]]:
        successors: dict[str, list[str]] = {key: [] for key in sorted(tasks)}
        for key in sorted(tasks):
            for dependency in sorted(set(tasks[key].deps)):
                successors[dependency].append(key)
        return successors

    def _build_snapshot(self) -> Snapshot:
        done = {key for key, phase in self._phase.items() if phase is TaskPhase.SUCCEEDED}
        running = {
            key: RunInfo(
                worker=run.worker,
                start=run.start,
                attempt=run.attempt,
                end_pred=max(run.end_pred, self._now + 1),
            )
            for key, run in sorted(self._running.items())
        }
        remaining = {
            key: running[key].end_pred - self._now if key in running else self._remaining[key]
            for key in sorted(self._tasks)
        }
        analysis = analyze(self._problem, remaining=remaining, now=self._now, done=done)
        return Snapshot(
            t=self._now,
            tasks=MappingProxyType(dict(sorted(self._tasks.items()))),
            ready=tuple(sorted(self._ready)),
            running=MappingProxyType(running),
            rem=MappingProxyType(remaining),
            free=MappingProxyType(dict(sorted(self._free.items()))),
            up=MappingProxyType(dict(sorted(self._up.items()))),
            eligible=MappingProxyType(
                {key: self._effective_eligible(key) for key in sorted(self._eligible)}
            ),
            analysis=analysis,
            ready_since=MappingProxyType(dict(sorted(self._ready_since.items()))),
            preempt_count=MappingProxyType(dict(sorted(self._preempt_count.items()))),
        )

    def _validate_decisions(
        self, decisions: list[Decision]
    ) -> tuple[
        list[tuple[Decision, dict[str, Any]]],
        list[tuple[Decision, int, dict[str, Any]]],
        list[tuple[Decision, dict[str, Any]]],
    ]:
        free = dict(self._free)
        selected: set[str] = set()
        preempted: set[str] = set()
        planned: list[tuple[Decision, dict[str, Any]]] = []
        reservations: list[tuple[Decision, int, dict[str, Any]]] = []
        preemptions: list[tuple[Decision, dict[str, Any]]] = []
        for decision in decisions:
            if not isinstance(decision, Decision):
                raise OnlineDecisionError(decision, "policy returned an object that is not Decision")
            if decision.action not in {"START", "RESERVE", "PREEMPT"}:
                raise OnlineDecisionError(decision, "policy returned an unknown action")
            if decision.action != "PREEMPT":
                continue
            run = self._running.get(decision.task)
            if run is None or self._phase.get(decision.task) is not TaskPhase.RUNNING:
                raise OnlineDecisionError(decision, "task is not RUNNING")
            if run.worker != decision.worker:
                raise OnlineDecisionError(decision, f"task runs on {run.worker}")
            if not self._tasks[decision.task].preemptible:
                raise OnlineDecisionError(decision, "task is not preemptible")
            if decision.task in preempted:
                raise OnlineDecisionError(decision, "task has more than one PREEMPT decision")
            preempted.add(decision.task)
            free[decision.worker] = free[decision.worker] + self._tasks[decision.task].resources
            preemptions.append((decision, dict(decision.reason)))

        for decision in decisions:
            if decision.action == "PREEMPT":
                continue
            if self._phase.get(decision.task) is not TaskPhase.READY:
                raise OnlineDecisionError(decision, "task is not READY")
            if decision.task in selected:
                raise OnlineDecisionError(decision, "task has more than one action in an epoch")
            if not self._up.get(decision.worker, False):
                raise OnlineDecisionError(decision, "worker is unknown or down")
            if decision.worker not in self._effective_eligible(decision.task):
                raise OnlineDecisionError(decision, "worker is not eligible for the task")
            selected.add(decision.task)
            reason = dict(decision.reason)
            if decision.action == "RESERVE":
                t_res = reason.get("t_res")
                if isinstance(t_res, bool) or not isinstance(t_res, int) or t_res < self._now:
                    raise OnlineDecisionError(
                        decision,
                        "reservation needs an integer t_res not earlier than now",
                    )
                reservations.append((decision, t_res, reason))
                continue
            task = self._tasks[decision.task]
            if not task.resources.fits(free[decision.worker]):
                raise OnlineDecisionError(decision, "not enough free resources")
            free[decision.worker] = free[decision.worker] - task.resources
            planned.append((decision, reason))
        return (
            sorted(planned, key=lambda item: (item[0].task, item[0].worker)),
            sorted(reservations, key=lambda item: (item[0].task, item[0].worker, item[1])),
            sorted(preemptions, key=lambda item: (item[0].task, item[0].worker)),
        )

    def _effective_eligible(self, task_id: str) -> tuple[str, ...]:
        resume_worker = self._resume_worker.get(task_id)
        return (resume_worker,) if resume_worker is not None else self._eligible[task_id]

    def _advance(self, now: int) -> None:
        self._validate_time(now)
        self._now = now
        self._refresh_ready()

    def _validate_time(self, now: int) -> None:
        if isinstance(now, bool) or not isinstance(now, int) or now < 0:
            raise OnlineError(f"time must be a non-negative integer, got {now!r}")
        if now < self._now:
            raise OnlineError(f"time cannot move backwards from {self._now} to {now}")

    def _refresh_ready(self) -> None:
        for key in sorted(self._tasks):
            if self._phase[key] is TaskPhase.RETRY_WAIT:
                retry_at = self._retry_at[key]
                if retry_at <= self._now:
                    self._retry_at.pop(key)
                    self._phase[key] = TaskPhase.READY
                    self._ready.add(key)
                    self._ready_since[key] = retry_at
                continue
            if self._phase[key] is not TaskPhase.PENDING:
                continue
            dependencies = set(self._tasks[key].deps)
            if not all(self._phase[dependency] is TaskPhase.SUCCEEDED for dependency in dependencies):
                continue
            ready_at = max(
                self._submitted_at[key],
                self._tasks[key].release,
                *(self._completed_at[dependency] for dependency in dependencies),
            )
            if ready_at <= self._now:
                self._phase[key] = TaskPhase.READY
                self._ready.add(key)
                self._ready_since[key] = ready_at

    def _retry_or_fail(self, task_id: str, *, retryable: bool, now: int) -> None:
        task = self._tasks[task_id]
        if retryable and self._attempt[task_id] <= task.max_retries:
            retry_at = now + task.retry_backoff
            self._retry_at[task_id] = retry_at
            self._phase[task_id] = TaskPhase.RETRY_WAIT
            self._refresh_ready()
            return
        self._retry_at.pop(task_id, None)
        self._phase[task_id] = TaskPhase.FAILED
        self._block_descendants(task_id)

    def _block_descendants(self, root: str) -> None:
        stack = list(reversed(self._successors.get(root, [])))
        seen: set[str] = set()
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            phase = self._phase[key]
            if phase not in _TERMINAL_PHASES:
                if phase in {TaskPhase.STARTING, TaskPhase.RUNNING, TaskPhase.PREEMPTING}:
                    raise OnlineError(f"active task {key!r} cannot be a descendant of failed task {root!r}")
                self._ready.discard(key)
                self._phase[key] = TaskPhase.BLOCKED
                stack.extend(reversed(self._successors.get(key, [])))

    def _release_allocation(self, task_id: str) -> None:
        worker = self._allocations.pop(task_id)
        if self._up[worker]:
            self._free[worker] = self._free[worker] + self._tasks[task_id].resources

    def _require_intent(self, intent: StartIntent) -> StartIntent:
        current = self._intents.get(intent.task)
        if current is None or self._intent_key(current) != self._intent_key(intent):
            raise InvalidTransitionError(f"start intent is not current: {intent}")
        return current

    def _require_running(self, intent: StartIntent) -> RunInfo:
        current = self._running_intents.get(intent.task)
        run = self._running.get(intent.task)
        if (
            current is None
            or self._intent_key(current) != self._intent_key(intent)
            or run is None
            or run.worker != intent.worker
            or run.attempt != intent.attempt
        ):
            raise InvalidTransitionError(f"task attempt is not RUNNING: {intent}")
        return run

    def _require_preemption(self, intent: PreemptionIntent) -> PreemptionIntent:
        current = self._preemptions.get(intent.task)
        if current is None or self._preemption_key(current) != self._preemption_key(intent):
            raise InvalidTransitionError(f"preemption intent is not current: {intent}")
        return current

    def _require_worker(self, worker: str) -> None:
        if worker not in self._up:
            raise InvalidTransitionError(f"unknown worker {worker!r}")

    def _begin_event(self, event_id: str, fingerprint: tuple[object, ...], now: int) -> bool:
        if not isinstance(event_id, str) or not event_id:
            raise OnlineError("event_id must be a non-empty string")
        previous = self._events.get(event_id)
        if previous is not None:
            if previous != fingerprint:
                raise EventConflictError(f"event_id {event_id!r} was already used for another event")
            return False
        self._validate_time(now)
        return True

    def _commit_event(self, event_id: str, fingerprint: tuple[object, ...]) -> None:
        self._events[event_id] = fingerprint

    @staticmethod
    def _preemption_key(intent: PreemptionIntent) -> tuple[object, ...]:
        return (
            intent.task,
            intent.worker,
            intent.attempt,
            intent.requested_at,
            tuple(sorted(intent.reason.items())),
        )

    @staticmethod
    def _intent_key(intent: StartIntent) -> tuple[object, ...]:
        return (
            intent.task,
            intent.worker,
            intent.attempt,
            intent.issued_at,
            intent.duration_estimate,
            intent.end_pred,
        )
