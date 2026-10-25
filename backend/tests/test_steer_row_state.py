"""The user row of a steer message: in flight, read, or back in the queue.

Design docs/design/2026-10-24-chat-steer.md D2 (T14 storage half, T23). A steer
is written into the running turn's stdin before the CLI has read it. Until its
echo, its row must be neither history (sent = 1 would place it in the middle of
the running turn on any reconcile) nor the queue (a drain would send it again, a
cancel would delete a message already on stdin):

| state      | sent | claimed_at | pending_seq | metadata.steer |
|------------|------|------------|-------------|----------------|
| in flight  | 0    | write time | NULL        | "inflight"     |
| read       | 1    | NULL       | (as before) | - (steered: during / after) |
| re-queued  | 0    | NULL       | new seq     | -              |

Real SQLite (tmp file), the module's own pool, no DB mocks. The live-feed push of
``confirm_steer`` is checked at the module's one seam (``_report_row``); what the
feed does with a row is the messages table's own (tested with it).
Each test fails with its function missing or its guard reverted.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from database.sqlite import SQLiteDatabase

SID = "sess-steer"


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
    reported: list[str] = []

    async def _spy(row_id: str) -> None:
        reported.append(row_id)
    monkeypatch.setattr(sp, "_report_row", _spy)
    sp._test_reported = reported
    return sp


async def _live_user_row(db_path: Path, row_id: str, client_id: str,
                         created_at: str = "2026-10-25T10:00:00") -> None:
    """The row the send path writes at POST (persist-before-slot): sent = 1."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, metadata, sent, created_at, updated_at) "
            "VALUES (?, ?, 'user', ?, ?, 1, ?, ?)",
            (row_id, SID, json.dumps([{"type": "text", "text": "skip the second one"}]),
             json.dumps({"client_id": client_id}), created_at, created_at),
        )
        await conn.commit()


async def _row(db_path: Path, row_id: str) -> dict:
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT sent, claimed_at, pending_seq, created_at, metadata FROM messages WHERE id = ?",
            (row_id,))
        r = await cur.fetchone()
    assert r is not None, f"row {row_id} is gone"
    out = dict(r)
    out["metadata"] = json.loads(out["metadata"]) if out["metadata"] else {}
    return out


# ── in flight ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_inflight_steer_is_neither_history_nor_queue(pending, db_path: Path):
    await _live_user_row(db_path, "steer-1", "c-steer")
    queued = await pending.persist_pending(SID, user_message="queued", content=None, agent_id="a")

    assert await pending.mark_steer_inflight(SID, "steer-1") is True
    r = await _row(db_path, "steer-1")
    assert r["sent"] == 0 and r["claimed_at"] is not None and r["pending_seq"] is None
    assert r["metadata"] == {"client_id": "c-steer", "steer": "inflight"}

    # not the queue: the list, the count and the drain's input see only the queued one
    assert [p["id"] for p in await pending.list_pending(SID)] == [queued.id]
    assert await pending.count_pending(SID) == 1
    assert [p.id for p in await pending.peek_pending_batch(SID)] == [queued.id]
    assert [p.id for p in await pending.claim_pending_batch(SID)] == [queued.id]
    # not cancellable: it is already on stdin
    assert await pending.cancel_unclaimed(SID, "c-steer") == "claimed"
    await _row(db_path, "steer-1")
    # not history
    import database
    hist = await database.db.messages.list_by_session(SID)
    assert "steer-1" not in [m["id"] for m in hist]


@pytest.mark.asyncio
async def test_only_a_delivered_user_row_goes_in_flight(pending, db_path: Path):
    await _live_user_row(db_path, "steer-1", "c-steer")
    assert await pending.mark_steer_inflight(SID, "steer-1") is True
    assert await pending.mark_steer_inflight(SID, "steer-1") is False  # already in flight
    q = await pending.persist_pending(SID, user_message="queued", content=None, agent_id="a")
    assert await pending.mark_steer_inflight(SID, q.id) is False       # a queued row
    assert (await _row(db_path, q.id))["metadata"].get("steer") is None
    assert await pending.mark_steer_inflight(SID, "no-such-row") is False


# ── read (the echo) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,label", [("folded", "during"), ("opened", "after")])
async def test_the_echo_makes_it_history_at_the_echo_time(pending, db_path: Path, outcome, label):
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")

    echo_at = "2026-10-25T10:03:07.123456"
    assert await pending.confirm_steer(SID, "steer-1", echo_at, outcome) is True
    r = await _row(db_path, "steer-1")
    assert r["sent"] == 1 and r["claimed_at"] is None
    assert r["created_at"] == echo_at
    assert r["metadata"] == {"client_id": "c-steer", "steered": label}
    assert pending._test_reported == ["steer-1"]  # other tabs see it now (live feed)

    import database
    hist = await database.db.messages.list_by_session(SID)
    assert "steer-1" in [m["id"] for m in hist]
    assert await pending.count_pending(SID) == 0


@pytest.mark.asyncio
async def test_confirm_only_moves_an_inflight_steer(pending, db_path: Path):
    await _live_user_row(db_path, "plain", "c-plain")
    assert await pending.confirm_steer(SID, "plain", "2026-10-25T11:00:00", "folded") is False
    r = await _row(db_path, "plain")
    assert r["created_at"] == "2026-10-25T10:00:00" and "steered" not in r["metadata"]
    assert pending._test_reported == []

    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")
    await pending.release_steer(SID, "steer-1")       # back in the queue
    assert await pending.confirm_steer(SID, "steer-1", "2026-10-25T11:00:00", "folded") is False
    assert (await _row(db_path, "steer-1"))["sent"] == 0
    with pytest.raises(ValueError):
        await pending.confirm_steer(SID, "steer-1", "2026-10-25T11:00:00", "merged")


# ── back in the queue ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_released_steer_is_queued_last_and_drains_once(pending, db_path: Path):
    first = await pending.persist_pending(SID, user_message="queued", content=None, agent_id="a")
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")

    seq = await pending.release_steer(SID, "steer-1")
    assert seq == first.pending_seq + 1
    r = await _row(db_path, "steer-1")
    assert r["sent"] == 0 and r["claimed_at"] is None and r["pending_seq"] == seq
    assert r["metadata"] == {"client_id": "c-steer"}
    assert [p["id"] for p in await pending.list_pending(SID)] == [first.id, "steer-1"]

    # cancellable again, like any queued message
    listed = {p["client_id"]: p["claimed"] for p in await pending.list_pending(SID)}
    assert listed["c-steer"] is False

    claimed = await pending.claim_pending_batch(SID)
    assert [p.id for p in claimed] == [first.id, "steer-1"]
    await pending.mark_sent_batch(SID, [p.pending_seq for p in claimed])
    assert (await _row(db_path, "steer-1"))["sent"] == 1
    assert await pending.count_pending(SID) == 0


@pytest.mark.asyncio
async def test_release_only_moves_an_inflight_steer(pending, db_path: Path):
    await _live_user_row(db_path, "plain", "c-plain")
    assert await pending.release_steer(SID, "plain") is None
    assert (await _row(db_path, "plain"))["sent"] == 1
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")
    assert await pending.confirm_steer(SID, "steer-1", "2026-10-25T11:00:00", "opened") is True
    assert await pending.release_steer(SID, "steer-1") is None   # read: stays history
    assert (await _row(db_path, "steer-1"))["sent"] == 1


# ── startup ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_requeues_an_inflight_steer_with_a_seq(pending, db_path: Path):
    """A daemon that died before the echo: the CLI never ran it (E7), so it goes back
    to the queue -- with a seq, or claim takes it and mark_sent_batch never flips it."""
    other = await pending.persist_pending(SID, user_message="queued", content=None, agent_id="a")
    await pending.claim_pending_batch(SID)                       # a drain in flight
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")

    assert await pending.reopen_dangling_claims() == 2
    r = await _row(db_path, "steer-1")
    assert r["sent"] == 0 and r["claimed_at"] is None
    assert r["pending_seq"] == other.pending_seq + 1
    assert r["metadata"] == {"client_id": "c-steer"}
    assert (await _row(db_path, other.id))["claimed_at"] is None
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sent = 0 AND pending_seq IS NULL")
        assert (await cur.fetchone())[0] == 0
    claimed = await pending.claim_pending_batch(SID)
    await pending.mark_sent_batch(SID, [p.pending_seq for p in claimed])
    assert await pending.count_pending(SID) == 0


@pytest.mark.asyncio
async def test_session_scoped_reopen_requeues_only_that_session(pending, db_path: Path):
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")
    assert await pending.reopen_dangling_claims("other-session") == 0
    assert (await _row(db_path, "steer-1"))["metadata"].get("steer") == "inflight"
    assert await pending.reopen_dangling_claims(SID) == 1
    assert (await _row(db_path, "steer-1"))["pending_seq"] == 1


# ── duplicate POST (T23) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_duplicate_post_of_an_inflight_steer_is_not_queued(pending, db_path: Path):
    from core import send_requests
    await _live_user_row(db_path, "steer-1", "c-steer")
    await pending.mark_steer_inflight(SID, "steer-1")
    rec = {"client_id": "c-steer", "session_id": SID, "status": "persisted",
           "user_message_id": "steer-1"}
    out = await send_requests.receipt(rec)
    assert out["steer"] is True and out["queued"] is False

    await pending.release_steer(SID, "steer-1")
    out = await send_requests.receipt(rec)
    assert "steer" not in out and out["queued"] is True
