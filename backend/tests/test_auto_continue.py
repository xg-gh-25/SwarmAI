"""Auto-continue after an SSE disconnect (core/auto_continue, 2026-10-21 97df0539).

A 322 s daemon freeze made the frontend drop its SSE stream; disconnect recovery
flushed the CLI and the turn stopped with "the last call did not finish". Only a
user Stop may stop a turn now.

Tested here:
- a disconnect schedules one continuation: after the grace it persists a
  ``restarted/disconnected`` notice and sends ``PROMPT`` with ``_auto_continue``;
- a user Stop (even one landing after the abort), a newer message, a released
  unit, or a waiting question cancels it;
- the budget: ``LIMIT`` continuations per user turn, then a ``gave_up`` notice
  and no send; a disconnect of a NEW user turn resets it;
- chat.py wiring: disconnect recovery schedules, /chat/stop marks the stop;
- ``run_conversation(_auto_continue=True)`` writes no user row.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import auto_continue
from core.session_unit import SessionState


class _Unit:
    def __init__(self, sid="ac-1", state=SessionState.IDLE):
        self.session_id = sid
        self.agent_id = "agent-1"
        self.state = state
        self._send_generation = 5
        self.has_outstanding_tool_use = False
        self._pipe_flush_task = None


class _Router:
    def __init__(self, unit):
        self.unit = unit
        self.calls = []

    def get_unit(self, sid):
        return self.unit if self.unit and self.unit.session_id == sid else None

    def run_conversation(self, **kw):
        self.calls.append(kw)
        unit = self.unit

        async def gen():
            unit._send_generation += 1      # send() bumps it
            yield {"type": "result"}
        return gen()


@pytest.fixture(autouse=True)
def _fast():
    notices = []

    async def persist(sid, reason):
        notices.append((sid, reason))
    with patch.object(auto_continue, "GRACE_SECONDS", 0), \
         patch.object(auto_continue, "_persist_notice", side_effect=persist):
        yield notices


async def _settle(unit):
    await unit._auto_continue_task


@pytest.mark.asyncio
async def test_disconnect_continues_the_turn(_fast):
    unit = _Unit()
    router = _Router(unit)
    assert auto_continue.schedule(router, unit)
    await _settle(unit)
    assert len(router.calls) == 1
    call = router.calls[0]
    assert call["user_message"] == auto_continue.PROMPT and call["_auto_continue"] is True
    assert _fast == [("ac-1", "restarted")]
    assert unit._auto_continue_count == 1


@pytest.mark.asyncio
async def test_user_stop_after_the_abort_cancels_it(_fast):
    unit = _Unit()
    router = _Router(unit)
    with patch.object(auto_continue, "GRACE_SECONDS", 0.05):
        auto_continue.schedule(router, unit)
        await asyncio.sleep(0)
        auto_continue.mark_user_stop(unit)     # /chat/stop lands after the disconnect
        with pytest.raises(asyncio.CancelledError):
            await _settle(unit)
    assert router.calls == [] and _fast == []


@pytest.mark.asyncio
async def test_stop_recorded_just_before_also_counts(_fast):
    unit = _Unit()
    unit._user_stop_at = time.monotonic()
    router = _Router(unit)
    auto_continue.schedule(router, unit)
    await _settle(unit)
    assert router.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["newer_message", "released", "question", "streaming"])
async def test_other_owners_cancel_it(_fast, change):
    unit = _Unit()
    router = _Router(unit)
    auto_continue.schedule(router, unit)
    if change == "newer_message":
        unit._send_generation += 1
    elif change == "released":
        router.unit = None
    elif change == "question":
        unit.has_outstanding_tool_use = True
    else:
        unit.state = SessionState.STREAMING
    await _settle(unit)
    assert router.calls == []


@pytest.mark.asyncio
async def test_budget_then_gave_up_and_reset_on_a_new_user_turn(_fast):
    unit = _Unit()
    router = _Router(unit)
    for _ in range(auto_continue.LIMIT):
        auto_continue.schedule(router, unit)
        await _settle(unit)
    assert len(router.calls) == auto_continue.LIMIT
    # the last continuation's stream drops too: no 4th send, a gave_up notice
    auto_continue.schedule(router, unit)
    await _settle(unit)
    assert len(router.calls) == auto_continue.LIMIT
    assert _fast[-1] == ("ac-1", "gave_up")
    # the user sends a new message (new generation), that turn drops: fresh budget
    unit._send_generation += 1
    auto_continue.schedule(router, unit)
    await _settle(unit)
    assert len(router.calls) == auto_continue.LIMIT + 1
    assert unit._auto_continue_count == 1


@pytest.mark.asyncio
async def test_chat_disconnect_recovery_schedules_it():
    from routers import chat
    unit = MagicMock()
    unit.recover_from_disconnect.return_value = True
    router = MagicMock()
    router.get_unit.return_value = unit
    with patch.object(chat, "_session_router", router), \
         patch.object(chat, "_cleanup_subprocess_after_disconnect", MagicMock()), \
         patch("core.auto_continue.schedule", return_value=True) as sched:
        chat._recover_streaming_on_disconnect("ac-1")
    sched.assert_called_once_with(router, unit)


@pytest.mark.asyncio
async def test_chat_stop_marks_the_user_stop():
    from routers import chat
    unit = MagicMock()
    router = MagicMock()
    router.get_unit.return_value = unit
    router.interrupt_session = AsyncMock(return_value={"success": True, "message": "Interrupted"})
    with patch.object(chat, "_get_router", return_value=router), \
         patch("core.auto_continue.mark_user_stop") as mark:
        await chat.stop_session("ac-1")
    mark.assert_called_once_with(unit)


@pytest.mark.asyncio
async def test_auto_continue_turn_writes_no_user_row():
    from core.session_router import SessionRouter
    router = SessionRouter(prompt_builder=MagicMock())
    put = AsyncMock()

    class _Stop(Exception):
        pass
    with patch.object(router, "_desktop_prewarm_enabled", return_value=False), \
         patch("core.session_manager.session_manager.store_session", AsyncMock()), \
         patch("database.db.messages.put", put), \
         patch.object(router, "get_or_create_unit", side_effect=lambda *a: _unit_stub()), \
         patch.object(router, "_acquire_slot", AsyncMock(side_effect=_Stop()), create=True):
        for flag in (True, False):
            try:
                async for _ in router.run_conversation(
                    agent_id="agent-1", user_message=auto_continue.PROMPT,
                    session_id="ac-1", _auto_continue=flag,
                ):
                    break
            except Exception:
                pass
            users = [c for c in put.call_args_list if c.args and c.args[0].get("role") == "user"]
            if flag:
                assert users == [], "an auto-continue turn wrote a user row"
            else:
                assert users, "control: a normal turn writes its user row"


def _unit_stub():
    u = MagicMock()
    u.is_channel_session = False
    u._has_channel_context = False
    return u
