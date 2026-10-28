"""Детерминированная дискретно-событийная симуляция.

Эпоха: все события ближайшего момента в порядке очереди, затем одно решение политики, затем применение
решений с проверкой (guard). Всё, что происходит, записывается в журнал. Решать имеет смысл только
в моменты событий: старт задачи возможен лишь тогда, когда что-то освободилось или стало готовым.

У каждой нестартовавшей задачи есть причина ожидания (WAIT_*). Запись WAIT появляется, когда причина
меняется, и действует до следующей записи WAIT этой задачи или до её старта.

Решения эпохи: PREEMPT применяются первыми, в каком бы порядке их ни вернула политика, затем START и RESERVE
по порядку. Вытеснение прерывает попытку так же, как отказ узла (работа до чекпоинта сохраняется), но идёт
в счётчик вытеснений, а не в max_retries. RESERVE — обещание начать задачу на узле не позже t_res: ресурсы
резерв не занимает, политика пересчитывает его каждую эпоху, а запись пишется, когда резерв меняется.

Когда попытка закончится и сколько работы сохранит при прерывании, решает исполнитель (`executor`): в
симуляции это известно заранее, при прогоне на агентах окончания и сбои подаются извне — `inject` в момент
часов модели, а `advance` проводит эпохи всех событий, чей момент наступил.

С `forecast=True` на каждой эпохе с внешними событиями (подача задачи, отказ или возврат узла) пишется REPLAN:
как изменились прогнозные старты задач по сравнению с прогнозом, где этих событий не было. Прогноз — прогон
копии симулятора до конца той же политикой без будущих внешних событий; он стоит как весь остаток прогона.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from expsched.domain.models import Event, EvType, Problem, Task, time_for, work_in
from expsched.domain.resources import Resources
from expsched.domain.snapshot import Decision, Policy, RunInfo
from expsched.domain.states import FINAL_STATUSES, TaskStatus
from expsched.engine.errors import InvalidDecision
from expsched.engine.events import Crash, EventQueue, Finish, Payload, Reestimate, Release
from expsched.engine.executor import Executor, Simulated
from expsched.engine.journal import Journal
from expsched.engine.snapshot import build_snapshot
from expsched.engine.state import SimState
from expsched.engine.waits import ready_wait
from expsched.validation.diagnostics import Code, Diagnostic
from expsched.validation.graph import validate_graph
from expsched.validation.resources import validate_resources

# Задачи с таким статусом не будут выполнены: их потомки блокируются.
UNREACHABLE = frozenset({TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.INFEASIBLE})


class Simulator:
    """Симуляция одного экземпляра. Вход должен пройти validate и quarantine.

    Если в графе всё же есть цикл или неизвестная зависимость, такие задачи не стартуют и в конце
    получают диагностику STUCK.
    """

    def __init__(
        self, problem: Problem, policy: Policy, forecast: bool = False, executor: Executor | None = None
    ) -> None:
        self.policy = policy
        self.executor = executor or Simulated()
        self.replans = forecast
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
        external = [p for p in batch if isinstance(p, Event)]
        before = self.forecast(pending=batch) if self.replans and external else None
        epoch = len(self.journal)
        for payload in batch:
            self._handle(payload)
        self._decide()
        if before is not None:
            self._replan(before, external, epoch)
        first, self._reported = self._reported, len(self.journal)
        return list(self.journal.records[first:])

    def inject(self, t: int, payload: Payload) -> None:
        """Факт извне (окончание, сбой, отказ узла, подача задачи) в момент `t` часов модели."""
        if t < self.t:
            raise ValueError(f"fact at {t} is earlier than the model clock {self.t}")
        self.queue.push(t, payload)

    def next_time(self) -> int | None:
        """Момент ближайшего события в очереди; None — событий нет."""
        return self.queue.peek()

    def advance(self, until: int) -> list[dict[str, Any]]:
        """Часы модели дошли до `until`: эпохи всех событий не позже него; новые записи журнала."""
        out: list[dict[str, Any]] = []
        while (t := self.queue.peek()) is not None and t <= until:
            out += self.step() or []
        return out

    def run_all(self) -> list[dict[str, Any]]:
        while self.step() is not None:
            pass
        self._finalize()
        return list(self.journal.records)

    def forecast(self, pending: Iterable[Payload] = ()) -> dict[str, int]:
        """Прогнозные старты задач: копия прогоняется до конца без внешних событий, состояние не меняется.

        `pending` — события текущего момента, уже извлечённые из очереди, но ещё не обработанные; внешние
        из них отбрасываются. В ответе — первый старт каждой задачи, стартующей в прогнозе.
        """
        sim = copy.deepcopy(self, {id(self.journal): Journal()})  # журнал прогнозу не нужен
        sim.replans = False
        sim.queue.drop_external()
        if not isinstance(self.executor, Simulated):  # окончания идущих попыток — по прогнозу
            sim.executor = Simulated()
            for key, run in list(
                sim.state.run.items()
            ):  # в порядке стартов, как их окончания встали бы в очередь
                sim.queue.push(max(run.end_pred, sim.t), Finish(key, run.attempt))
        for payload in pending:
            if not isinstance(payload, Event):
                sim.queue.push(self.t, payload)
        while sim.step() is not None:
            pass
        starts: dict[str, int] = {}
        for r in sim.journal.records:
            if r["type"] == "START":
                starts.setdefault(r["task"], r["t"])
        return starts

    def _replan(self, before: dict[str, int], external: list[Event], epoch: int) -> None:
        """Запись REPLAN: чьи прогнозные старты сдвинулись из-за внешних событий эпохи.

        new_tasks — задачи, которых не было в прежнем прогнозе (поданные и прерванные отказом), dropped —
        задачи, которые были в прогнозе, но теперь не стартуют.
        """
        after = self.forecast()
        for r in self.journal.records[epoch:]:
            if r["type"] == "START":
                after[r["task"]] = r["t"]  # стартовавшие в эту эпоху уже не в прогнозе, а в факте
        changes = [
            {"task": k, "old_start": before[k], "new_start": after[k], "delta": after[k] - before[k]}
            for k in sorted(before.keys() & after.keys())
            if before[k] != after[k]
        ]
        self.journal.append(
            "REPLAN",
            self.t,
            trigger=external[0].type.value,
            events=[_describe(e) for e in external],
            changes=changes,
            new_tasks={k: after[k] for k in sorted(after.keys() - before.keys())},
            dropped=sorted(before.keys() - after.keys()),
        )

    # ---- события ----

    def _handle(self, payload: Payload) -> None:
        if isinstance(payload, Finish):
            self._finish(payload)
        elif isinstance(payload, Crash):
            run = self.state.run.get(payload.task)
            if run is not None and run.attempt == payload.attempt:
                reason = {"detail": payload.detail} if payload.detail else None
                self._interrupt(payload.task, cause=payload.cause, reason=reason)
        elif isinstance(payload, Reestimate):
            self._reestimate(payload)
        elif isinstance(payload, Release):
            self._try_ready(payload.task)
        else:
            self._external(payload)

    def _external(self, event: Event) -> None:
        if event.type is EvType.SUBMIT:
            if event.task is None:
                raise ValueError("submit_task event without a task")
            self._submit(event.task)
        elif event.worker not in self.state.up:
            raise ValueError(f"{event.type.value} event for unknown worker {event.worker!r}")
        elif event.type is EvType.WORKER_DOWN:
            self._worker_down(event.worker)
        else:
            self._worker_up(event.worker)

    def _worker_down(self, worker: str) -> None:
        """Узел перестаёт работать: его попытки прерываются, ресурсов у него больше нет."""
        s = self.state
        if not s.up[worker]:
            return  # узел уже недоступен
        s.up[worker] = False
        self.journal.append("WORKER_DOWN", self.t, worker=worker)
        for key in sorted(k for k, run in s.run.items() if run.worker == worker):
            self._interrupt(key, cause="WORKER_DOWN")
        s.free[worker] = Resources()

    def _worker_up(self, worker: str) -> None:
        """Узел вернулся пустым: все его ресурсы свободны."""
        s = self.state
        if s.up[worker]:
            return
        s.up[worker] = True
        s.free[worker] = s.problem.workers[worker].capacity
        self.journal.append("WORKER_UP", self.t, worker=worker)

    def _interrupt(self, key: str, cause: str, reason: dict[str, Any] | None = None) -> None:
        """Прерывает попытку: работа до последнего чекпоинта сохраняется, остальное теряется.

        Вытеснение (`cause="PREEMPT"`, `reason` — причина от политики) не расходует ретраи; отказ узла и сбой
        попытки (FAILED, OOM) — расходуют.
        """
        s = self.state
        task, run = s.tasks[key], s.run.pop(key)
        done = min(work_in(self.t - run.start, run.speed), run.work or self.t - run.start)
        saved = self.executor.saved(task, key, run, self.t)
        s.rem[key] -= saved
        s.free[run.worker] = s.free[run.worker] + task.resources
        s.analysis = None  # остатки работы изменились
        self.journal.append(
            "INTERRUPTED",
            self.t,
            task=key,
            attempt=run.attempt,
            worker=run.worker,
            cause=cause,
            saved=saved,
            lost=max(0, done - saved),
            remaining=s.rem[key],
            **({"reason": dict(reason)} if reason is not None else {}),
        )
        if cause == "PREEMPT":
            s.preempt_count[key] = s.preempt_count.get(key, 0) + 1
        else:
            s.failures[key] = s.failures.get(key, 0) + 1
        if s.failures.get(key, 0) > task.max_retries:
            s.status[key] = TaskStatus.FAILED
            self._block_descendants(key)
            return
        s.status[key] = TaskStatus.PENDING
        self._try_ready(key)

    def _block_descendants(self, key: str) -> None:
        """Все незавершённые потомки задачи, которая не будет выполнена, получают BLOCKED."""
        s = self.state
        stack = list(s.successors.get(key, []))
        while stack:
            node = stack.pop()
            if s.status[node] not in FINAL_STATUSES:
                s.status[node] = TaskStatus.BLOCKED
                s.wait_code.pop(node, None)
                self.journal.append("BLOCKED", self.t, task=node, by=key)
                stack.extend(s.successors.get(node, []))

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
        lost = sorted(d for d in set(task.deps) if s.status[d] in UNREACHABLE)
        if lost:
            s.status[task.id] = TaskStatus.BLOCKED
            self.journal.append("BLOCKED", self.t, task=task.id, by=lost[0])
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
        # Снимок смотрит на живое состояние, поэтому решения собираются целиком до их применения.
        decisions = list(self.policy.decide(build_snapshot(s, self.t)))
        for decision in decisions:
            if decision.action == "PREEMPT":
                self._preempt(decision)
        # Освобождённое вытеснением считается местом, которое было до решений: если его забрали другие,
        # причина ожидания — WAIT_PRIORITY.
        free_before = dict(s.free)
        started: list[str] = []
        reserved: dict[str, tuple[str, int]] = {}
        for decision in decisions:
            if decision.action == "START":
                self._start(decision)
                started.append(decision.task)
            elif decision.action == "RESERVE":
                reserved[decision.task] = self._reserve(decision)
            elif decision.action != "PREEMPT":
                raise InvalidDecision(decision, "unknown action")
        for key, (worker, t_res) in reserved.items():
            if s.reserved.get(key) != (worker, t_res):
                self.journal.append("RESERVE", self.t, task=key, worker=worker, t_res=t_res)
        s.reserved = reserved
        # Причина зависит только от требований задачи, её допустимых узлов и того, есть ли резерв у неё
        # самой: у задач одной формы она общая.
        by_shape: dict[tuple[Resources, tuple[str, ...], bool], tuple[str, dict[str, Any]]] = {}
        for key in sorted(s.ready):
            shape = (s.tasks[key].resources, s.eligible[key], key in reserved)
            if shape not in by_shape:
                by_shape[shape] = ready_wait(s, key, free_before, started, reserved)
            self._set_wait(key, *by_shape[shape])

    def _check_target(self, decision: Decision) -> None:
        """Задача готова, узел работает и подходит ей."""
        s = self.state
        if s.status.get(decision.task) is not TaskStatus.READY:
            raise InvalidDecision(decision, "task is not READY")
        if not s.up.get(decision.worker, False):
            raise InvalidDecision(decision, "worker is unknown or down")
        if decision.worker not in s.eligible[decision.task]:
            raise InvalidDecision(decision, "worker is not eligible for the task")

    def _preempt(self, decision: Decision) -> None:
        s = self.state
        run = s.run.get(decision.task)
        if run is None:
            raise InvalidDecision(decision, "task is not RUNNING")
        if run.worker != decision.worker:
            raise InvalidDecision(decision, f"task runs on {run.worker}")
        if not s.tasks[decision.task].preemptible:
            raise InvalidDecision(decision, "task is not preemptible")
        self._interrupt(decision.task, cause="PREEMPT", reason=decision.reason)

    def _reserve(self, decision: Decision) -> tuple[str, int]:
        self._check_target(decision)
        t_res = decision.reason.get("t_res")
        if not isinstance(t_res, int) or t_res < self.t:
            raise InvalidDecision(decision, "reservation needs an integer t_res not earlier than now")
        return decision.worker, t_res

    def _start(self, decision: Decision) -> None:
        self._check_target(decision)
        s = self.state
        key, worker = decision.task, decision.worker
        task = s.tasks[key]
        if not task.resources.fits(s.free[worker]):
            raise InvalidDecision(decision, "not enough free resources")

        s.free[worker] = s.free[worker] - task.resources
        s.status[key] = TaskStatus.RUNNING
        s.ready.discard(key)
        s.wait_code.pop(key, None)
        s.reserved.pop(key, None)
        s.attempt[key] += 1
        speed = s.problem.workers[worker].speed
        end = self.t + time_for(s.rem[key], speed)
        s.run[key] = RunInfo(worker, self.t, s.attempt[key], end, s.rem[key], speed)
        finish = self.executor.finish_at(task, s.run[key])
        if finish is not None:
            self.queue.push(finish, Finish(key, s.attempt[key]))
        self.journal.append(
            "START",
            self.t,
            task=key,
            worker=worker,
            attempt=s.attempt[key],
            resume_from=task.duration - s.rem[key],
            reason=dict(decision.reason),
        )

    def _reestimate(self, fact: Reestimate) -> None:
        """Попытка идёт иначе, чем ждали: её работа — `fact.work` от её начала. Прогноз окончания, остаток и
        длительность задачи (из неё считается позиция следующей попытки) меняются согласованно."""
        s = self.state
        run = s.run.get(fact.task)
        if run is None or run.attempt != fact.attempt:
            return  # попытка уже закончилась или прервана
        task = s.tasks[fact.task]
        old = run.work or run.end_pred - run.start
        s.tasks[fact.task] = replace(task, duration=task.duration + fact.work - old, spread=fact.spread)
        s.rem[fact.task] = fact.work
        s.run[fact.task] = replace(run, end_pred=run.start + time_for(fact.work, run.speed), work=fact.work)
        s.analysis = None
        self.journal.append(
            "ESTIMATE",
            self.t,
            task=fact.task,
            attempt=fact.attempt,
            work=fact.work,
            spread=round(fact.spread, 3),
            end=s.run[fact.task].end_pred,
        )

    def _finalize(self) -> None:
        """Диагностика для каждой задачи, не дошедшей до финального статуса.

        E050 — задача готова, но подходящие ей узлы так и не вернулись; STUCK — задача не стартовала по другой
        причине (например, ждёт зависимость, которая никогда не завершится).
        """
        if self._finalized:
            return
        self._finalized = True
        s = self.state
        for key in sorted(s.status):
            if s.status[key] in FINAL_STATUSES:
                continue
            if s.status[key] is TaskStatus.READY and not any(s.up[w] for w in s.eligible[key]):
                diagnostic = Diagnostic(
                    Code.E050,
                    {"task": key, "eligible": list(s.eligible[key])},
                    "Все подходящие узлы недоступны до конца прогона: добавьте узел или событие его возврата",
                )
                self.journal.append("DIAGNOSTIC", self.t, task=key, **diagnostic.to_dict())
            else:
                self.journal.append("DIAGNOSTIC", self.t, code="STUCK", task=key)


def _describe(event: Event) -> dict[str, Any]:
    if event.task is not None:
        return {"type": event.type.value, "task": event.task.id}
    return {"type": event.type.value, "worker": event.worker}
