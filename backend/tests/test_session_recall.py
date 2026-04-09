"""Tests for SessionRecall — FTS5-based session search and recall.

Key public symbols tested:
- ``SessionRecall``  — Search + recall engine
- ``SessionMatch``   — Per-session match result
- ``RecallResult``   — Overall search result
"""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from core.session_recall import SessionRecall, SessionMatch, RecallResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a temp SQLite DB with messages table matching production schema."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
    # Create FTS5 virtual table + sync triggers (matching production migration)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            content=messages,
            content_rowid=rowid
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
            INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
        END
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def recall(db_path: Path) -> SessionRecall:
    return SessionRecall(db_path=db_path)


def _insert_message(db_path: Path, session_id: str, role: str, content: str,
                     created_at: str | None = None) -> None:
    """Helper to insert a message directly."""
    conn = sqlite3.connect(str(db_path))
    now = created_at or datetime.now().isoformat()
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid4()), session_id, role, content, now, now),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# FTS5 setup
# ---------------------------------------------------------------------------

def test_fts5_table_created(db_path: Path):
    """FTS5 virtual table should exist (created by fixture, mirroring DB migration).

    SessionRecall only *verifies* the table exists — it does not create it.
    """
    recall = SessionRecall(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_fts5_auto_sync(db_path: Path):
    """New message inserted via trigger should be searchable immediately."""
    recall = SessionRecall(db_path=db_path)
    _insert_message(db_path, "sess-sync", "user", "kubernetes deployment strategy")
    result = recall.search("kubernetes")
    assert result.total_matches >= 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_returns_matches(db_path: Path, recall: SessionRecall):
    """Insert messages, search, verify results."""
    _insert_message(db_path, "sess-1", "user", "How do I configure pytest fixtures?")
    _insert_message(db_path, "sess-1", "assistant", "Use @pytest.fixture decorator")
    _insert_message(db_path, "sess-1", "user", "Thanks that works for pytest")

    result = recall.search("pytest")
    assert result.total_matches >= 2
    assert len(result.sessions) >= 1
    assert result.sessions[0].session_id == "sess-1"


def test_search_groups_by_session(db_path: Path, recall: SessionRecall):
    """Matches from 2 sessions grouped correctly."""
    _insert_message(db_path, "sess-a", "user", "terraform apply failed")
    _insert_message(db_path, "sess-b", "user", "terraform plan looks good")

    result = recall.search("terraform")
    session_ids = {s.session_id for s in result.sessions}
    assert "sess-a" in session_ids
    assert "sess-b" in session_ids


def test_search_window_context(db_path: Path, recall: SessionRecall):
    """Context messages around match should be included."""
    # Insert 25 messages, match should be in the middle
    for i in range(25):
        content = f"message number {i}" if i != 12 else "special kubernetes topic"
        _insert_message(db_path, "sess-ctx", "user", content,
                       created_at=f"2026-04-08T10:{i:02d}:00")

    result = recall.search("kubernetes")
    assert result.total_matches >= 1
    sess = result.sessions[0]
    # Should have context messages (up to ±10)
    assert len(sess.key_messages) > 1


def test_search_no_matches(recall: SessionRecall):
    """Query with no results should return empty."""
    result = recall.search("nonexistent_query_xyz_12345")
    assert result.total_matches == 0
    assert result.sessions == []


def test_max_sessions_limit(db_path: Path, recall: SessionRecall):
    """Only top N sessions returned."""
    for i in range(5):
        _insert_message(db_path, f"sess-limit-{i}", "user", "docker container management")

    result = recall.search("docker", max_sessions=2)
    assert len(result.sessions) <= 2


# ---------------------------------------------------------------------------
# recall_about formatting
# ---------------------------------------------------------------------------

def test_recall_about_format(db_path: Path, recall: SessionRecall):
    """Returns readable markdown with session headers."""
    _insert_message(db_path, "sess-fmt", "user", "How to debug pytest failures?",
                   created_at="2026-04-08T10:00:00")
    _insert_message(db_path, "sess-fmt", "assistant", "Use pytest -v for verbose output",
                   created_at="2026-04-08T10:01:00")

    text = recall.recall_about("pytest")
    assert "## Session Recall:" in text
    assert "sess-fmt" in text
    assert "pytest" in text.lower()


def test_recall_about_empty(recall: SessionRecall):
    """No matches should return empty string."""
    text = recall.recall_about("nonexistent_xyz_99999")
    assert text == ""
