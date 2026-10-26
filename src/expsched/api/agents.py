"""HTTP-эндпойнты агентов: hello, долгий опрос команд, события. Только с токеном EXPSCHED_AGENT_TOKEN.

Обработчики асинхронные: события агентов меняют движок прогона, а он живёт в потоке цикла событий.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse

from expsched.api.errors import error_response
from expsched.api.models import ErrorOut
from expsched.contract.agent import EventsAck, EventsBatch, Hello, HelloAck, Poll, PollReply
from expsched.emulate.agents import AgentRegistry, EventGap

router = APIRouter(prefix="/v1/agents/{agent_id}", tags=["agents"])
AgentId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": ErrorOut} for status in (403, 409, 422)}


@router.post("/hello", response_model=HelloAck, responses=ERRORS)
async def hello(request: Request, agent_id: AgentId, body: Hello) -> Any:
    return _denied(request) or _registry(request).hello(agent_id, body)


@router.post("/poll", response_model=PollReply, responses=ERRORS)
async def poll(request: Request, agent_id: AgentId, body: Poll) -> Any:
    """Отдаёт неподтверждённые команды сразу, а если их нет — ждёт до `wait_s` секунд."""
    if denied := _denied(request):
        return denied
    registry = _registry(request)
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


def _denied(request: Request) -> JSONResponse | None:
    token = os.environ.get("EXPSCHED_AGENT_TOKEN")
    if not token:
        return error_response(403, "FORBIDDEN", "the agent API is off: set EXPSCHED_AGENT_TOKEN")
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
        return error_response(403, "FORBIDDEN", "wrong agent token")
    return None
