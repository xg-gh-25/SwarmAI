"""core/turn_writer: one writer per turn, closed by the turn wrapper.

- Structure (guard): nothing in ``core/`` calls ``_persist_stream_event`` except
  ``TurnWriter``, and every ``_published_turn`` call passes the turn's writer
  (the parameter is required) — a new turn path cannot write around it.
- ``close()`` writes the held step results once (idempotent); a write failure
  never raises into the turn.
- A Stop receipt written while the turn runs lands after the result the turn
  already showed (the router flushes the running turn's writer first).
- Real path (conftest SQLite, real ``run_conversation``): a turn ending right
  after a tool result — Stop, an error, or its task cancelled at shutdown — keeps
  the result, and it is in the DB before ``turn_end`` is published.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.session_manager  # noqa: F401 — binds db at import, before any patch
import routers.chat  # noqa: F401
from core import session_events
from core.session_router import SessionRouter
from core.session_unit import SessionState
from core.turn_writer import TurnWriter

CORE = Path(__file__).resolve().parents[1] / "core"


def test_only_the_turn_writer_persists_stream_events():
    callers = [p.name for p in CORE.rglob("*.py")
               if p.name != "turn_writer.py" and re.search(r"\._persist_stream_event\(", p.read_text())]
    assert callers == [], f"write a turn's events through TurnWriter, not directly: {callers}"


def test_every_published_turn_gets_its_writer():
    src = (CORE / "session_router.py").read_text()
    calls = re.findall(r"self\._published_turn\((.*?)\)\) as turn", src, re.S)
    assert calls and all("writer=writer" in c for c in calls), calls


class _Router:
    def __init__(self, fail: bool = False):
        self.rows: list[list[dict]] = []
        self.fail = fail

    async def _persist_stream_event(self, sid, event, held, **_k):
        if self.fail:
            raise RuntimeError("db gone")
        if event["type"] == "tool_result":
            held.append({"type": "tool_result", "tool_use_id": "t1"})
        return []

    async def _persist_assistant_blocks(self, sid, blocks, model, **_k):
        self.rows.append(blocks)
        return blocks


@pytest.mark.asyncio
async def test_close_writes_the_held_results_once():
    r = _Router()
    w = TurnWriter(r, "s", row_key="c-asst")
    await w.write({"type": "tool_result", "content": [{}]})
    await w.close()
    await w.close()
    assert r.rows == [[{"type": "tool_result", "tool_use_id": "t1"}]]


@pytest.mark.asyncio
async def test_a_write_failure_does_not_raise():
    w = TurnWriter(_Router(fail=True), "s", row_key=None)
    assert await w.write({"type": "assistant", "content": [{"type": "text", "text": "x"}]}) == []


# ── real path ─────────────────────────────────────────────────────────────────

def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _tool_then(end: str, hold: asyncio.Event | None = None, release: asyncio.Event | None = None):
    async def send(**kwargs: Any):
        sid = kwargs.get("app_session_id")
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [
            {"type": "tool_use", "id": "toolu_w", "name": "Bash", "summary": "Running: ls"}]}
        yield {"type": "tool_result", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_w", "content": "out", "is_error": False}]}
        if end == "error":
            raise RuntimeError("the CLI died")
        if end == "hang":
            hold.set()
            await (release or asyncio.Event()).wait()
    return send


async def _results(sid: str) -> list[str]:
    from database import db
    rows = await db.messages.list_by_session(sid, include_pending=True)
    return [b.get("tool_use_id") for r in rows for b in r["content"] if b.get("type") == "tool_result"]


async def _turn(router, sid, send):
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for _ in router.run_conversation(agent_id="default", user_message="go",
                                               session_id=sid, client_id=f"local-{sid}"):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["stop", "error"])
async def test_a_turn_ending_after_a_result_keeps_it(end):
    sid = f"s-tw-{end}"
    try:
        await _turn(_router(), sid, _tool_then(end))
    except RuntimeError:
        pass
    assert await _results(sid) == ["toolu_w"]


@pytest.mark.asyncio
async def test_a_turn_cancelled_at_shutdown_keeps_its_result():
    sid, hold = "s-tw-cancel", asyncio.Event()
    task = asyncio.ensure_future(_turn(_router(), sid, _tool_then("hang", hold)))
    await asyncio.wait_for(hold.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _results(sid) == ["toolu_w"]


@pytest.mark.asyncio
async def test_the_result_is_in_the_db_when_turn_end_is_published():
    sid = "s-tw-order"
    hub = session_events.SessionEventHub()
    orig = hub.publish
    # whether the writer had finished closing when turn_end went out
    closed: list[bool] = []
    seen_at_end: list[list[bool]] = []
    real_close = TurnWriter.close

    async def close(self):
        await real_close(self)
        closed.append(True)

    def publish(session_id, event, **k):
        if event.get("type") == "turn_end":
            seen_at_end.append(list(closed))
        return orig(session_id, event, **k)
    hub.publish = publish
    with patch.object(session_events, "_hub", hub), patch.object(TurnWriter, "close", close):
        await _turn(_router(), sid, _tool_then("stop"))
    assert seen_at_end == [[True]]
    assert await _results(sid) == ["toolu_w"]


@pytest.mark.asyncio
async def test_a_stop_receipt_lands_after_the_result_already_shown():
    from database import db
    sid, hold, release = "s-tw-stop", asyncio.Event(), asyncio.Event()
    router = _router()
    task = asyncio.ensure_future(_turn(router, sid, _tool_then("hang", hold, release)))
    await asyncio.wait_for(hold.wait(), 5)
    unit = router.get_unit(sid)
    unit._transition(SessionState.STREAMING)    # as the real send() does at admission
    receipt = await router._persist_stop_receipt(sid, unit)   # /chat/stop
    release.set()
    await task
    assert receipt, "a running turn gets a receipt"
    kinds = [b["type"] for r in await db.messages.list_by_session(sid, include_pending=True)
             for b in r["content"] if b["type"] in ("tool_result", "turn_stop")]
    assert kinds == ["tool_result", "turn_stop"]
