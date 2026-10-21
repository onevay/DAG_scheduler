"""Изменяемое состояние симуляции. Живёт только в движке; наружу уходит снимком (engine/snapshot.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

from expsched.domain.models import Problem, Task
from expsched.domain.resources import Resources
from expsched.domain.snapshot import RunInfo
from expsched.domain.states import TaskStatus


@dataclass
class SimState:
    problem: Problem  # узлы и исходный вход; задачи из submit_task добавляются в `tasks`
    tasks: dict[str, Task]
    status: dict[str, TaskStatus]
    rem: dict[str, int]  # остаток работы
    attempt: dict[str, int]  # номер последней начатой попытки (0 — не запускалась)
    eligible: dict[str, tuple[str, ...]]  # допустимые узлы; узлы и ёмкости не меняются, поэтому кешируется
    waiting_preds: dict[str, int]  # число незавершённых предшественников: READY возможен при 0
    successors: dict[str, list[str]]  # кому уменьшать счётчик при FINISH
    free: dict[str, Resources]
    up: dict[str, bool]
    run: dict[str, RunInfo] = field(default_factory=dict)
    ready_since: dict[str, int] = field(default_factory=dict)
    preempt_count: dict[str, int] = field(default_factory=dict)  # заполняется вытеснением (Э5)

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
        """Новая задача в статусе PENDING.

        Неизвестная зависимость или самоссылка тоже считаются незавершёнными, поэтому такая задача
        никогда не станет READY: вход должен пройти валидацию и карантин, движок только не зацикливается.
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
