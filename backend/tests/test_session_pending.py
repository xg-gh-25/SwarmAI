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

from database.sqlite import SQLiteDatabase


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
    user_version == 6."""
    await _make_migrated_db(db_path)

    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute("PRAGMA user_version")
        version = (await cursor.fetchone())[0]
        assert version == 6, f"expected user_version=6, got {version}"

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
