"""Прогон на агентах: решения политики — команды агентам, события агентов — факты движка.

Часы модели — часы стены в масштабе `time_scale_ms` мс на единицу времени модели. Движок с внешним
исполнителем: окончание попытки приходит событием агента `exited`, сбой — `exited` с ненулевым кодом,
`oom`, `lost` или `rejected`, сохранённая работа — по событиям `checkpoint`. Агент, переставший выходить на
связь, — отказ всех его узлов; вернувшийся — их возврат. В режиме shadow рядом считается план — симуляция
того же входа, — и по каждой задаче видно, где факт разошёлся с планом и почему (`emulate/shadow.py`).
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from expsched.contract.agent import AgentEvent, Usage
from expsched.contract.io_models import ContainerSpec, PolicyIn, ProblemIn
from expsched.domain.models import Event, EvType, work_in
from expsched.domain.states import FINAL_STATUSES
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.shadow import compare
from expsched.engine.events import Crash, Finish, Reestimate
from expsched.engine.executor import External
from expsched.estimate import Estimate
from expsched.pipeline import RunResult, conclude, prepare

Status = Literal["running", "done", "cancelled"]
LOG_LINES = 500  # строк журнала контейнера, которые помнит сервер


@dataclass
class Container:
    """Попытка на агенте — то, что показывает экран «Контейнеры»."""

    task: str
    attempt: int
    worker: str
    agent: str
    resume_from: int
    state: str = "sent"  # sent, accepted, started, затем тип последнего события
    container_id: str | None = None
    progress: int = 0
    detail: str | None = None
    usage: dict[str, int] | None = None  # последний замер агента, пока контейнер идёт


class Emulation:
    def __init__(
        self,
        run_id: str,
        problem: ProblemIn,
        policy: PolicyIn,
        registry: AgentRegistry,
        clock: Callable[[], float] = time.monotonic,
        history: Mapping[str, Estimate] | None = None,  # оценки по прошлым прогонам, по ключу задачи
    ) -> None:
        missing = sorted(w.id for w in problem.cluster.workers if not w.agent)
        if missing:
            raise ValueError(f"workers without an agent: {', '.join(missing)}")
        _check_capacity(problem, registry)
        self.run_id = run_id
        self.registry = registry
        self.clock = clock
        self.scale_ms = problem.time_scale_ms
        self.agent_of = {w.id: str(w.agent) for w in problem.cluster.workers}
        tasks = [*problem.tasks, *(e.task for e in problem.events if e.task)]
        self.specs: dict[str, ContainerSpec | None] = {t.id: t.container for t in tasks}
        self.containers: dict[tuple[str, int], Container] = {}
        self.dropped: set[str] = set()  # узлы, уроненные вручную: не поднимаются, пока их не вернут
        self.dropped_at: list[tuple[str, int]] = []  # ручные отказы (узел, момент) — для причин shadow
        self.plan: RunResult | None = None
        self.result: RunResult | None = None
        self.status: Status = "running"
        self.started = clock()
        prepared = prepare(problem, policy, history)
        if isinstance(prepared, RunResult):  # строгая проверка не пропустила вход
            self.result, self.status = prepared, "done"
            return
        self.prepared = prepared
        self.executor = External()
        self.sim = prepared.simulator(self.executor)
        if problem.mode == "shadow":
            plan = prepared.simulator()
            plan.run_all()
            self.plan = conclude(prepared, plan)
        self.logs: dict[tuple[str, int], deque[str]] = {}  # журналы контейнеров: последние LOG_LINES строк
        registry.listeners[run_id] = self.on_event
        registry.telemetry[run_id] = self.on_telemetry

    def now(self) -> int:
        return round((self.clock() - self.started) * 1000) // self.scale_ms  # до мс: без хвостов float

    def tick(self) -> list[dict[str, Any]]:
        """Часы модели дошли до сейчас: отказы и возвраты агентов, эпохи наступивших событий, команды."""
        if self.status != "running":
            return []
        now = self.now()
        for worker, agent in sorted(self.agent_of.items()):
            alive = self.registry.alive(agent, since=self.started) and worker not in self.dropped
            if alive != self.sim.state.up[worker] and now >= self.sim.t:
                kind = EvType.WORKER_UP if alive else EvType.WORKER_DOWN
                self.sim.inject(now, Event(now, kind, worker=worker))
        self._overdue(now)
        records = self.sim.advance(now)
        for r in records:
            if r["type"] == "START":
                self._start(r)
            elif r["type"] == "INTERRUPTED" and r["cause"] in ("PREEMPT", "WORKER_DOWN"):
                self._stop(r["task"], r["attempt"], "preempt" if r["cause"] == "PREEMPT" else "worker_down")
        if self._finished():
            self.result, self.status = conclude(self.prepared, self.sim, exact=False), "done"
            if self.plan is not None:
                self.result.result["shadow"] = self.shadow()
            self.registry.listeners.pop(self.run_id, None)
            self.registry.telemetry.pop(self.run_id, None)
        return records

    def _finished(self) -> bool:
        """Ничего не идёт и не ждёт очереди; узлы лежат при незаконченных задачах — ждать: агент вернётся."""
        state = self.sim.state
        if state.run or self.sim.next_time() is not None:
            return False
        unfinished = any(s not in FINAL_STATUSES for s in state.status.values())
        return not (unfinished and not all(state.up.values()))

    def on_event(self, event: AgentEvent) -> None:
        c = self.containers.get((event.task, event.attempt))
        if c is None or self.status != "running":
            return
        c.state = event.type
        if event.type in ("exited", "oom", "killed", "lost", "rejected"):
            c.usage = None
        c.container_id = event.container_id or c.container_id
        c.detail = event.detail or c.detail
        if event.progress is not None:
            c.progress = event.progress
        now = max(self.now(), self.sim.t)
        if event.type == "checkpoint" and event.progress is not None:
            self.executor.checkpoint(event.task, event.attempt, event.progress - c.resume_from)
            if event.total is not None:
                self._learn(c, event.progress, event.total, now)
        elif event.type == "exited" and event.exit_code == 0:
            self.sim.inject(now, Finish(event.task, event.attempt))
        elif event.type in ("exited", "lost", "rejected"):
            code = f"exit code {event.exit_code}" if event.type == "exited" else None
            detail = "; ".join(d for d in (code, event.detail) if d) or event.type
            self.sim.inject(now, Crash(event.task, event.attempt, "FAILED", detail))
        elif event.type == "oom":
            detail = "; ".join(d for d in ("container exceeded its memory limit", event.detail) if d)
            self.sim.inject(now, Crash(event.task, event.attempt, "OOM", detail))

    def on_telemetry(self, task: str, attempt: int, usage: Usage | None, lines: list[str]) -> None:
        c = self.containers.get((task, attempt))
        if c is None:
            return
        if usage is not None and c.state not in ("exited", "oom", "killed", "lost", "rejected"):
            c.usage = usage.model_dump()
        if lines:
            self.logs.setdefault((task, attempt), deque(maxlen=LOG_LINES)).extend(lines)

    def _learn(self, c: Container, progress: int, total: int, now: int) -> None:
        """Задача сообщила всю свою работу: оценка смешивается с ней в лог-пространстве с весом доли
        сделанного — чем дальше задача, тем больше веры ей; неизвестное сужается в той же мере."""
        run = self.sim.state.run.get(c.task)
        if run is None or run.attempt != c.attempt or total <= progress:
            return
        believed = c.resume_from + (run.work or run.end_pred - run.start)
        share = progress / total
        blended = math.exp((1 - share) * math.log(believed) + share * math.log(total))
        work = max(progress + 1, round(blended)) - c.resume_from
        if work != believed - c.resume_from:
            spread = self.sim.state.tasks[c.task].spread * (1 - share)
            self.sim.inject(now, Reestimate(c.task, c.attempt, work, spread))

    def _overdue(self, now: int) -> None:
        """Попытка дошла до ожидаемого конца, а не закончилась: ждать её столько, сколько обещает условное
        ожидание при уже сделанной работе; у задачи с известной длительностью — ещё половину прошедшего."""
        if now < self.sim.t:
            return
        for key, run in sorted(self.sim.state.run.items()):
            c = self.containers.get((key, run.attempt))
            if c is None or now < run.end_pred or c.state not in ("started", "checkpoint"):
                continue
            work = run.work or run.end_pred - run.start
            done = c.resume_from + work_in(now - run.start, run.speed)
            spread = self.sim.state.tasks[key].spread
            if spread > 0:
                believed = c.resume_from + work
                expected = Estimate(believed * math.exp(-(spread**2) / 2), spread, "kind").residual(done)
                total = max(done + 1, math.ceil(expected - 1e-9))
            else:
                total = done + max(1, math.ceil((done - c.resume_from) / 2))
            self.sim.inject(now, Reestimate(key, run.attempt, total - c.resume_from, spread))

    def checkpoint(self, task: str) -> None:
        run = self.sim.state.run.get(task)
        if run is not None:
            ref = {"run_id": self.run_id, "task": task, "attempt": run.attempt}
            self.registry.send(self.agent_of[run.worker], {**ref, "kind": "checkpoint"})

    def drop(self, worker: str) -> None:
        """Уронить узел вручную: его попытки прерываются, контейнеры останавливаются."""
        self.dropped.add(worker)
        self.dropped_at.append((worker, max(self.now(), self.sim.t)))
        self.tick()

    def shadow(self) -> dict[str, Any] | None:
        """План рядом с фактом по задачам; None — прогон не в режиме shadow."""
        if self.plan is None:
            return None
        tasks = self.sim.state.tasks
        return compare(
            self.plan.result["tasks"],
            self.plan.events,
            self.sim.journal.records,
            {k: t.deps for k, t in tasks.items()},
            {k: t.duration for k, t in tasks.items()},
            self.dropped_at,
        )

    def restore(self, worker: str) -> None:
        """Вернуть уроненный вручную узел; поднимется, если его агент на связи."""
        self.dropped.discard(worker)
        self.tick()

    def cancel(self) -> None:
        for task, run in sorted(self.sim.state.run.items()):
            self._stop(task, run.attempt, "cancel")
        self.status = "cancelled"
        self.registry.listeners.pop(self.run_id, None)
        self.registry.telemetry.pop(self.run_id, None)

    def _start(self, r: dict[str, Any]) -> None:
        task, run = self.sim.state.tasks[r["task"]], self.sim.state.run[r["task"]]
        agent = self.agent_of[r["worker"]]
        self.containers[(r["task"], r["attempt"])] = Container(
            r["task"], r["attempt"], r["worker"], agent, r["resume_from"]
        )
        spec = self.specs.get(r["task"])
        self.registry.send(
            agent,
            {
                "run_id": self.run_id,
                "task": r["task"],
                "attempt": r["attempt"],
                "kind": "start",
                "worker": r["worker"],
                "container": spec.model_dump() if spec else None,
                "limits": dict(task.resources.items()),
                "work": run.work or run.end_pred - run.start,  # работа, не время: узлы бывают быстрее
                "resume_from": r["resume_from"],
                "checkpoint_interval": task.ckpt,
                "time_scale_ms": self.scale_ms,
            },
        )

    def _stop(self, task: str, attempt: int, reason: str) -> None:
        c = self.containers.get((task, attempt))
        if c is not None:
            ref = {"run_id": self.run_id, "task": task, "attempt": attempt}
            self.registry.send(c.agent, {**ref, "kind": "stop", "reason": reason})


def _check_capacity(problem: ProblemIn, registry: AgentRegistry) -> None:
    """Узлы агента вместе не больше того, что он отдаёт под задачи. Агент, которого сервер ещё не видел, —
    без проверки: прогон подождёт его, а не знающий ёмкости сервер не отказывает."""
    need: dict[str, dict[str, int]] = {}
    for w in problem.cluster.workers:
        total = need.setdefault(str(w.agent), {})
        for r, v in w.resources.items():
            total[r] = total.get(r, 0) + v
    known = registry.known()
    for agent, total in sorted(need.items()):
        if agent not in known:
            continue
        capacity = known[agent][0].capacity
        short = [
            f"{r} {v} > {capacity.get(r, 0)}" for r, v in sorted(total.items()) if v > capacity.get(r, 0)
        ]
        if short:
            raise ValueError(
                f"workers of agent {agent} need more than it offers: {', '.join(short)}; "
                "give the agent more (--cpu, --ram-mb, --gpu) or split the workers between agents"
            )
