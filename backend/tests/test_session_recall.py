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
            updated_at TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 1,
            pending_seq INTEGER,
            claimed_at TEXT
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
                     created_at: str | None = None, sent: int = 1) -> None:
    """Helper to insert a message directly.

    ``sent`` defaults to 1 (delivered). Pass ``sent=0`` to simulate an
    unsent pending message (Root-1 SSOT Phase 2) — these must NEVER surface
    in recall (P3 phantom-injection guard).
    """
    conn = sqlite3.connect(str(db_path))
    now = created_at or datetime.now().isoformat()
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, updated_at, sent) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), session_id, role, content, now, now, sent),
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


def test_multiword_query_recalls_via_or(db_path: Path, recall: SessionRecall):
    """R3 (real FTS): a multi-word query whose words are SCATTERED across
    messages (never verbatim+adjacent) must still recall via OR-join. The old
    phrase-wrap required all words consecutive → 0 matches; OR matches any term.
    """
    _insert_message(db_path, "sess-r3", "user", "the deploy pipeline crashed")
    _insert_message(db_path, "sess-r3", "assistant", "check the goal cycle config")
    _insert_message(db_path, "sess-r3", "user", "recall was empty afterwards")

    # No single message contains "pipeline goal cycle recall" as a phrase.
    result = recall.search("pipeline goal cycle recall")
    assert result.total_matches >= 1, "OR-join must recall scattered terms (was 0 under phrase-wrap)"
    assert result.sessions[0].session_id == "sess-r3"


def test_multiword_phrase_only_no_longer_required(db_path: Path, recall: SessionRecall):
    """Counterpart proving the fix is the discriminator: a 3-word query where
    the words live in DIFFERENT messages returns matches (OR), whereas an exact
    phrase that appears NOWHERE still returns 0 (so it's not matching everything).
    """
    _insert_message(db_path, "sess-r3b", "user", "alpha configuration")
    _insert_message(db_path, "sess-r3b", "assistant", "beta deployment")

    assert recall.search("alpha beta").total_matches >= 1  # scattered terms → OR hit
    assert recall.search("zzzznope qqqnope").total_matches == 0  # genuinely absent → still 0


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


# ---------------------------------------------------------------------------
# Root-1 SSOT Phase 2 — pending (sent=0) messages must NEVER surface in recall
# ---------------------------------------------------------------------------

def test_search_excludes_unsent_pending(db_path: Path, recall: SessionRecall):
    """BLOCKER (Gate-1 F1): a sent=0 pending message is indexed into FTS by the
    insert trigger, but the recall search JOIN must filter it out — otherwise an
    un-delivered queued message phantom-injects into recall context (P3)."""
    _insert_message(db_path, "sess-pending", "user",
                    "zzzphantom unsent pending message", sent=0)
    result = recall.search("zzzphantom")
    assert result.total_matches == 0
    assert result.sessions == []


def test_search_mixed_sent_and_pending(db_path: Path, recall: SessionRecall):
    """A sent=1 row matches; a sent=0 row with the same term does NOT — and the
    pending row must not appear in the context window either."""
    _insert_message(db_path, "sess-mix", "user",
                    "qqterm delivered message", created_at="2026-04-08T10:00:00", sent=1)
    _insert_message(db_path, "sess-mix", "user",
                    "qqterm pending message", created_at="2026-04-08T10:01:00", sent=0)
    result = recall.search("qqterm")
    assert result.total_matches == 1  # only the sent=1 row
    # the pending row's content must not leak via the context window
    all_content = " ".join(
        m.get("content", "")
        for s in result.sessions
        for m in s.key_messages
    )
    assert "pending message" not in all_content


# ---------------------------------------------------------------------------
# Rank-primary scoring + LIMIT quality-preservation (run_78bd708f)
#
# The FTS query is bounded by _SEARCH_ROW_LIMIT so a broad multi-word query can
# no longer fetchall() tens of thousands of rows (the RECALL DISASTER TIMEOUT
# root cause). For that LIMIT to be QUALITY-PRESERVING, session scoring is
# rank-primary: the surfaced session is the one owning the best-BM25 (min
# fts.rank) message, which `ORDER BY fts.rank LIMIT N` is guaranteed to keep.
# ---------------------------------------------------------------------------

def test_rank_primary_beats_verbose_session(db_path: Path, recall: SessionRecall):
    """A session with ONE highly-relevant hit must outrank a VERBOSE session with
    many mediocre mentions.

    Old density-primary scoring (density*0.4+recency*0.35+richness*0.25) let a
    wordy session with 20 weak mentions beat a session with a single excellent
    BM25 match. rank-primary fixes this: best (min) fts.rank wins.
    """
    # Session A: ONE message, query term repeated many times → very strong BM25.
    _insert_message(db_path, "sess-focused", "assistant",
                    ("kubernetes kubernetes kubernetes kubernetes kubernetes "
                     "kubernetes kubernetes kubernetes deployment guide"),
                    created_at="2026-04-08T10:00:00")
    # Session B: MANY messages each mentioning the term once → high density, weak per-row rank.
    for i in range(20):
        _insert_message(db_path, "sess-verbose", "user",
                        f"a note about kubernetes among many other unrelated topics {i}",
                        created_at="2026-04-08T09:00:00")

    result = recall.search("kubernetes", max_sessions=2)
    assert result.sessions, "expected matches"
    # rank-primary → the focused single-hit session surfaces FIRST.
    assert result.sessions[0].session_id == "sess-focused", (
        f"rank-primary should surface the best-BM25 session first, "
        f"got {result.sessions[0].session_id}"
    )


def test_limit_is_quality_preserving(db_path: Path):
    """rank-primary top session under a SMALL row-limit == top session over the
    full unbounded result set — proving the LIMIT never drops the deciding
    (best-BM25) message.

    Builds a corpus larger than the limit, then compares the top session picked
    with a tiny limit against the top session with no limit.
    """
    import core.session_recall as sr_mod

    # One session owns the single best-BM25 message (term repeated → strong rank);
    # many filler sessions each contribute one weak-rank match so total rows > limit.
    _insert_message(db_path, "sess-best", "assistant",
                    "flywheel flywheel flywheel flywheel flywheel compounding value",
                    created_at="2026-04-08T10:00:00")
    for i in range(60):
        _insert_message(db_path, f"sess-filler-{i}", "user",
                        f"one passing mention of flywheel in row {i}",
                        created_at="2026-04-08T09:00:00")

    recall = SessionRecall(db_path=db_path)

    # Full (unbounded) ground truth: temporarily lift the limit high.
    orig = sr_mod._SEARCH_ROW_LIMIT
    try:
        sr_mod._SEARCH_ROW_LIMIT = 100000
        full_top = recall.search("flywheel", max_sessions=1).sessions[0].session_id
        # Bounded: a limit well below the total match count.
        sr_mod._SEARCH_ROW_LIMIT = 10
        limited_top = recall.search("flywheel", max_sessions=1).sessions[0].session_id
    finally:
        sr_mod._SEARCH_ROW_LIMIT = orig

    assert full_top == "sess-best"
    assert limited_top == full_top, (
        f"LIMIT changed the top session: full={full_top} limited={limited_top} — "
        "rank-primary should keep the best-BM25 session under any limit"
    )
