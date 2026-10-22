"""Детерминированная дискретно-событийная симуляция.

Эпоха: все события ближайшего момента в порядке очереди, затем одно решение политики, затем применение
решений с проверкой (guard). Всё, что происходит, записывается в журнал. Решать имеет смысл только
в моменты событий: старт задачи возможен лишь тогда, когда что-то освободилось или стало готовым.

У каждой нестартовавшей задачи есть причина ожидания (WAIT_*). Запись WAIT появляется, когда причина
меняется, и действует до следующей записи WAIT этой задачи или до её старта.

События узлов, PREEMPT и RESERVE пока дают NotImplementedError.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from expsched.domain.models import Event, EvType, Problem, Task
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, RunInfo
from expsched.domain.states import FINAL_STATUSES, TaskStatus
from expsched.engine.errors import InvalidDecision
from expsched.engine.events import EventQueue, Finish, Payload, Release
from expsched.engine.journal import Journal
from expsched.engine.snapshot import build_snapshot
from expsched.engine.state import SimState
from expsched.engine.waits import ready_wait
from expsched.validation.graph import validate_graph
from expsched.validation.resources import validate_resources


class Simulator:
    """Симуляция одного экземпляра. Вход должен пройти validate и quarantine.

    Если в графе всё же есть цикл или неизвестная зависимость, такие задачи не стартуют и в конце
    получают диагностику STUCK.
    """

    def __init__(self, problem: Problem, policy: Policy) -> None:
        self.policy = policy
        self.state = SimState.from_problem(problem)
        self.journal = Journal()
        self.queue = EventQueue()
        self.t = 0
        self._finalized = False
        self._reported = 0  # сколько записей журнала уже вернул step()
        # Release на каждую задачу: момент готовности задачи без зависимостей и первая эпоха в t = 0.
        for key in sorted(problem.tasks):
            self.queue.push(problem.tasks[key].release, Release(key))
            self._pending_wait(key)
        for event in problem.events:
            self.queue.push(event.time, event)

    def step(self) -> list[dict[str, Any]] | None:
        """Выполняет одну эпоху и возвращает новые записи журнала (список может быть пустым).

        None — событий больше нет.
        """
        if not self.queue:
            return None
        self.t, batch = self.queue.pop_batch()
        for payload in batch:
            self._handle(payload)
        self._decide()
        first, self._reported = self._reported, len(self.journal)
        return list(self.journal.records[first:])

    def run_all(self) -> list[dict[str, Any]]:
        while self.step() is not None:
            pass
        self._finalize()
        return list(self.journal.records)

    # ---- события ----

    def _handle(self, payload: Payload) -> None:
        if isinstance(payload, Finish):
            self._finish(payload)
        elif isinstance(payload, Release):
            self._try_ready(payload.task)
        else:
            self._external(payload)

    def _external(self, event: Event) -> None:
        if event.type is not EvType.SUBMIT:
            raise NotImplementedError(f"event {event.type.value} is not supported yet")
        if event.task is None:
            raise ValueError("submit_task event without a task")
        self._submit(event.task)

    def _submit(self, task: Task) -> None:
        """Задача, поданная во время прогона. Задача с ошибками получает INFEASIBLE и диагностику."""
        s = self.state
        if task.id in s.tasks:
            self.journal.append("DIAGNOSTIC", self.t, code="DUPLICATE_TASK_ID", task=task.id)
            return
        s.add_task(task)
        self.journal.append("SUBMIT", self.t, task=task.id, priority=task.priority, deadline=task.deadline)

        # Проверяется только новая задача: у остальных ошибок либо нет, либо они уже учтены до запуска.
        alone = replace(s.problem, tasks={task.id: task})
        problems = [
            d for d in validate_graph(s.tasks) if d.detail.get("task") == task.id and d.level == "error"
        ]
        problems += validate_resources(alone)
        if problems:
            s.status[task.id] = TaskStatus.INFEASIBLE
            for diagnostic in problems:
                self.journal.append("DIAGNOSTIC", self.t, task=task.id, **diagnostic.to_dict())
            return
        if task.release > self.t:
            self.queue.push(task.release, Release(task.id))
        self._try_ready(task.id)

    def _finish(self, event: Finish) -> None:
        s = self.state
        run = s.run.get(event.task)
        if run is None or run.attempt != event.attempt:
            return  # окончание уже прерванной попытки: такие события не удаляются из очереди, а пропускаются
        del s.run[event.task]
        s.free[run.worker] = s.free[run.worker] + s.tasks[event.task].resources
        s.status[event.task] = TaskStatus.SUCCEEDED
        s.rem[event.task] = 0
        self.journal.append("FINISH", self.t, task=event.task, attempt=event.attempt, worker=run.worker)
        for successor in s.successors.get(event.task, []):
            s.waiting_preds[successor] -= 1
            self._try_ready(successor)

    def _try_ready(self, key: str) -> None:
        """READY, если предшественники завершены и наступил release_time; иначе — новая причина ожидания."""
        s = self.state
        if s.status[key] is not TaskStatus.PENDING:
            return
        if s.waiting_preds[key] > 0 or self.t < s.tasks[key].release:
            self._pending_wait(key)
            return
        s.status[key] = TaskStatus.READY
        s.ready.add(key)
        s.ready_since[key] = self.t
        s.wait_code.pop(key, None)  # причина ожидания готовой задачи определится после решений эпохи
        self.journal.append("READY", self.t, task=key)

    # ---- причины ожидания ----

    def _pending_wait(self, key: str) -> None:
        s = self.state
        task = s.tasks[key]
        if s.waiting_preds[key] > 0:
            if s.wait_code.get(key) != "WAIT_DEPS":
                pending = sorted(d for d in set(task.deps) if s.status.get(d) is not TaskStatus.SUCCEEDED)
                self._set_wait(key, "WAIT_DEPS", {"pending": pending})
        elif self.t < task.release:
            self._set_wait(key, "WAIT_RELEASE", {"until": task.release})

    def _set_wait(self, key: str, code: str, detail: dict[str, Any]) -> None:
        if self.state.wait_code.get(key) != code:
            self.state.wait_code[key] = code
            self.journal.append("WAIT", self.t, task=key, code=code, detail=dict(detail))

    # ---- решения ----

    def _decide(self) -> None:
        s = self.state
        if not s.ready:
            return
        free_before = dict(s.free)
        # Снимок смотрит на живое состояние, поэтому решения собираются целиком до их применения.
        decisions = list(self.policy.decide(build_snapshot(s, self.t)))
        started: list[str] = []
        for decision in decisions:
            if decision.action == "START":
                self._start(decision)
                started.append(decision.task)
            elif decision.action in ("PREEMPT", "RESERVE"):
                raise NotImplementedError(f"{decision.action} is not supported yet")
            else:
                raise InvalidDecision(decision, "unknown action")
        # Причина зависит только от требований задачи и её допустимых узлов: у задач одной формы она общая.
        by_shape: dict[tuple[Resources, tuple[str, ...]], tuple[str, dict[str, Any]]] = {}
        for key in sorted(s.ready):
            shape = (s.tasks[key].resources, s.eligible[key])
            if shape not in by_shape:
                by_shape[shape] = ready_wait(s, key, free_before, started)
            self._set_wait(key, *by_shape[shape])

    def _start(self, decision: Decision) -> None:
        s = self.state
        key, worker = decision.task, decision.worker
        if s.status.get(key) is not TaskStatus.READY:
            raise InvalidDecision(decision, "task is not READY")
        if not s.up.get(worker, False):
            raise InvalidDecision(decision, "worker is unknown or down")
        if worker not in s.eligible[key]:
            raise InvalidDecision(decision, "worker is not eligible for the task")
        task = s.tasks[key]
        if not task.resources.fits(s.free[worker]):
            raise InvalidDecision(decision, "not enough free resources")

        s.free[worker] = s.free[worker] - task.resources
        s.status[key] = TaskStatus.RUNNING
        s.ready.discard(key)
        s.wait_code.pop(key, None)
        s.attempt[key] += 1
        end = self.t + s.rem[key]
        s.run[key] = RunInfo(worker, self.t, s.attempt[key], end)
        self.queue.push(end, Finish(key, s.attempt[key]))
        self.journal.append(
            "START",
            self.t,
            task=key,
            worker=worker,
            attempt=s.attempt[key],
            resume_from=task.duration - s.rem[key],
            reason=dict(decision.reason),
        )

    def _finalize(self) -> None:
        """Записывает STUCK для каждой задачи, не дошедшей до финального статуса."""
        if self._finalized:
            return
        self._finalized = True
        for key in sorted(self.state.status):
            if self.state.status[key] not in FINAL_STATUSES:
                self.journal.append("DIAGNOSTIC", self.t, code="STUCK", task=key)
