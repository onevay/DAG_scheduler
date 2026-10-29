"""HTTP-эндпойнты агентов: hello, долгий опрос команд, события. Только с токеном агентов.

Токен — EXPSCHED_AGENT_TOKEN; не задан — сервер придумывает свой при запуске и отдаёт его только своим
локальным агентам: агенты на других машинах его не знают и без явного токена не подключатся.

Обработчики асинхронные: события агентов меняют движок прогона, а он живёт в потоке цикла событий.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse, Response

from expsched.api.errors import error_response
from expsched.api.local_agents import CheckFailed, LocalAgents
from expsched.api.models import AgentsOut, ErrorOut, LocalAgentIn, LocalAgentOut, LocalAgentsOut
from expsched.contract.agent import EventsAck, EventsBatch, Hello, HelloAck, Poll, PollReply
from expsched.emulate.agents import AgentRegistry, EventGap

router = APIRouter(prefix="/v1/agents/{agent_id}", tags=["agents"])
# Список — для клиента, без токена агентов и только на основном адресе: отдельный слушатель агентов его не
# показывает
listing = APIRouter(prefix="/v1/agents", tags=["agents"])
# Агенты на машине сервера, которых он запускает сам; как и список — только на основном адресе
local = APIRouter(prefix="/v1/local-agents", tags=["agents"])
AgentId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": ErrorOut} for status in (403, 404, 409, 422)}


@listing.get("", response_model=AgentsOut)
async def agents(request: Request) -> Any:
    """Агенты, представившиеся серверу: среда, ёмкость, на связи ли, какие попытки у них идут."""
    registry = _registry(request)
    running: dict[str, list[str]] = {}
    for run_id, item in request.app.state.emulations.live.items():
        if item.emulation.status != "running":
            continue
        for c in item.emulation.containers.values():
            if c.state not in ("exited", "oom", "killed", "lost", "rejected"):
                running.setdefault(c.agent, []).append(f"{run_id}:{c.task}:{c.attempt}")
    return {
        "agents": [
            {
                "id": agent_id,
                "runtime": hello.runtime,
                "version": hello.agent_version,
                "capacity": dict(hello.capacity),
                "max_containers": hello.max_containers,
                "alive": seen <= registry.heartbeat_s,
                "seen_s_ago": round(seen, 1),
                "running": sorted(running.get(agent_id, [])),
            }
            for agent_id, (hello, seen) in registry.known().items()
        ],
        "heartbeat_timeout_s": registry.heartbeat_s,
    }


@router.post("/hello", response_model=HelloAck, responses=ERRORS)
async def hello(request: Request, agent_id: AgentId, body: Hello) -> Any:
    return _denied(request) or _registry(request).hello(agent_id, body)


@router.post("/poll", response_model=PollReply, responses=ERRORS)
async def poll(request: Request, agent_id: AgentId, body: Poll) -> Any:
    """Отдаёт неподтверждённые команды сразу, а если их нет — ждёт до `wait_s` секунд."""
    if denied := _denied(request):
        return denied
    registry = _registry(request)
    registry.report(body)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(body.wait_s, registry.poll_s)
    while not (reply := registry.poll(agent_id, body)).commands and loop.time() < deadline:
        await asyncio.sleep(0.2)
    return reply


@router.post("/events", response_model=EventsAck, responses=ERRORS)
async def events(request: Request, agent_id: AgentId, body: EventsBatch) -> Any:
    if denied := _denied(request):
        return denied
    try:
        return _registry(request).events(agent_id, body)
    except EventGap as gap:
        return error_response(409, "EVENT_GAP", f"{gap}; resend from {gap.expected}")


def _registry(request: Request) -> AgentRegistry:
    registry: AgentRegistry = request.app.state.agents
    return registry


def agent_token(request: Request) -> str:
    """Токен агентов: заданный в окружении, иначе придуманный сервером при запуске."""
    token: str = os.environ.get("EXPSCHED_AGENT_TOKEN") or request.app.state.agent_token
    return token


def _denied(request: Request) -> JSONResponse | None:
    token = agent_token(request)
    # Байты, а не строки: compare_digest не сравнивает строки с не-ASCII, а заголовок может их нести
    sent = request.headers.get("authorization", "").encode("utf-8", "surrogateescape")
    if not hmac.compare_digest(sent, f"Bearer {token}".encode()):
        return error_response(403, "FORBIDDEN", "wrong agent token")
    return None


def _local(request: Request) -> LocalAgents:
    manager: LocalAgents = request.app.state.local_agents
    return manager


LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _remote(request: Request) -> JSONResponse | None:
    """Запуск и остановка процессов на машине сервера — только с неё же: сервер может слушать и сеть."""
    if (request.client.host if request.client else None) not in LOOPBACK:
        return error_response(403, "FORBIDDEN", "local agents are managed only from the server's machine")
    return None


@local.get("", response_model=LocalAgentsOut)
async def local_agents(request: Request) -> Any:
    """Агенты, которых запустил этот сервер: идут ли, код выхода, хвост журнала процесса."""
    return {"agents": _local(request).describe()}


@local.post("", status_code=201, response_model=LocalAgentOut, responses=ERRORS)
async def start_local_agent(request: Request, body: LocalAgentIn) -> Any:
    """Проверить среду (`agent check`) и запустить агента на машине сервера; он сам представится серверу."""
    if denied := _remote(request):
        return denied
    host, port = request.scope.get("server") or ("127.0.0.1", 8000)
    scheduler = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    capacity = body.model_dump(include={"cpu", "ram_mb", "gpu"})
    try:  # проверка ходит сюда же с hello — не держать цикл событий
        await asyncio.to_thread(
            _local(request).start, body.id, body.runtime, capacity, scheduler, agent_token(request)
        )
    except ValueError as error:
        return error_response(409, "AGENT_RUNNING", str(error))
    except CheckFailed as error:
        return error_response(422, "AGENT_NOT_READY", str(error))
    return next(a for a in _local(request).describe() if a["id"] == body.id)


@local.delete("/{agent_id}", status_code=204, response_model=None, responses=ERRORS)
async def stop_local_agent(request: Request, agent_id: AgentId) -> Any:
    """Остановить агента; его контейнеры остаются и подхватываются, если запустить его снова."""
    if denied := _remote(request):
        return denied
    if not await asyncio.to_thread(_local(request).stop, agent_id):
        return error_response(404, "NOT_FOUND", f"no local agent {agent_id}")
    return Response(status_code=204)
