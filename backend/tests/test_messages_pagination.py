"""Unit tests for SQLiteMessagesTable.list_by_session_paginated().

Tests the cursor-based pagination method added for the app-restart-performance
spec.  Covers the three query modes (both params, limit-only, neither) and
verifies correct ordering, tie-breaking on shared timestamps, and backward
compatibility with the existing list_by_session() method.
"""
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import aiosqlite

from database.sqlite import SQLiteMessagesTable


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a temporary database file path."""
    return tmp_path / "test.db"


@pytest.fixture
def messages_table(db_path: Path) -> SQLiteMessagesTable:
    """Create a SQLiteMessagesTable backed by a temp database."""
    return SQLiteMessagesTable(table_name="messages", db_path=db_path)


async def _init_schema(db_path: Path) -> None:
    """Create the messages table schema for testing."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content TEXT,
                role TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                expires_at INTEGER
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session_created
                ON messages(session_id, created_at)
        """)
        await conn.commit()


async def _insert_message(
    db_path: Path, msg_id: str, session_id: str, created_at: str, content: str = "test"
) -> None:
    """Insert a message directly via SQL for test setup."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, content, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (msg_id, session_id, content, "user", created_at),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_params_returns_all_chronological(db_path, messages_table):
    """Neither limit nor before_id → full fetch, same as list_by_session()."""
    await _init_schema(db_path)
    sid = "sess-1"
    await _insert_message(db_path, "m1", sid, "2024-01-01T00:00:01")
    await _insert_message(db_path, "m2", sid, "2024-01-01T00:00:02")
    await _insert_message(db_path, "m3", sid, "2024-01-01T00:00:03")

    result = await messages_table.list_by_session_paginated(sid)
    assert [r["id"] for r in result] == ["m1", "m2", "m3"]

    # Should match list_by_session exactly
    full = await messages_table.list_by_session(sid)
    assert [r["id"] for r in result] == [r["id"] for r in full]


@pytest.mark.asyncio
async def test_limit_only_returns_most_recent(db_path, messages_table):
    """Limit without before_id → most recent N messages in chronological order."""
    await _init_schema(db_path)
    sid = "sess-1"
    for i in range(1, 6):
        await _insert_message(db_path, f"m{i}", sid, f"2024-01-01T00:00:{i:02d}")

    result = await messages_table.list_by_session_paginated(sid, limit=3)
    # Should return the 3 most recent in chronological order
    assert len(result) == 3
    assert [r["id"] for r in result] == ["m3", "m4", "m5"]


@pytest.mark.asyncio
async def test_limit_greater_than_total(db_path, messages_table):
    """Limit larger than total messages → returns all messages."""
    await _init_schema(db_path)
    sid = "sess-1"
    await _insert_message(db_path, "m1", sid, "2024-01-01T00:00:01")
    await _insert_message(db_path, "m2", sid, "2024-01-01T00:00:02")

    result = await messages_table.list_by_session_paginated(sid, limit=50)
    assert len(result) == 2
    assert [r["id"] for r in result] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_before_id_with_limit(db_path, messages_table):
    """Both before_id and limit → older messages before cursor."""
    await _init_schema(db_path)
    sid = "sess-1"
    for i in range(1, 8):
        await _insert_message(db_path, f"m{i}", sid, f"2024-01-01T00:00:{i:02d}")

    # Get 3 messages before m5
    result = await messages_table.list_by_session_paginated(sid, limit=3, before_id="m5")
    assert len(result) == 3
    # Should be m2, m3, m4 in chronological order
    assert [r["id"] for r in result] == ["m2", "m3", "m4"]


@pytest.mark.asyncio
async def test_before_id_with_limit_at_start(db_path, messages_table):
    """Cursor near the start → returns fewer than limit messages."""
    await _init_schema(db_path)
    sid = "sess-1"
    for i in range(1, 6):
        await _insert_message(db_path, f"m{i}", sid, f"2024-01-01T00:00:{i:02d}")

    # Get 10 messages before m3 — only m1, m2 exist before it
    result = await messages_table.list_by_session_paginated(sid, limit=10, before_id="m3")
    assert len(result) == 2
    assert [r["id"] for r in result] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_shared_timestamps_tiebreak(db_path, messages_table):
    """Messages with identical created_at are ordered by rowid for tie-breaking."""
    await _init_schema(db_path)
    sid = "sess-1"
    same_ts = "2024-01-01T00:00:01"
    # Insert 5 messages with the same timestamp — rowid determines order
    for i in range(1, 6):
        await _insert_message(db_path, f"m{i}", sid, same_ts)

    # Limit=3 should return the 3 most recent by rowid
    result = await messages_table.list_by_session_paginated(sid, limit=3)
    assert len(result) == 3
    assert [r["id"] for r in result] == ["m3", "m4", "m5"]

    # before_id=m4 with limit=2 should return m2, m3
    result2 = await messages_table.list_by_session_paginated(sid, limit=2, before_id="m4")
    assert len(result2) == 2
    assert [r["id"] for r in result2] == ["m2", "m3"]


@pytest.mark.asyncio
async def test_nonexistent_before_id_returns_empty(db_path, messages_table):
    """A before_id that doesn't exist → empty result (subquery returns NULL)."""
    await _init_schema(db_path)
    sid = "sess-1"
    await _insert_message(db_path, "m1", sid, "2024-01-01T00:00:01")

    result = await messages_table.list_by_session_paginated(sid, limit=10, before_id="nonexistent")
    assert result == []


@pytest.mark.asyncio
async def test_empty_session(db_path, messages_table):
    """Empty session → returns empty list for all modes."""
    await _init_schema(db_path)
    sid = "sess-empty"

    assert await messages_table.list_by_session_paginated(sid) == []
    assert await messages_table.list_by_session_paginated(sid, limit=10) == []
    assert await messages_table.list_by_session_paginated(sid, limit=10, before_id="x") == []
