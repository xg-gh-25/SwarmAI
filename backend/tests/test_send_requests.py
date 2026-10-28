"""core/send_requests + schema v14: the send ledger.

- Migration: a v13 database gets ``send_requests`` and lands at v14; re-running
  is a no-op.
- Reservation: of N concurrent reservations of one client_id exactly one wins;
  a reservation that never reached its user row (failed, or left by another
  process) is taken over; one with a user row never is.
- Real path (conftest SQLite, real ``run_conversation``):
  - a duplicate gets an ``accepted`` receipt naming the first send's session and
    user row, with ``queued`` read from the row (true after SESSION_BUSY);
  - a send cancelled before its user row releases the reservation (the retry runs);
  - a ledger that raises fails open (the send runs, one user row).
- Channel: the gateway's client_id is the platform message id, scoped by channel
  and chat; none without one.
- Prune drops only requests past the messages TTL.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import core.session_manager  # noqa: F401 — binds db at import, before any patch
import routers.chat  # noqa: F401
from core import send_requests as sr
from core.exceptions import SessionBusyError
from core.session_router import SessionRouter
from core.session_unit import SessionState


@pytest.fixture(autouse=True)
def _no_background_drain(monkeypatch):
    """This file's `_busy` double makes send() raise SESSION_BUSY on a unit that is
    IDLE — a state production never has (a busy error means the unit streams or its
    CLI is not quiescent). The queued message then really is deliverable, and the
    end of the turn kicks the drain (docs/design/chat-turn-liveness.md S1), which
    would deliver it through the REAL SessionUnit.send after this test's patch is
    gone. The drain is covered by test_session_drain.py / test_turn_liveness_p0.py;
    here it is kept from running."""
    async def _skip(self, session_id):
        return None
    monkeypatch.setattr(SessionRouter, "drain_pending", _skip)


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _finish_state_pushes():
    """A send queued here pushes the session's state (core/session_state.publish:
    a fire-and-forget task that reads the queue from the DB pool). Each test waits
    for its pushes, so none is cut off holding a pooled connection when the test's
    loop closes (that left the session teardown waiting on the pool forever)."""
    yield
    pubs = [t for t in asyncio.all_tasks()
            if not t.done() and "publish" in getattr(t.get_coro(), "__qualname__", "")]
    if pubs:
        await asyncio.wait(pubs, timeout=2)


def _row(cid: str, sid: str = "s", owner: str = sr.OWNER, status: str = sr.RESERVED, mid=None, at=None):
    now = at or datetime.now().isoformat()
    return {"client_id": cid, "session_id": sid, "status": status, "owner": owner,
            "user_message_id": mid, "created_at": now, "updated_at": now}


@pytest.mark.asyncio
async def test_v13_database_migrates_to_v14(tmp_path):
    import aiosqlite
    from database.sqlite import SQLiteDatabase
    path = str(tmp_path / "m.db")
    d = SQLiteDatabase(path)
    await d.initialize()
    async with aiosqlite.connect(path) as conn:
        await conn.execute("DROP TABLE send_requests")
        await conn.execute("PRAGMA user_version = 13")
        await conn.commit()
        await d._run_versioned_migrations(conn, 13)
        await d._run_versioned_migrations(conn, 13)          # idempotent
        assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == 17   # v15-v17 chain on
        cols = [r[1] for r in await (await conn.execute("PRAGMA table_info(send_requests)")).fetchall()]
    assert cols == list(SQLiteDatabase.SEND_REQUEST_COLS)


@pytest.mark.asyncio
async def test_concurrent_reservations_have_one_winner():
    results = await asyncio.gather(*(sr.reserve("cid-race", "s-race") for _ in range(8)))
    assert sum(r.taken for r in results) == 1


@pytest.mark.asyncio
async def test_takeover_only_before_the_user_row():
    from database import db
    await db.reserve_send_request(_row("cid-dead", owner="other-proc"))
    assert (await sr.reserve("cid-dead", "s")).taken                 # a dead process's
    await sr.failed("cid-dead")
    assert (await sr.reserve("cid-dead", "s")).taken                 # failed → retry runs
    await db.reserve_send_request(_row("cid-done", owner="other-proc", status=sr.PERSISTED, mid="m1"))
    r = await sr.reserve("cid-done", "s")
    assert not r.taken and r.record["user_message_id"] == "m1"
    assert not (await sr.reserve("cid-dead", "s")).taken             # ours, in flight


def test_channel_client_id_is_scoped_and_optional():
    a = sr.channel_client_id("slack", "C1", "D1", "17.1")
    assert a != sr.channel_client_id("slack", "C2", "D1", "17.1")
    assert a == sr.channel_client_id("slack", "C1", "D1", "17.1")
    assert sr.channel_client_id("slack", "C1", "D1", None) is None


@pytest.mark.asyncio
async def test_prune_keeps_requests_within_the_ttl():
    from database import db
    from database.sqlite import SQLiteMessagesTable
    old = (datetime.now() - timedelta(seconds=SQLiteMessagesTable.TTL_SECONDS + 60)).isoformat()
    await db.reserve_send_request(_row("cid-old", at=old))
    await db.reserve_send_request(_row("cid-new"))
    await sr.prune()
    assert await db.get_send_request("cid-old") is None
    assert await db.get_send_request("cid-new") is not None


# ── real path ─────────────────────────────────────────────────────────────────

def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _reply(**kwargs: Any):
    async def gen():
        sid = kwargs.get("app_session_id")
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "content": [{"type": "text", "text": "ok"}], "model": "m"}
        yield {"type": "result", "sessionId": sid}
    return gen()


def _busy(**_k):
    async def gen():
        raise SessionBusyError(detail="actively streaming")
        yield  # pragma: no cover
    return gen()


async def _send(router, sid, cid, send=_reply):
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    if unit.state != SessionState.IDLE:
        unit._transition(SessionState.IDLE)
    out = []
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for ev in router.run_conversation(agent_id="default", user_message="hi",
                                               session_id=sid, client_id=cid):
            out.append(ev)
    return out


async def _users(sid):
    from database import db
    return [r for r in await db.messages.list_by_session(sid, include_pending=True) if r["role"] == "user"]


@pytest.mark.asyncio
async def test_a_duplicate_gets_the_first_sends_receipt():
    r = _router()
    await _send(r, "s-rc", "local-rc")
    (ev,) = await _send(r, "s-rc", "local-rc")
    (row,) = await _users("s-rc")
    assert ev == {"type": "accepted", "duplicate": True, "clientId": "local-rc", "sessionId": "s-rc",
                  "status": sr.PERSISTED, "userMessageId": row["id"], "queued": False}


@pytest.mark.asyncio
async def test_a_duplicate_of_a_queued_send_says_queued():
    r = _router()
    await _send(r, "s-q", "local-q", send=_busy)
    (ev,) = await _send(r, "s-q", "local-q", send=_busy)
    assert ev["queued"] is True and len(await _users("s-q")) == 1


@pytest.mark.asyncio
async def test_a_send_cancelled_before_its_row_releases_the_reservation():
    from core import session_manager as sm
    r = _router()
    with patch.object(sm.session_manager, "store_session", AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await _send(r, "s-cx", "local-cx")
    assert len(await _users("s-cx")) == 0
    await _send(r, "s-cx", "local-cx")                               # the retry runs
    assert len(await _users("s-cx")) == 1


@pytest.mark.asyncio
async def test_a_broken_ledger_fails_open():
    from database import db
    r = _router()
    with patch.object(db, "reserve_send_request", AsyncMock(side_effect=RuntimeError("locked"))):
        events = await _send(r, "s-open", "local-open")
    assert any(e.get("type") == "result" for e in events)
    assert len(await _users("s-open")) == 1


@pytest.mark.asyncio
async def test_every_taken_send_gets_its_receipt_first():
    """The POST's first reply once the row is written: unpublished (no turnId),
    before session_start, naming the row — and before a SESSION_BUSY too."""
    r = _router()
    ok = await _send(r, "s-acc", "local-acc")
    (row,) = await _users("s-acc")
    first = [e for e in ok if e.get("type") not in ("turn_phase",)][0]
    assert first == {"type": "accepted", "duplicate": False, "clientId": "local-acc", "sessionId": "s-acc",
                     "status": sr.PERSISTED, "userMessageId": row["id"], "queued": False}
    assert "turnId" not in first
    busy = await _send(r, "s-acc2", "local-acc2", send=_busy)
    assert [e["type"] for e in busy if e["type"] in ("accepted", "error")] == ["accepted", "error"]


@pytest.mark.asyncio
async def test_a_send_the_server_cannot_keep_is_handed_back_and_resent_once():
    """SESSION_BUSY whose row cannot be made pending: the row (never delivered) is
    removed, the payload carries the client_id, and the resend with it runs once."""
    from core import session_pending
    r = _router()
    with patch.object(session_pending, "mark_pending_by_id", AsyncMock(side_effect=RuntimeError("locked"))):
        ev = await _send(r, "s-hb", "local-hb", send=_busy)
    busy = [e for e in ev if e.get("code") == "SESSION_BUSY"][0]
    assert busy["retryPayload"]["clientId"] == "local-hb"
    assert await _users("s-hb") == []
    await _send(r, "s-hb", "local-hb")                                   # the client's resend
    await _send(r, "s-hb", "local-hb")                                   # and a retry of it
    assert len(await _users("s-hb")) == 1
