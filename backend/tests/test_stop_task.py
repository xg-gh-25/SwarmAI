"""Stopping ONE sub-agent (chat turn spec v6 §7.3).

What is tested:
- the live task registry (``SessionUnit._live_task_ids``) follows the SDK task
  lifecycle through the real orchestrator loop: an id joins on a running status and
  leaves on any terminal one (completed / failed / stopped / killed);
- ``SessionUnit.stop_task`` calls ``client.stop_task`` only for an id in that registry
  while the turn is active, refuses an unknown id, a finished id and an id from an
  earlier turn without calling the SDK, never stops the turn (no user-stop flag, no
  stop event), and turns a hanging / failing SDK call into a generic refusal;
- the registry is cleared at every turn boundary (send, interrupt, teardown);
- ``SessionRouter.stop_task`` and ``POST /api/chat/stop/{session_id}/task/{task_id}``
  delegate, validate the id shape and report a generic status.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    AssistantMessage, SystemMessage, TaskNotificationMessage, TaskProgressMessage,
    TaskStartedMessage, TaskUpdatedMessage, ToolUseBlock,
)

from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator

AGENT = "tu-agent"


class _Script:
    def __init__(self, msgs):
        self._m = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._m:
            return self._m.pop(0)
        raise RuntimeError("script exhausted")


def _task(cls, **kw):
    base = {"subtype": cls.__name__, "data": {}}
    if cls is not TaskUpdatedMessage:
        base.update(uuid="u", session_id="s")
    return cls(**base, **kw)


def _unit(state=SessionState.STREAMING) -> SessionUnit:
    unit = SessionUnit(session_id="stop-task", agent_id="default")
    unit.state = state
    unit._streaming_start_time = unit._last_event_time = time.time()
    return unit


async def _stream(unit: SessionUnit, msgs) -> list[set]:
    """Run the orchestrator; return the registry after every yielded event."""
    from core import permission_manager as _pm

    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(msgs)
    seen: list[set] = []
    with patch.object(_pm.permission_manager, "get_session_queue", return_value=asyncio.Queue()):
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                if ev.get("type") == "subagent_progress":
                    seen.append(set(unit._live_task_ids))
    return seen


def _spawn():
    return [SystemMessage(subtype="init", data={"session_id": "s"}),
            AssistantMessage(content=[ToolUseBlock(id=AGENT, name="Agent", input={"description": "d", "prompt": "p"})], model="m")]


@pytest.mark.asyncio
@pytest.mark.parametrize("end", [
    lambda: _task(TaskNotificationMessage, task_id="t1", status="completed", output_file="", summary="ok", tool_use_id=AGENT),
    lambda: _task(TaskNotificationMessage, task_id="t1", status="failed", output_file="", summary="x", tool_use_id=AGENT),
    lambda: _task(TaskUpdatedMessage, task_id="t1", patch={"status": "killed"}),
])
async def test_registry_follows_task_lifecycle(end):
    unit = _unit()
    seen = await _stream(unit, _spawn() + [
        _task(TaskStartedMessage, task_id="t1", description="d", tool_use_id=AGENT),
        _task(TaskProgressMessage, task_id="t1", description="d", tool_use_id=AGENT,
              usage={"total_tokens": 1, "tool_uses": 1, "duration_ms": 10}, last_tool_name="Read"),
        end(),
    ])
    assert seen == [{"t1"}, {"t1"}, set()]


@pytest.mark.asyncio
async def test_background_task_without_parent_is_not_stoppable():
    unit = _unit()
    await _stream(unit, _spawn() + [_task(TaskStartedMessage, task_id="bg", description="b", tool_use_id=None)])
    assert "bg" not in unit._live_task_ids


def _client():
    c = MagicMock()
    c.stop_task = AsyncMock(return_value=None)
    return c


@pytest.mark.asyncio
async def test_stop_task_calls_sdk_for_a_live_id_and_keeps_the_turn():
    unit = _unit()
    unit._client = _client()
    unit._live_task_ids = {"t1"}
    out = await unit.stop_task("t1")
    assert out["stopped"] is True
    unit._client.stop_task.assert_awaited_once_with("t1")
    assert unit._user_stopped_current_turn is False
    assert not unit._stop_event.is_set()
    assert unit.state == SessionState.STREAMING


@pytest.mark.asyncio
async def test_stop_task_refuses_unknown_and_finished_ids():
    unit = _unit()
    unit._client = _client()
    unit._live_task_ids = {"t1"}
    assert (await unit.stop_task("nope"))["stopped"] is False
    unit._live_task_ids.discard("t1")  # the task finished on its own
    assert (await unit.stop_task("t1"))["stopped"] is False
    unit._client.stop_task.assert_not_called()


@pytest.mark.asyncio
async def test_stop_task_refuses_when_turn_not_active_or_no_client():
    unit = _unit(SessionState.IDLE)
    unit._client = _client()
    unit._live_task_ids = {"t1"}
    assert (await unit.stop_task("t1"))["stopped"] is False
    unit2 = _unit()
    unit2._client = None
    unit2._live_task_ids = {"t1"}
    assert (await unit2.stop_task("t1"))["stopped"] is False
    unit._client.stop_task.assert_not_called()


@pytest.mark.asyncio
async def test_stop_task_sdk_error_or_hang_is_a_generic_refusal():
    unit = _unit()
    unit._client = MagicMock()
    unit._client.stop_task = AsyncMock(side_effect=RuntimeError("/secret/internal path"))
    unit._live_task_ids = {"t1"}
    out = await unit.stop_task("t1")
    assert out["stopped"] is False and "/secret" not in out["reason"]

    async def _hang(_):
        await asyncio.sleep(30)

    unit._client.stop_task = _hang
    with patch("core.session_unit.STOP_TASK_TIMEOUT_S", 0.05):
        out = await unit.stop_task("t1")
    assert out["stopped"] is False


def test_registry_cleared_at_teardown_and_interrupt_and_send():
    unit = _unit()
    unit._live_task_ids = {"t1"}
    unit._cleanup_internal()
    assert unit._live_task_ids == set()
    # an id from an earlier turn: send()'s Layer 0 and interrupt() clear it
    assert "self._live_task_ids = set()" in inspect.getsource(SessionUnit.send)
    assert "self._live_task_ids = set()" in inspect.getsource(SessionUnit.interrupt)


@pytest.mark.asyncio
async def test_interrupt_clears_registry_so_a_late_stop_is_refused():
    unit = _unit()
    unit._client = None
    unit._live_task_ids = {"t1"}
    await unit.interrupt()
    assert (await unit.stop_task("t1"))["stopped"] is False


@pytest.mark.asyncio
async def test_router_unknown_session():
    from core.session_router import SessionRouter

    router = SessionRouter.__new__(SessionRouter)
    router.get_unit = lambda _sid: None
    out = await SessionRouter.stop_task(router, "missing", "t1")
    assert out == {"status": "not_found"}


@pytest.mark.asyncio
async def test_router_delegates():
    from core.session_router import SessionRouter

    unit = MagicMock()
    unit.stop_task = AsyncMock(return_value={"stopped": True, "reason": ""})
    router = SessionRouter.__new__(SessionRouter)
    router.get_unit = lambda _sid: unit
    assert await SessionRouter.stop_task(router, "s", "t1") == {"status": "stopped"}
    unit.stop_task = AsyncMock(return_value={"stopped": False, "reason": "not running"})
    assert await SessionRouter.stop_task(router, "s", "t1") == {"status": "refused", "message": "not running"}


@pytest.mark.asyncio
async def test_route_validates_and_delegates():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import chat as chat_router

    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api/chat")
    fake = MagicMock()
    fake.stop_task = AsyncMock(return_value={"status": "stopped"})
    with patch.object(chat_router, "_get_router", return_value=fake):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            ok = await c.post("/api/chat/stop/sess-1/task/task_ab-9")
            bad = await c.post("/api/chat/stop/sess-1/task/" + "x" * 200)
            bad2 = await c.post("/api/chat/stop/sess-1/task/a%20b")
    assert ok.status_code == 200 and ok.json() == {"status": "stopped"}
    fake.stop_task.assert_awaited_once_with("sess-1", "task_ab-9")
    assert bad.status_code == 422 and bad2.status_code == 422
