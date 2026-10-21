"""Layer-4 contract: the streaming-state mirror and the SessionUnit surface it reads.

A client disconnect detaches the turn instead of cancelling it (core/session_events),
so the SessionUnit's old disconnect-recovery path (a post-disconnect pipe flush and
the "still flushing" flag it reported) has no trigger. This file binds what remains:

- a real SessionUnit carries no member of that path (I1);
- GET /api/chat/sessions/streaming-state, called over HTTP through the real router,
  returns per-session entries with exactly the live keys (I2). The desktop's
  chatService.getStreamingState (desktop/src/services/chat.ts) maps all of them
  except `pid`, which only the backend's session-health probe reads;
  scripts/daemon-lib.sh reads `streaming`.

REMOVED_MEMBERS names the deleted members on purpose: it is the absence check that
keeps them from coming back, not a reader of them.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from core import session_unit
from core.session_unit import SessionState, SessionUnit
from routers import chat

LIVE_KEYS = {
    "streaming", "state", "pid", "waiting_input",
    "pending_count", "pending_question", "last_drained_seqs",
}

REMOVED_MEMBERS = (
    "recover_from_disconnect",
    "schedule_pipe_flush",
    "flush_subprocess_pipe",
    "is_post_disconnect_flushing",
    "_pipe_flush_task",
)


@pytest.fixture(autouse=True)
def _restore_streaming_count(monkeypatch):
    """A STREAMING transition adds to the daemon-wide streaming counter; keep it
    local to each test so later tests in the process see the real count."""
    monkeypatch.setattr(session_unit, "_streaming_count", session_unit._streaming_count)


def _unit(session_id: str, state: SessionState) -> SessionUnit:
    unit = SessionUnit(session_id=session_id, agent_id="default")
    if state is not SessionState.COLD:
        unit._transition(SessionState.IDLE)
    if state is SessionState.STREAMING:
        unit._transition(SessionState.STREAMING)
    return unit


def test_session_unit_has_no_disconnect_recovery_members():
    unit = _unit("S-members", SessionState.IDLE)
    present = [name for name in REMOVED_MEMBERS if hasattr(unit, name)]
    assert present == [], f"disconnect-recovery members still on SessionUnit: {present}"


@pytest.mark.asyncio
async def test_streaming_state_entry_has_exactly_the_live_keys(monkeypatch):
    units = [_unit("S-idle", SessionState.IDLE), _unit("S-run", SessionState.STREAMING)]
    sr = MagicMock()
    sr.list_units.return_value = units
    monkeypatch.setattr(chat, "_get_router", lambda: sr)

    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/chat/sessions/streaming-state")

    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert set(sessions) == {"S-idle", "S-run"}
    for sid, entry in sessions.items():
        assert set(entry) == LIVE_KEYS, (sid, sorted(set(entry) ^ LIVE_KEYS))
    assert sessions["S-idle"]["streaming"] is False and sessions["S-idle"]["state"] == "idle"
    assert sessions["S-run"]["streaming"] is True and sessions["S-run"]["state"] == "streaming"
