"""Tests for Root-1 SSOT Phase 1: schema v6 migration + session_pending module.

Phase 1 is PURELY ADDITIVE — it establishes the pending-message persistence
primitives that Phase 2's drain worker will consume. Nothing reads the `sent`
column outside this module + the migration yet (zero behavior change).

Covers:
- Migration v5→v6: sent/pending_seq/claimed_at columns + idx_messages_pending,
  user_version bumped to 6, legacy rows default sent=1.
- session_pending three-phase row lifecycle (pending → claimed → sent) with
  rollback, per-session monotonic pending_seq under concurrency, FIFO coalesce,
  and crash-window reopen.

Methodology: real SQLite (tmp file) — no mocking of the DB boundary, since
SQLite is a local-substitutable dependency (BUILD Mock Discipline table).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from database.sqlite import SQLiteDatabase, CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


async def _make_migrated_db(db_path: Path) -> SQLiteDatabase:
    """Create a fully-initialized DB at current schema (runs all migrations)."""
    db = SQLiteDatabase(db_path=db_path)
    await db.initialize()
    return db


# ---------------------------------------------------------------------------
# AC1 / AC2 — Migration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_adds_columns_and_index_and_bumps_version(db_path: Path):
    """AC1: after migration, messages has sent/pending_seq/claimed_at + index,
    and user_version is bumped to the CURRENT schema version.

    Assert against CURRENT_SCHEMA_VERSION, not a hard-coded literal: a full
    initialize() runs ALL migrations, so the DB ends at CURRENT, not at the
    version that happened to add these columns. Hard-coding `== 6` left this
    test pre-existing RED once CURRENT advanced past 6 (the same hard-coded-
    version class this fix's v9 change addresses)."""
    await _make_migrated_db(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute("PRAGMA user_version")
        version = (await cursor.fetchone())[0]
        assert version == CURRENT_SCHEMA_VERSION, \
            f"expected user_version={CURRENT_SCHEMA_VERSION}, got {version}"

        cursor = await conn.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "sent" in cols
        assert "pending_seq" in cols
        assert "claimed_at" in cols

        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_messages_pending'"
        )
        assert await cursor.fetchone() is not None, "idx_messages_pending missing"


@pytest.mark.asyncio
async def test_legacy_rows_default_sent_1(db_path: Path):
    """AC2: a pre-migration row (inserted at v5 schema) reads sent=1 after
    migration — no phantom-pending, no backfill needed."""
    # Build a v5-shaped DB: messages table without the v6 columns, version=5.
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                metadata TEXT DEFAULT '{}',
                expires_at INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, updated_at) "
            "VALUES ('legacy1', 'sess-legacy', 'user', '[]', '2026-01-01', '2026-01-01')"
        )
        await conn.execute("PRAGMA user_version = 5")
        await conn.commit()

    # Run migrations on the existing v5 DB.
    db = SQLiteDatabase(db_path=db_path)
    await db.initialize()

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT sent FROM messages WHERE id = 'legacy1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1, "legacy row must default to sent=1"


# ---------------------------------------------------------------------------
# session_pending module fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def pending(db_path: Path, monkeypatch):
    """Migrated DB + a session_pending store bound to it.

    session_pending reaches the messages table via its own _WALConnection on
    db.messages.db_path, so we point the global db singleton at our tmp DB.
    """
    db = await _make_migrated_db(db_path)
    import database
    import core.session_pending as sp
    monkeypatch.setattr(database, "db", db, raising=False)
    monkeypatch.setattr(sp, "_db_path_override", str(db_path), raising=False)
    # Fresh per-session locks each test to avoid cross-test contention.
    sp._SEQ_LOCKS.clear()
    return sp


async def _seed_session_row(db_path: Path, session_id: str) -> None:
    """Insert a 'sent' parent message so the session exists in history."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, sent, created_at, updated_at) "
            "VALUES (?, ?, 'user', '[]', 1, '2026-01-01', '2026-01-01')",
            (f"seed-{session_id}", session_id),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# AC3 — persist_pending: sent=0 + monotonic pending_seq under concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_pending_writes_sent_0(pending, db_path: Path):
    msg = await pending.persist_pending(
        "sess-a", user_message="hello", content=None, agent_id="agent-1"
    )
    assert msg.session_id == "sess-a"
    assert msg.user_message == "hello"
    assert msg.pending_seq == 1  # first pending for this session

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT sent, pending_seq, claimed_at FROM messages WHERE id = ?",
            (msg.id,),
        )
        row = await cursor.fetchone()
    assert row == (0, 1, None), f"expected (sent=0, seq=1, claimed_at=None), got {row}"


@pytest.mark.asyncio
async def test_persist_pending_monotonic_under_concurrency(pending):
    """AC3: 5 concurrent persists to the SAME session get distinct, contiguous
    pending_seq values (per-session seq lock serializes assignment)."""
    results = await asyncio.gather(*[
        pending.persist_pending("sess-c", user_message=f"m{i}", content=None, agent_id="a")
        for i in range(5)
    ])
    seqs = sorted(r.pending_seq for r in results)
    assert seqs == [1, 2, 3, 4, 5], f"expected contiguous 1..5, got {seqs}"


@pytest.mark.asyncio
async def test_count_pending(pending):
    assert await pending.count_pending("sess-cnt") == 0
    await pending.persist_pending("sess-cnt", user_message="a", content=None, agent_id="a")
    await pending.persist_pending("sess-cnt", user_message="b", content=None, agent_id="a")
    assert await pending.count_pending("sess-cnt") == 2


# ---------------------------------------------------------------------------
# Phase 2 L2 — mark_pending_by_id: convert an already-persisted live row to
# pending (used by send() on SESSION_BUSY/QUEUE_TIMEOUT instead of deleting it).
# The row already exists with its client_id metadata; we flip it sent=0 and
# assign a monotonic pending_seq, preserving id + metadata (no re-insert, so
# no double FTS trigger and the frontend dedup key survives).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_pending_by_id_converts_existing_row(pending, db_path: Path):
    """An existing sent=1 row flips to sent=0 with a fresh monotonic seq;
    id and metadata are preserved (R1 client_id dedup)."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, metadata, sent, created_at, updated_at) "
            "VALUES ('live-1', 'sess-mp', 'user', '[{\"type\":\"text\",\"text\":\"hi\"}]', "
            "'{\"client_id\": \"local-42\"}', 1, '2026-01-01', '2026-01-01')",
        )
        await conn.commit()

    seq = await pending.mark_pending_by_id("sess-mp", "live-1")
    assert seq == 1  # first pending for this session

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT sent, pending_seq, claimed_at, metadata FROM messages WHERE id = 'live-1'"
        )
        row = await cursor.fetchone()
    assert row[0] == 0          # sent flipped to pending
    assert row[1] == 1          # monotonic seq assigned
    assert row[2] is None       # not claimed
    assert "local-42" in row[3]  # client_id metadata preserved

    # And it is now visible to the pending pipeline.
    assert await pending.count_pending("sess-mp") == 1


@pytest.mark.asyncio
async def test_mark_pending_by_id_monotonic_after_existing_pending(pending, db_path: Path):
    """A converted row gets MAX(pending_seq)+1, coexisting with prior pendings."""
    await pending.persist_pending("sess-mp2", user_message="first", content=None, agent_id="a")
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, sent, created_at, updated_at) "
            "VALUES ('live-2', 'sess-mp2', 'user', '[]', 1, '2026-01-02', '2026-01-02')",
        )
        await conn.commit()

    seq = await pending.mark_pending_by_id("sess-mp2", "live-2")
    assert seq == 2  # after the existing seq=1 pending


@pytest.mark.asyncio
async def test_mark_pending_by_id_missing_row_returns_none(pending):
    """No matching row → returns None, no crash (defensive)."""
    seq = await pending.mark_pending_by_id("sess-none", "does-not-exist")
    assert seq is None


# ---------------------------------------------------------------------------
# AC4 — three-phase lifecycle: claim → mark_sent / rollback, exactly-once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_peek_returns_fifo_order(pending):
    await pending.persist_pending("sess-p", user_message="first", content=None, agent_id="a")
    await pending.persist_pending("sess-p", user_message="second", content=None, agent_id="a")
    rows = await pending.peek_pending_batch("sess-p")
    assert [r.user_message for r in rows] == ["first", "second"]


@pytest.mark.asyncio
async def test_claim_then_mark_sent(pending, db_path: Path):
    await pending.persist_pending("sess-m", user_message="x", content=None, agent_id="a")
    claimed = await pending.claim_pending_batch("sess-m")
    assert len(claimed) == 1

    # claimed phase: sent still 0, claimed_at set
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT sent, claimed_at FROM messages WHERE session_id='sess-m'")
        sent, claimed_at = await cur.fetchone()
    assert sent == 0 and claimed_at is not None

    await pending.mark_sent_batch("sess-m", [c.pending_seq for c in claimed])
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT sent FROM messages WHERE session_id='sess-m'")
        assert (await cur.fetchone())[0] == 1
    assert await pending.count_pending("sess-m") == 0


@pytest.mark.asyncio
async def test_rollback_claim_returns_to_pending(pending, db_path: Path):
    await pending.persist_pending("sess-r", user_message="x", content=None, agent_id="a")
    claimed = await pending.claim_pending_batch("sess-r")
    await pending.rollback_claim_batch("sess-r", [c.pending_seq for c in claimed])

    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT sent, claimed_at FROM messages WHERE session_id='sess-r'")
        sent, claimed_at = await cur.fetchone()
    assert sent == 0 and claimed_at is None, "rollback must restore pending (sent=0, claimed_at=NULL)"
    assert await pending.count_pending("sess-r") == 1


@pytest.mark.asyncio
async def test_claim_is_exactly_once_under_concurrency(pending):
    """AC4: two concurrent claim_pending_batch on the same session — exactly one
    gets the rows, the other gets an empty set (no double-drain)."""
    await pending.persist_pending("sess-x", user_message="a", content=None, agent_id="a")
    await pending.persist_pending("sess-x", user_message="b", content=None, agent_id="a")

    r1, r2 = await asyncio.gather(
        pending.claim_pending_batch("sess-x"),
        pending.claim_pending_batch("sess-x"),
    )
    sizes = sorted([len(r1), len(r2)])
    assert sizes == [0, 2], f"exactly one claim wins the whole set, got sizes {sizes}"


# ---------------------------------------------------------------------------
# AC5 — combine_pending: FIFO coalesce
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combine_pending_text_fifo_latest_last(pending):
    await pending.persist_pending("sess-t", user_message="first", content=None, agent_id="a")
    await pending.persist_pending("sess-t", user_message="second", content=None, agent_id="a")
    rows = await pending.peek_pending_batch("sess-t")
    text, content = pending.combine_pending(rows)
    assert content is None
    assert text == "first\n\nsecond", f"got {text!r}"


@pytest.mark.asyncio
async def test_combine_pending_multimodal_concatenates_blocks(pending):
    await pending.persist_pending(
        "sess-mm", user_message=None,
        content=[{"type": "text", "text": "a"}], agent_id="a",
    )
    await pending.persist_pending(
        "sess-mm", user_message=None,
        content=[{"type": "image", "source": {"x": 1}}], agent_id="a",
    )
    rows = await pending.peek_pending_batch("sess-mm")
    text, content = pending.combine_pending(rows)
    assert content == [
        {"type": "text", "text": "a"},
        {"type": "image", "source": {"x": 1}},
    ], f"got {content!r}"


# ---------------------------------------------------------------------------
# AC6 — reopen_dangling_claims: crash-window recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reopen_dangling_claims(pending, db_path: Path):
    """AC6: a row stuck in 'claimed' (claimed_at set, sent=0) by a crash is
    reopened to pending (claimed_at=NULL)."""
    await pending.persist_pending("sess-d", user_message="x", content=None, agent_id="a")
    await pending.claim_pending_batch("sess-d")  # now claimed, sent=0

    reopened = await pending.reopen_dangling_claims()
    assert reopened >= 1

    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT sent, claimed_at FROM messages WHERE session_id='sess-d'")
        sent, claimed_at = await cur.fetchone()
    assert sent == 0 and claimed_at is None
    assert await pending.count_pending("sess-d") == 1


@pytest.mark.asyncio
async def test_reopen_does_not_touch_sent_rows(pending, db_path: Path):
    """reopen must NOT resurrect already-sent rows."""
    await pending.persist_pending("sess-s", user_message="x", content=None, agent_id="a")
    claimed = await pending.claim_pending_batch("sess-s")
    await pending.mark_sent_batch("sess-s", [c.pending_seq for c in claimed])

    await pending.reopen_dangling_claims()
    assert await pending.count_pending("sess-s") == 0, "sent rows must not be reopened"


# ---------------------------------------------------------------------------
# Adversarial-review hardening (round 1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_row_to_pending_survives_empty_and_nondict_payloads(pending, db_path: Path):
    """CORR-MED: _row_to_pending must not crash on '[]' (legacy/seed shape) or a
    non-dict first element — these must surface as degenerate rows, not propagate
    IndexError/AttributeError out of peek/claim."""
    # Insert raw pending rows with adversarial content payloads.
    async with aiosqlite.connect(str(db_path)) as conn:
        for i, payload in enumerate(["[]", '["just a string"]', "[123]", "not json"]):
            await conn.execute(
                "INSERT INTO messages (id, session_id, role, content, sent, "
                "pending_seq, created_at, updated_at) "
                "VALUES (?, 'sess-bad', 'user', ?, 0, ?, '2026-01-01', '2026-01-01')",
                (f"bad-{i}", payload, i + 1),
            )
        await conn.commit()
    # Must not raise.
    rows = await pending.peek_pending_batch("sess-bad")
    assert len(rows) == 4
    claimed = await pending.claim_pending_batch("sess-bad")
    assert len(claimed) == 4


@pytest.mark.asyncio
async def test_mark_sent_ignores_stale_unclaimed_seq(pending, db_path: Path):
    """CONC/CORR: mark_sent_batch must NO-OP on a seq that is not currently
    claimed (e.g. rolled back / reopened) — never mark an undelivered row sent."""
    await pending.persist_pending("sess-stale", user_message="x", content=None, agent_id="a")
    # Row is pending (claimed_at=NULL). Try to mark it sent WITHOUT claiming.
    await pending.mark_sent_batch("sess-stale", [1])
    # Must remain pending — the claimed_at guard blocked the flip.
    assert await pending.count_pending("sess-stale") == 1


@pytest.mark.asyncio
async def test_persist_threads_client_id_into_metadata(pending, db_path: Path):
    """R1 (cross-track BLOCKER): client_id must land in metadata.client_id exactly
    as the live send path does, or Phase-2 drain can't dedup the optimistic bubble
    → duplicate user message. No client_id → metadata stays '{}'."""
    msg = await pending.persist_pending(
        "sess-cid", user_message="hi", content=None, agent_id="a", client_id="local-123"
    )
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT metadata FROM messages WHERE id=?", (msg.id,))
        meta = (await cur.fetchone())[0]
    import json as _json
    assert _json.loads(meta) == {"client_id": "local-123"}, f"got {meta!r}"

    # No client_id → empty metadata (no spurious null client_id key).
    msg2 = await pending.persist_pending(
        "sess-cid2", user_message="hi", content=None, agent_id="a"
    )
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT metadata FROM messages WHERE id=?", (msg2.id,))
        assert (await cur.fetchone())[0] == "{}"


@pytest.mark.asyncio
async def test_persist_sets_expires_at(pending, db_path: Path):
    """OP: pending rows must carry expires_at so a drained row is eventually
    TTL-reaped (no permanent leak once delivered)."""
    msg = await pending.persist_pending("sess-exp", user_message="x", content=None, agent_id="a")
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT expires_at FROM messages WHERE id=?", (msg.id,))
        expires_at = (await cur.fetchone())[0]
    assert expires_at is not None and expires_at > 0


@pytest.mark.asyncio
async def test_ttl_cleanup_skips_pending_rows(pending, db_path: Path):
    """OP-3 (F4 durability): cleanup_expired must NOT delete an undelivered
    pending row even if its expires_at is in the past — only sent/legacy rows."""
    from database.sqlite import SQLiteMessagesTable
    # Pending row with a PAST expires_at (simulate a long-orphaned pending msg).
    await pending.persist_pending("sess-ttl", user_message="keep-me", content=None, agent_id="a")
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "UPDATE messages SET expires_at = 1 WHERE session_id='sess-ttl'"
        )
        # Also a delivered (sent=1) expired row that SHOULD be reaped.
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, sent, "
            "expires_at, created_at, updated_at) "
            "VALUES ('sent-old', 'sess-ttl', 'user', '[]', 1, 1, '2026-01-01', '2026-01-01')"
        )
        await conn.commit()

    table = SQLiteMessagesTable(table_name="messages", db_path=db_path)
    await table.cleanup_expired()

    # Pending row preserved; delivered expired row deleted.
    assert await pending.count_pending("sess-ttl") == 1, "pending row must survive TTL"
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM messages WHERE id='sent-old'")
        assert (await cur.fetchone())[0] == 0, "delivered expired row should be reaped"


@pytest.mark.asyncio
async def test_with_retry_recovers_then_propagates():
    """CONC-HIGH (STEERING #11): the retry wrapper must actually EXECUTE its
    retry path — recover from transient lock/busy, but propagate non-transient
    errors immediately and raise after exhausting retries."""
    import core.session_pending as sp

    # (a) transient → recovers
    calls = {"n": 0}
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise Exception("database is locked")
        return "ok"
    assert await sp._with_retry(flaky) == "ok"
    assert calls["n"] == 3  # proves retries ran, not just the happy path

    # (b) non-transient → immediate propagate (no retries)
    nt = {"n": 0}
    async def hard():
        nt["n"] += 1
        raise ValueError("not a lock error")
    with pytest.raises(ValueError):
        await sp._with_retry(hard)
    assert nt["n"] == 1  # did NOT retry a non-transient error

    # (c) transient exhausted → raises
    async def always():
        raise Exception("busy")
    with pytest.raises(Exception, match="busy"):
        await sp._with_retry(always)


@pytest.mark.asyncio
async def test_unique_pending_seq_index_blocks_duplicate(pending, db_path: Path):
    """CONC-LOW: a cross-process duplicate (session_id, pending_seq) must fail
    loudly via the UNIQUE partial index, not silently duplicate the coalesce key."""
    await pending.persist_pending("sess-uniq", user_message="a", content=None, agent_id="a")
    async with aiosqlite.connect(str(db_path)) as conn:
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO messages (id, session_id, role, content, sent, "
                "pending_seq, created_at, updated_at) "
                "VALUES ('dup', 'sess-uniq', 'user', '[]', 0, 1, '2026-01-01', '2026-01-01')"
            )
            await conn.commit()

