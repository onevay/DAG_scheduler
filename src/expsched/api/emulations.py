"""Прогоны на агентах в API: запуск в фоне, состояние контейнеров, действия, сохранение по окончании.

Прогон на агентах неповторим (это настоящие контейнеры), поэтому его id случайный, а не из хеша входа.
Пока он идёт, журнал и вход отдаются из памяти, результат — 409 RUN_IN_PROGRESS; законченный сохраняется
в хранилище как обычный прогон. Одновременно идёт не больше EXPSCHED_EMULATIONS прогонов, каждый — не
дольше EXPSCHED_EMULATION_MAX_S секунд, при остановке сервера идущие отменяются.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import TypeAdapter, ValidationError

from expsched.api.errors import error_response
from expsched.api.models import Action, CancelAction, CheckpointAction, ErrorOut, LiveOut, LogsOut
from expsched.api.store import RUN_ID_PATTERN, RunStore
from expsched.contract.io_models import PolicyIn, ProblemIn
from expsched.emulate.agents import AgentRegistry
from expsched.emulate.driver import Emulation

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/runs/{run_id}", tags=["emulation"])
RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]
ACTION: TypeAdapter[Action] = TypeAdapter(Action)
ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": ErrorOut} for status in (404, 422)}


@dataclass
class Live:
    emulation: Emulation
    problem: dict[str, Any]  # вход с политикой, как его сохранит хранилище
    task: asyncio.Task[None] | None = None


class Emulations:
    def __init__(self, registry: AgentRegistry, store: RunStore, tick_s: float = 0.1) -> None:
        self.registry = registry
        self.store = store
        self.tick_s = tick_s
        self.limit = int(os.environ.get("EXPSCHED_EMULATIONS", "1"))
        self.max_s = float(os.environ.get("EXPSCHED_EMULATION_MAX_S", "3600"))
        self.live: dict[str, Live] = {}

    def running(self) -> int:
        return sum(1 for item in self.live.values() if item.emulation.status == "running")

    def start(self, problem: ProblemIn, policy: PolicyIn) -> str:
        """Новый прогон на агентах; ValueError — вход для него не годится (узел без агента)."""
        run_id = secrets.token_hex(8)
        emulation = Emulation(run_id, problem, policy, self.registry)
        stored = problem.model_copy(update={"policy": policy}).model_dump(mode="json")
        item = Live(emulation, stored)
        self.live[run_id] = item
        item.task = asyncio.create_task(self._drive(run_id))
        return run_id

    async def _drive(self, run_id: str) -> None:
        item = self.live[run_id]
        emulation, started = item.emulation, time.monotonic()
        try:
            while emulation.status == "running":
                emulation.tick()
                if time.monotonic() - started > self.max_s:
                    log.warning("run %s exceeded EXPSCHED_EMULATION_MAX_S and is cancelled", run_id)
                    emulation.cancel()
                await asyncio.sleep(self.tick_s)
            if emulation.status == "done" and emulation.result is not None:
                await asyncio.to_thread(self.store.save, run_id, item.problem, emulation.result)
        except Exception:
            log.exception("run %s on agents failed", run_id)
            emulation.cancel()

    def close(self) -> None:
        for item in self.live.values():
            if item.emulation.status == "running":
                item.emulation.cancel()
            if item.task is not None:
                item.task.cancel()


def view(run_id: str, emulation: Emulation) -> dict[str, Any]:
    up = emulation.sim.state.up if hasattr(emulation, "sim") else {}
    return {
        "run_id": run_id,
        "status": emulation.status,
        "now": emulation.now(),
        "time_scale_ms": emulation.scale_ms,
        "workers": {
            w: {"agent": a, "up": up.get(w, False), "dropped": w in emulation.dropped}
            for w, a in sorted(emulation.agent_of.items())
        },
        "containers": [asdict(c) for _, c in sorted(emulation.containers.items())],
        "shadow": emulation.shadow() if hasattr(emulation, "sim") else None,
    }


def emulations(request: Request) -> Emulations:
    manager: Emulations = request.app.state.emulations
    return manager


@router.get("/containers", response_model=LiveOut, responses=ERRORS)
async def containers(request: Request, run_id: RunId) -> Any:
    """Прогон на агентах сейчас: часы модели, узлы и контейнеры попыток."""
    item = emulations(request).live.get(run_id)
    if item is None:
        return error_response(404, "NOT_FOUND", f"run {run_id} is not running on agents in this server")
    return view(run_id, item.emulation)


@router.get("/containers/{task}/{attempt}/logs", response_model=LogsOut, responses=ERRORS)
async def container_logs(request: Request, run_id: RunId, task: str, attempt: int) -> Any:
    """Журнал контейнера попытки — строки, что агент успел прислать; у сред без журнала он пуст."""
    item = emulations(request).live.get(run_id)
    if item is None or (task, attempt) not in item.emulation.containers:
        return error_response(
            404, "NOT_FOUND", f"run {run_id} has no attempt {attempt} of {task!r} on agents"
        )
    return {"lines": list(item.emulation.logs.get((task, attempt), ()))}


@router.post("/actions", response_model=LiveOut, responses=ERRORS)
async def actions(request: Request, run_id: RunId, body: dict[str, Any]) -> Any:
    """Чекпоинт задачи сейчас, уронить или вернуть узел, отменить прогон."""
    item = emulations(request).live.get(run_id)
    if item is None or item.emulation.status != "running":
        return error_response(404, "NOT_FOUND", f"run {run_id} is not running on agents")
    try:
        action = ACTION.validate_python(body)
    except ValidationError as error:
        return error_response(422, "INVALID_REQUEST", str(error).splitlines()[0])
    emulation = item.emulation
    if isinstance(action, CheckpointAction):
        emulation.checkpoint(action.task)
    elif isinstance(action, CancelAction):
        emulation.cancel()
    elif action.worker not in emulation.agent_of:
        return error_response(404, "NOT_FOUND", f"run {run_id} has no worker {action.worker!r}")
    elif action.kind == "worker_down":
        emulation.drop(action.worker)
    else:
        emulation.restore(action.worker)
    return view(run_id, emulation)
