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
    sid, hold, release = "s-tw-receipt", asyncio.Event(), asyncio.Event()
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


@pytest.mark.asyncio
async def test_a_result_is_durable_before_the_next_event():
    """SIGKILL-safe: a step's result is in the DB as soon as it is written, not
    when the turn's next row (or its close) comes."""
    sid, hold = "s-tw-durable", asyncio.Event()
    task = asyncio.ensure_future(_turn(_router(), sid, _tool_then("hang", hold)))
    await asyncio.wait_for(hold.wait(), 5)
    assert await _results(sid) == ["toolu_w"]          # the turn is still running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── steer (design docs/design/2026-10-24-chat-steer.md D4: T6, T16) ──────────

def _steered_turn(after_steer: str = "end", hold: asyncio.Event | None = None,
                  release: asyncio.Event | None = None):
    async def send(**kwargs: Any):
        sid = kwargs.get("app_session_id")
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [
            {"type": "text", "text": "A"},
            {"type": "tool_use", "id": "toolu_a", "name": "Bash", "summary": "Running: ls"}]}
        yield {"type": "tool_result", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_a", "content": "out", "is_error": False}]}
        yield {"type": "user_steered", "clientId": "c-steer", "outcome": "folded", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [{"type": "text", "text": "B"}]}
        if after_steer == "hang":
            hold.set()
            await release.wait()
    return send


async def _assistant_rows(sid: str) -> list[tuple]:
    from database import db
    rows = await db.messages.list_by_session(sid, include_pending=True)
    return [((r.get("metadata") or {}).get("client_id"),
             [b.get("text") or b.get("tool_use_id") or b.get("id") for b in r["content"]])
            for r in rows if r["role"] == "assistant"]


@pytest.mark.asyncio
async def test_t6_the_rows_after_a_steer_are_its_answer():
    sid = "s-tw-steer"
    router = _router()
    seen: list[dict] = []
    real = TurnWriter.write

    async def spy(self, event, **k):
        out = await real(self, event, **k)
        if event.get("type") == "user_steered":
            seen.append(dict(event))
        return out
    with patch.object(TurnWriter, "write", spy):
        await _turn(router, sid, _steered_turn())
    rows = await _assistant_rows(sid)
    assert rows[0][0] == f"local-{sid}-asst" and rows[0][1] == ["A", "toolu_a", "toolu_a"]
    assert rows[1] == ("c-steer-asst", ["B"])
    (ev,) = seen
    assert ev["rowKey"] == "c-steer-asst" and ev["segments"] == [f"local-{sid}-asst", "c-steer-asst"]
    unit = router.get_unit(sid)
    assert unit._turn_client_id == f"local-{sid}"          # the turn's identity: unchanged


@pytest.mark.asyncio
async def test_t16_after_a_steer_continuations_and_the_stop_receipt_key_on_its_segment():
    from database import db
    sid, hold, release = "s-tw-steer-stop", asyncio.Event(), asyncio.Event()
    router = _router()
    task = asyncio.ensure_future(_turn(router, sid, _steered_turn("hang", hold, release)))
    await asyncio.wait_for(hold.wait(), 5)
    unit = router.get_unit(sid)
    # the main loop rewrites the identity on every event; the segment key stays
    assert unit._turn_row_key == "c-steer" and unit._turn_client_id == f"local-{sid}"
    assert router._continuation_row_key(sid) == "c-steer-asst"
    assert router._continuation_client_id(sid) == f"local-{sid}-asst"   # published: unchanged
    unit._transition(SessionState.STREAMING)
    receipt = await router._persist_stop_receipt(sid, unit)
    release.set()
    await task
    assert receipt
    rows = await db.messages.list_by_session(sid, include_pending=True)
    (stop_row,) = [r for r in rows if any(b["type"] == "turn_stop" for b in r["content"])]
    # B2: the receipt keys on the segment's assistant key, never the steer bubble's
    assert (stop_row.get("metadata") or {}).get("client_id") == "c-steer-asst"
    # and the steer row keeps its time: a Stop started, the writer does not move it
    # past the receipt (T29) -- here the steer row is placed by the confirm only


@pytest.mark.asyncio
async def test_b2_a_stop_before_any_assistant_row_does_not_share_the_user_key():
    """Stopped before the turn wrote any assistant row: the receipt is the turn's
    first assistant row and must not carry the user bubble's client_id (the
    frontend's reconcile maps client_ids regardless of role)."""
    from database import db
    sid, hold, release = "s-tw-stop-early", asyncio.Event(), asyncio.Event()

    async def send(**kwargs: Any):
        yield {"type": "session_start", "sessionId": kwargs.get("app_session_id")}
        hold.set()
        await release.wait()
    router = _router()
    task = asyncio.ensure_future(_turn(router, sid, send))
    await asyncio.wait_for(hold.wait(), 5)
    unit = router.get_unit(sid)
    unit._transition(SessionState.STREAMING)
    receipt = await router._persist_stop_receipt(sid, unit)
    release.set()
    await task
    assert receipt
    rows = await db.messages.list_by_session(sid, include_pending=True)
    (stop_row,) = [r for r in rows if any(b["type"] == "turn_stop" for b in r["content"])]
    assert (stop_row.get("metadata") or {}).get("client_id") == f"local-{sid}-asst"


def _reader_ahead_turn(sid_box: dict):
    """B-T1: the reader is far ahead of the writer (opened: the final message, the
    Result, init and the echo arrive together). The confirm task has already
    written the steer row at the echo time, which is EARLIER than segment 1's last
    row, inserted only when the writer got to it."""
    async def send(**kwargs: Any):
        from datetime import datetime
        from database import db
        sid = kwargs.get("app_session_id")
        echo_at = datetime.now().isoformat()
        await db.messages.put({"id": f"row-steer-{sid}", "session_id": sid, "role": "user",
                               "content": [{"type": "text", "text": "and the tests"}],
                               "metadata": {"client_id": "c-steer", "steered": "after"},
                               "created_at": echo_at})
        await asyncio.sleep(0.002)
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [{"type": "text", "text": "A"}]}
        yield {"type": "user_steered", "clientId": "c-steer", "outcome": "opened", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [{"type": "text", "text": "B"}]}
    return send


@pytest.mark.asyncio
async def test_b_t1_reload_order_is_the_live_order_when_the_reader_runs_ahead():
    from database import db
    sid = "s-tw-reader-ahead"
    await _turn(_router(), sid, _reader_ahead_turn({}))
    rows = await db.messages.list_by_session(sid, include_pending=True)
    shape = [(r["role"], (r.get("metadata") or {}).get("client_id")) for r in rows]
    assert shape[-3:] == [("assistant", f"local-{sid}-asst"), ("user", "c-steer"),
                          ("assistant", "c-steer-asst")], shape


@pytest.mark.asyncio
async def test_a_turn_without_a_steer_keeps_one_segment():
    sid = "s-tw-nosteer"
    router = _router()
    await _turn(router, sid, _tool_then("stop"))
    unit = router.get_unit(sid)
    assert unit._turn_row_key == f"local-{sid}" and unit._turn_segments == []


@pytest.mark.asyncio
async def test_the_next_turn_starts_on_its_own_segment_after_a_steered_one():
    sid = "s-tw-steer-next"
    router = _router()
    await _turn(router, sid, _steered_turn())
    unit = router.get_unit(sid)
    unit._transition(SessionState.IDLE)
    unit._reset_turn_state()   # the real send() does it at admission (send is patched here)
    with patch("core.session_unit.SessionUnit.send", side_effect=_tool_then("stop")):
        async for _ in router.run_conversation(agent_id="default", user_message="next",
                                               session_id=sid, client_id="local-next"):
            pass
    assert unit._turn_row_key == "local-next" and unit._turn_segments == []
    rows = await _assistant_rows(sid)
    assert rows[-1][0] == "local-next-asst"
