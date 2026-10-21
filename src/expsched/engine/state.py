"""Изменяемое состояние симуляции. Политика видит его только через снимок."""

from __future__ import annotations

from dataclasses import dataclass, field

from expsched.domain.models import Problem, Task
from expsched.domain.resources import Resources
from expsched.domain.snapshot import RunInfo
from expsched.domain.states import TaskStatus


@dataclass
class SimState:
    problem: Problem  # исходный вход; задачи, поданные позже, есть только в `tasks`
    tasks: dict[str, Task]
    status: dict[str, TaskStatus]
    rem: dict[str, int]  # остаток работы
    attempt: dict[str, int]  # номер последней начатой попытки (0 — не запускалась)
    eligible: dict[str, tuple[str, ...]]  # допустимые узлы; узлы не меняются, поэтому считается один раз
    waiting_preds: dict[str, int]  # сколько предшественников ещё не завершено
    successors: dict[str, list[str]]  # кому уменьшить счётчик, когда задача завершится
    free: dict[str, Resources]
    up: dict[str, bool]
    run: dict[str, RunInfo] = field(default_factory=dict)
    ready_since: dict[str, int] = field(default_factory=dict)
    preempt_count: dict[str, int] = field(default_factory=dict)  # сколько раз задачу вытесняли

    @classmethod
    def from_problem(cls, problem: Problem) -> SimState:
        state = cls(
            problem=problem,
            tasks={},
            status={},
            rem={},
            attempt={},
            eligible={},
            waiting_preds={},
            successors={},
            free={w.id: w.capacity for w in problem.workers.values()},
            up={w: True for w in problem.workers},
        )
        for key in sorted(problem.tasks):
            state.add_task(problem.tasks[key])
        return state

    def add_task(self, task: Task) -> None:
        """Добавляет задачу в статусе PENDING.

        Неизвестная зависимость или самоссылка никогда не завершится, поэтому такая задача не станет READY.
        """
        deps = set(task.deps)
        self.tasks[task.id] = task
        self.status[task.id] = TaskStatus.PENDING
        self.rem[task.id] = task.duration
        self.attempt[task.id] = 0
        self.eligible[task.id] = self.problem.eligible(task)
        self.waiting_preds[task.id] = len(deps)
        for dep in sorted(deps):
            self.successors.setdefault(dep, []).append(task.id)
