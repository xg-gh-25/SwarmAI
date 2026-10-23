"""A send the backend parks as pending keeps its attached terminal output.

When all slots are busy (QUEUE_TIMEOUT) or the session is busy (SESSION_BUSY),
run_conversation turns the user row into a pending row (mark_pending_by_id). The
terminal output rides the request's terminal_context, not the message text, so it
must be kept with the row (metadata, only while pending) for the drain to deliver
it (docs/design/terminal-locations.md "Add to chat").

Covers: mark_pending_by_id stores it next to client_id; peek/claim return it;
combine_terminal joins a coalesced set within the schema limit (heading a second
folder's output); mark_sent_batch removes it once delivered; the hand-back payload
(persist-failed fallback) carries it; a malformed value is ignored. Real SQLite
(tmp file), same fixture as test_session_pending.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from database.sqlite import SQLiteDatabase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def pending(db_path: Path, monkeypatch):
    db = SQLiteDatabase(db_path=db_path)
    await db.initialize()
    import database
    import core.session_pending as sp
    monkeypatch.setattr(database, "db", db, raising=False)
    monkeypatch.setattr(sp, "_db_path_override", str(db_path), raising=False)
    sp._SEQ_LOCKS.clear()
    return sp


async def _live_row(db_path: Path, mid: str, session: str, text: str = "fix this") -> None:
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, metadata, sent, created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, '{\"client_id\": \"c-1\"}', 1, '2026-01-01', '2026-01-01')",
            (mid, session, json.dumps([{"type": "text", "text": text}])),
        )
        await conn.commit()


async def _metadata(db_path: Path, mid: str) -> dict:
    async with aiosqlite.connect(str(db_path)) as conn:
        row = await (await conn.execute("SELECT metadata FROM messages WHERE id = ?", (mid,))).fetchone()
    return json.loads(row[0])


@pytest.mark.asyncio
async def test_pending_row_keeps_terminal_output_until_delivered(pending, db_path: Path):
    await _live_row(db_path, "m1", "s1")
    tc = {"buffer_tail": "FAIL TerminalStore.test.ts", "cwd": "/r/swarmai"}
    seq = await pending.mark_pending_by_id("s1", "m1", terminal_context=tc)
    assert (await _metadata(db_path, "m1")) == {"client_id": "c-1", "terminal_context": tc}

    [peeked] = await pending.peek_pending_batch("s1")
    assert peeked.terminal_context == tc
    claimed = await pending.claim_pending_batch("s1")
    assert [c.terminal_context for c in claimed] == [tc]
    assert pending.combine_terminal(claimed) == tc

    await pending.mark_sent_batch("s1", [seq])
    assert (await _metadata(db_path, "m1")) == {"client_id": "c-1"}   # removed once delivered


@pytest.mark.asyncio
async def test_without_terminal_output_nothing_changes(pending, db_path: Path):
    await _live_row(db_path, "m2", "s2")
    await pending.mark_pending_by_id("s2", "m2")
    assert (await _metadata(db_path, "m2")) == {"client_id": "c-1"}
    [row] = await pending.claim_pending_batch("s2")
    assert row.terminal_context is None and pending.combine_terminal([row]) is None


@pytest.mark.asyncio
async def test_malformed_terminal_context_is_ignored(pending, db_path: Path):
    await _live_row(db_path, "m3", "s3")
    await pending.mark_pending_by_id("s3", "m3", terminal_context={"buffer_tail": 42})
    assert "terminal_context" not in await _metadata(db_path, "m3")
    await pending.mark_pending_by_id("s3", "m3", terminal_context={"buffer_tail": ""})
    [row] = await pending.peek_pending_batch("s3")
    assert row.terminal_context is None


def test_combine_terminal_joins_a_coalesced_set_within_the_limit():
    from core.session_pending import PendingMessage, combine_terminal, _TERMINAL_MAX

    def row(seq, tc):
        return PendingMessage(id=f"m{seq}", session_id="s", pending_seq=seq, user_message="x",
                              content=None, created_at="t", terminal_context=tc)
    a = {"buffer_tail": "from a", "cwd": "/r/a"}
    b = {"buffer_tail": "from b", "cwd": "/r/b"}
    got = combine_terminal([row(1, a), row(2, None), row(3, a), row(4, b)])
    assert got == {"buffer_tail": "from a\n\nfrom a\n\n# cwd: /r/b\nfrom b", "cwd": "/r/a"}
    big = combine_terminal([row(1, {"buffer_tail": "x" * _TERMINAL_MAX, "cwd": ""}), row(2, b)])
    assert len(big["buffer_tail"]) == _TERMINAL_MAX and big["buffer_tail"].endswith("from b")
    assert combine_terminal([row(1, None)]) is None


def test_the_limit_matches_the_request_schema():
    from core.session_pending import _TERMINAL_MAX
    from schemas.message import TerminalContext
    assert TerminalContext.model_fields["buffer_tail"].metadata[0].max_length == _TERMINAL_MAX


@pytest.mark.asyncio
async def test_hand_back_payload_carries_terminal_output(monkeypatch):
    """Persist-failed fallback: the client re-sends from retryPayload, so it must
    carry the terminal output (camelCase, as the desktop reads it)."""
    from core import send_requests
    from core.session_router import SessionRouter

    async def _release(_cid):
        return None
    monkeypatch.setattr(send_requests, "release", _release)
    router = SessionRouter.__new__(SessionRouter)
    p = await router._hand_back_send("c-1", "s1", "a1", None, [{"type": "text", "text": "hi"}], None,
                                     {"buffer_tail": "tail", "cwd": "/r/x"})
    assert p["terminalContext"] == {"bufferTail": "tail", "cwd": "/r/x"}
    p2 = await router._hand_back_send("c-1", "s1", "a1", "hi", None, None)
    assert "terminalContext" not in p2


@pytest.mark.asyncio
async def test_drain_delivers_the_parked_terminal_output(pending, db_path: Path):
    """The IDLE-edge drain hands the parked rows' terminal output to the turn
    (run_conversation then fences it onto the user turn), and the delivered row
    no longer carries it."""
    from unittest.mock import MagicMock
    from core.session_router import SessionRouter
    from core.session_unit import SessionState

    sid = "s-drain"
    await _live_row(db_path, "d1", sid, "why did this fail")
    tc = {"buffer_tail": "Error: boom", "cwd": "/r/swarmai"}
    await pending.mark_pending_by_id(sid, "d1", terminal_context=tc)

    router = SessionRouter(prompt_builder=MagicMock())
    unit = MagicMock()
    unit.session_id, unit.agent_id, unit.is_alive = sid, "agent-1", True
    unit.state, unit.has_outstanding_tool_use, unit._last_drained_seqs = SessionState.IDLE, False, []
    router._units[sid] = unit
    calls: list[dict] = []

    def _run(**kwargs):
        calls.append(kwargs)

        async def _agen():
            yield {"type": "result", "sessionId": sid}
        return _agen()
    router.run_conversation = _run  # type: ignore[assignment]
    try:
        await router.drain_pending(sid)
    finally:
        task = router._drain_worker_task
        if task is not None and not task.done():
            task.cancel()
    assert len(calls) == 1
    assert calls[0]["terminal_context"] == tc
    assert calls[0]["user_message"] == "why did this fail"
    assert "terminal_context" not in await _metadata(db_path, "d1")


def test_every_park_and_coalesce_site_carries_the_terminal_output():
    """Wiring guard (source scan): every place session_router parks a send keeps
    its terminal output, and the cold-spawn coalesce prefixes the claimed rows'."""
    import re
    src = (Path(__file__).resolve().parents[1] / "core" / "session_router.py").read_text()
    calls = [m.group(0) for m in re.finditer(r"mark_pending_by_id\([^)]*\)", src)]
    assert len(calls) >= 3
    for c in calls:
        assert "terminal_context=terminal_context" in c, c
    hand_backs = [m.group(0) for m in re.finditer(r"await self\._hand_back_send\([^)]*\)", src)]
    assert len(hand_backs) >= 2
    for c in hand_backs:
        assert c.rstrip(")").rstrip().endswith("terminal_context"), c
    merge_at = src.index("query_content = self._merge_pending_into_query(")
    assert "combine_terminal(_claimed_pending)" in src[merge_at:merge_at + 800]
    assert "terminal_context=terminal," in src[src.index("async def drain_pending"):]
