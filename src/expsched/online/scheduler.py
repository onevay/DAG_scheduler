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
    """The first online slice deliberately supports START decisions only."""


class TaskPhase(StrEnum):
    """Online task lifecycle; STARTING is the unacknowledged start intent."""

    PENDING = "PENDING"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


_TERMINAL_PHASES = frozenset({TaskPhase.SUCCEEDED, TaskPhase.FAILED, TaskPhase.BLOCKED})
_UNSUCCESSFUL_PHASES = frozenset({TaskPhase.FAILED, TaskPhase.BLOCKED})


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


class OnlineScheduler:
    """Stateful, single-writer online adapter for existing non-preemptive policies.

    ``plan`` performs READY -> STARTING and reserves resources. ``ack_start``
    performs STARTING -> RUNNING without changing resources. Resources are
    returned only by ``reject_start`` or ``task_exited``. All external event
    methods are idempotent by ``event_id``.
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
        self._eligible = {key: self._problem.eligible(task) for key, task in self._tasks.items()}
        self._up = {worker: True for worker in sorted(self._problem.workers)}
        self._free = {
            worker: self._problem.workers[worker].capacity for worker in sorted(self._problem.workers)
        }
        self._intents: dict[str, StartIntent] = {}
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

    def snapshot(self) -> Snapshot:
        """Build an immutable policy snapshot at the current logical time."""

        return self._build_snapshot()

    def plan(self, now: int) -> tuple[StartIntent, ...]:
        """Ask the policy for starts, validate atomically, and create intents.

        Output is sorted by ``(task, worker, attempt)``. Unsupported PREEMPT and
        RESERVE decisions fail explicitly; no intent is applied if any decision
        in the returned plan is invalid.
        """

        self._advance(now)
        decisions = list(self._policy.decide(self._build_snapshot()))
        planned = self._validate_decisions(decisions)
        intents: list[StartIntent] = []
        for decision, reason in planned:
            task = self._tasks[decision.task]
            self._free[decision.worker] = self._free[decision.worker] - task.resources
            self._attempt[task.id] += 1
            intent = StartIntent(
                task=task.id,
                worker=decision.worker,
                attempt=self._attempt[task.id],
                issued_at=self._now,
                duration_estimate=task.duration,
                end_pred=self._now + task.duration,
                reason=reason,
            )
            self._phase[task.id] = TaskPhase.STARTING
            self._ready.discard(task.id)
            self._intents[task.id] = intent
            self._allocations[task.id] = decision.worker
            intents.append(intent)
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
        self._running[current.task] = RunInfo(
            worker=current.worker,
            start=now,
            attempt=current.attempt,
            end_pred=now + current.duration_estimate,
        )
        self._commit_event(event_id, fingerprint)

    def reject_start(self, event_id: str, intent: StartIntent, *, now: int) -> None:
        """Reject an emitted intent, return its resources, and make the task READY."""

        fingerprint = ("reject_start", now, *self._intent_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        current = self._require_intent(intent)
        self._advance(now)
        self._intents.pop(current.task)
        self._release_allocation(current.task)
        self._phase[current.task] = TaskPhase.READY
        self._ready.add(current.task)
        self._commit_event(event_id, fingerprint)

    def task_exited(self, event_id: str, intent: StartIntent, *, success: bool, now: int) -> None:
        """Record an observed process exit and release resources.

        A successful exit releases dependencies. A failed exit is terminal and
        recursively blocks every unfinished descendant. ``Task.max_retries`` is
        not interpreted by this first online slice.
        """

        if not isinstance(success, bool):
            raise OnlineError(f"success must be a boolean, got {success!r}")
        fingerprint = ("task_exited", now, success, *self._intent_key(intent))
        if not self._begin_event(event_id, fingerprint, now):
            return
        self._require_running(intent)
        self._advance(now)
        self._running.pop(intent.task)
        self._running_intents.pop(intent.task)
        self._release_allocation(intent.task)
        self._phase[intent.task] = TaskPhase.SUCCEEDED if success else TaskPhase.FAILED
        if success:
            self._completed_at[intent.task] = now
            self._refresh_ready()
        else:
            self._block_descendants(intent.task)
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
        self._tasks[task.id] = task
        self._phase[task.id] = TaskPhase.PENDING
        self._submitted_at[task.id] = now
        self._attempt[task.id] = 0
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
        successors = {key: [] for key in sorted(tasks)}
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
            key: running[key].end_pred - self._now if key in running else task.duration
            for key, task in sorted(self._tasks.items())
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
            eligible=MappingProxyType(dict(sorted(self._eligible.items()))),
            analysis=analysis,
            ready_since=MappingProxyType(dict(sorted(self._ready_since.items()))),
            preempt_count=MappingProxyType({}),
        )

    def _validate_decisions(self, decisions: list[Decision]) -> list[tuple[Decision, dict[str, Any]]]:
        free = dict(self._free)
        selected: set[str] = set()
        planned: list[tuple[Decision, dict[str, Any]]] = []
        for decision in decisions:
            if not isinstance(decision, Decision):
                raise OnlineDecisionError(decision, "policy returned an object that is not Decision")
            if decision.action != "START":
                raise UnsupportedDecisionError(
                    decision, f"online scheduler does not support {decision.action} decisions"
                )
            if self._phase.get(decision.task) is not TaskPhase.READY:
                raise OnlineDecisionError(decision, "task is not READY")
            if decision.task in selected:
                raise OnlineDecisionError(decision, "task has more than one START decision")
            if not self._up.get(decision.worker, False):
                raise OnlineDecisionError(decision, "worker is unknown or down")
            if decision.worker not in self._eligible[decision.task]:
                raise OnlineDecisionError(decision, "worker is not eligible for the task")
            task = self._tasks[decision.task]
            if not task.resources.fits(free[decision.worker]):
                raise OnlineDecisionError(decision, "not enough free resources")
            free[decision.worker] = free[decision.worker] - task.resources
            selected.add(decision.task)
            planned.append((decision, dict(decision.reason)))
        return sorted(planned, key=lambda item: (item[0].task, item[0].worker))

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
                if phase in {TaskPhase.STARTING, TaskPhase.RUNNING}:
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
    def _intent_key(intent: StartIntent) -> tuple[object, ...]:
        return (
            intent.task,
            intent.worker,
            intent.attempt,
            intent.issued_at,
            intent.duration_estimate,
            intent.end_pred,
        )
