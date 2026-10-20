"""One subprocess per unit when a retry and a user message race (2026-10-20, a878efb9).

The stall watchdog killed a stream; while its retry slept in backoff the user sent
"继续". Both found the unit COLD, both spawned a CLI, the second overwrote
``_client`` and the first kept streaming into the same chat and run as an orphan.

Tested here:
- ``_spawn`` reuses the unit's live client instead of starting a second process.
- a retry whose send was superseded (``_send_generation`` moved during the backoff,
  or during its spawn) does not spawn / stream, and yields a ``superseded`` notice.
- a retry after the stall watchdog yields one ``restarted`` notice with cause
  ``stalled`` (core/turn_notice).
- ``turn_notice`` is a display block, kept out of resume history.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit

STALL = "Command failed with exit code -9 (exit code: -9)"


def _unit() -> SessionUnit:
    u = SessionUnit(session_id="race-1", agent_id="agent-1")
    u._sdk_session_id = "sdk-1"
    u.MAX_RETRY_ATTEMPTS = 3
    u.RETRY_BACKOFF_SECONDS = 0.01
    return u


def _patches(unit):
    budget = MagicMock(can_spawn=True, reason="")
    return [
        patch("core.session_unit._oom_cooldown_until", 0),
        patch("core.session_unit._spawn_lock", asyncio.Lock()),
        patch("core.resource_monitor.resource_monitor.spawn_budget", return_value=budget),
        patch.object(unit, "_build_retry_options", return_value=MagicMock()),
        patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock),
        patch.object(unit, "_transition"),
    ]


async def _drain(unit, sleep_side_effect=None):
    started = [p.start() for p in _patches(unit)]
    try:
        with patch("asyncio.sleep", new_callable=AsyncMock, side_effect=sleep_side_effect):
            return [e async for e in unit._retry_with_resume("msg", MagicMock(), None, STALL, "")]
    finally:
        patch.stopall()
        del started


@pytest.mark.asyncio
async def test_spawn_reuses_a_live_client():
    """The second spawner under _spawn_lock finds the first one's client."""
    unit = _unit()
    unit._client = live = MagicMock()
    with patch("core.claude_environment._ClaudeClientWrapper") as wrapper:
        await unit._spawn(MagicMock(), None)
    wrapper.assert_not_called()
    assert unit._client is live


@pytest.mark.asyncio
async def test_retry_superseded_during_backoff_does_not_spawn():
    unit = _unit()
    spawn = AsyncMock()

    async def user_sends(_secs):   # the user's message arrives while the retry sleeps
        unit._send_generation += 1

    with patch.object(unit, "_spawn", spawn), \
         patch.object(unit._streaming_orchestrator, "stream_query") as stream:
        events = await _drain(unit, user_sends)
    spawn.assert_not_called()
    stream.assert_not_called()
    notices = [b for e in events if e.get("type") == "assistant"
               for b in e["content"] if b.get("type") == "turn_notice"]
    assert [n["reason"] for n in notices][-1] == "superseded"
    assert events[-1] == {"_abort": True}


@pytest.mark.asyncio
async def test_retry_superseded_during_its_spawn_does_not_stream():
    unit = _unit()

    async def spawn(*_a, **_k):    # the newer send got its turn while we spawned
        unit._send_generation += 1

    with patch.object(unit, "_spawn", side_effect=spawn), \
         patch.object(unit._streaming_orchestrator, "stream_query") as stream:
        events = await _drain(unit)
    stream.assert_not_called()
    assert events[-1] == {"_abort": True}


@pytest.mark.asyncio
async def test_retry_after_the_stall_watchdog_says_restarted_stalled():
    unit = _unit()
    unit._last_unstick_at = time.monotonic()

    async def ok_stream(_q):
        yield {"type": "result"}

    with patch.object(unit, "_spawn", AsyncMock()), \
         patch.object(unit._streaming_orchestrator, "stream_query", side_effect=ok_stream):
        events = await _drain(unit)
    notices = [b for e in events if e.get("type") == "assistant"
               for b in e["content"] if b.get("type") == "turn_notice"]
    assert len(notices) == 1 and notices[0]["reason"] == "restarted"
    assert notices[0]["cause"] == "stalled"
    assert {"type": "result"} in events


def test_turn_notice_is_kept_out_of_resume_history():
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "turn_notice" in _TOOL_ONLY_TYPES
