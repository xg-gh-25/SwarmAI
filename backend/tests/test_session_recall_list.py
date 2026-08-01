"""Tests for SessionRecall.search_session_list — FTS5 session-content search
returning session-list rows (the History-overlay backend).

Distinct from test_session_recall.py (which covers the recall-tuned ``search()``
that returns context windows). This method powers ``GET /api/search/sessions``:
it FTS-searches message CONTENT, joins ``sessions`` for the display row, filters
unsent drafts, and optionally scopes by workspace.

Key behaviors verified:
- content hit: a query word present in a message body (NOT the session title)
  surfaces that session — proves it is content search, not title search.
- sent filter: an unsent (sent=0) draft never surfaces (P3 phantom guard).
- workspace scope: default (None) is app-wide; a workspace_id filters to it.
- dedup: a session with N matching messages appears ONCE (not N rows).
"""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from core.session_recall import SessionRecall


# ---------------------------------------------------------------------------
# Fixture — messages + sessions + messages_fts (production-shaped)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            user_id TEXT,
            title TEXT,
            status TEXT DEFAULT 'active',
            metadata TEXT DEFAULT '{}',
            work_dir TEXT,
            workspace_id TEXT,
            last_accessed TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content, content=messages, content_rowid=rowid
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
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def recall(db_path: Path) -> SessionRecall:
    return SessionRecall(db_path=db_path)


def _insert_session(db_path: Path, session_id: str, title: str,
                    agent_id: str = "agent-1", workspace_id: str | None = None,
                    last_accessed: str | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    now = last_accessed or datetime.now().isoformat()
    conn.execute(
        "INSERT INTO sessions (id, agent_id, title, workspace_id, last_accessed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, agent_id, title, workspace_id, now, now, now),
    )
    conn.commit()
    conn.close()


def _insert_message(db_path: Path, session_id: str, role: str, content: str,
                    sent: int = 1) -> None:
    conn = sqlite3.connect(str(db_path))
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, updated_at, sent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid4()), session_id, role, content, now, now, sent),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AC3 — content search (the core: title cannot match, content does)
# ---------------------------------------------------------------------------

def test_search_session_list_matches_message_content_not_title(db_path: Path, recall: SessionRecall):
    """A word present only in a message BODY (title does not contain it) must
    surface the session — proving this is content search, not title search."""
    _insert_session(db_path, "sess-1", title="Untitled conversation")
    _insert_message(db_path, "sess-1", "user", "how do I configure kubernetes ingress?")

    rows = recall.search_session_list("kubernetes")

    assert len(rows) == 1
    assert rows[0]["id"] == "sess-1"
    # title does NOT contain the query word — this is the whole point
    assert "kubernetes" not in rows[0]["title"].lower()
    # return shape carries the display fields
    assert rows[0]["agent_id"] == "agent-1"
    assert "last_accessed" in rows[0]
    assert "created_at" in rows[0]


def test_search_session_list_dedups_multiple_matches_per_session(db_path: Path, recall: SessionRecall):
    """A session with several matching messages appears exactly ONCE."""
    _insert_session(db_path, "sess-1", title="Docker chat")
    _insert_message(db_path, "sess-1", "user", "docker compose up")
    _insert_message(db_path, "sess-1", "assistant", "docker networking explained")
    _insert_message(db_path, "sess-1", "user", "docker volumes")

    rows = recall.search_session_list("docker")

    assert [r["id"] for r in rows] == ["sess-1"]


def test_search_session_list_empty_query_returns_empty(recall: SessionRecall):
    """A blank query returns [] (the frontend falls back to the grouped list)."""
    assert recall.search_session_list("   ") == []


# ---------------------------------------------------------------------------
# AC4 — sent filter + workspace scope
# ---------------------------------------------------------------------------

def test_search_session_list_excludes_unsent_drafts(db_path: Path, recall: SessionRecall):
    """An unsent (sent=0) message must never surface its session (P3 guard)."""
    _insert_session(db_path, "sess-draft", title="Draft session")
    _insert_message(db_path, "sess-draft", "user", "topsecret unsent draft", sent=0)

    rows = recall.search_session_list("topsecret")

    assert rows == []


def test_search_session_list_default_scope_is_app_wide(db_path: Path, recall: SessionRecall):
    """No workspace_id → returns sessions from every workspace (matches the
    workspace-blind list_sessions the empty-query fallback uses)."""
    _insert_session(db_path, "sess-a", title="A", workspace_id="ws-1")
    _insert_session(db_path, "sess-b", title="B", workspace_id="ws-2")
    _insert_message(db_path, "sess-a", "user", "elephant in ws-1")
    _insert_message(db_path, "sess-b", "user", "elephant in ws-2")

    rows = recall.search_session_list("elephant")

    assert {r["id"] for r in rows} == {"sess-a", "sess-b"}


def test_search_session_list_workspace_filter_scopes(db_path: Path, recall: SessionRecall):
    """A workspace_id filters results to that workspace only."""
    _insert_session(db_path, "sess-a", title="A", workspace_id="ws-1")
    _insert_session(db_path, "sess-b", title="B", workspace_id="ws-2")
    _insert_message(db_path, "sess-a", "user", "giraffe in ws-1")
    _insert_message(db_path, "sess-b", "user", "giraffe in ws-2")

    rows = recall.search_session_list("giraffe", workspace_id="ws-1")

    assert [r["id"] for r in rows] == ["sess-a"]


# ---------------------------------------------------------------------------
# AC3 (endpoint) — GET /api/search/sessions through the real ASGI stack
# ---------------------------------------------------------------------------

class TestSearchSessionsEndpoint:
    """Drive GET /api/search/sessions through the FastAPI app against the test DB."""

    def test_endpoint_finds_session_by_message_content(self, client):
        """A word in a message body (not the title) surfaces the session, 200,
        response is a ChatSession-shaped list (camelCase via response_model)."""
        import sqlite3
        from uuid import uuid4
        from datetime import datetime
        from database import db

        sid = f"sess-ep-{uuid4().hex[:8]}"
        now = datetime.now().isoformat()
        conn = sqlite3.connect(str(db.db_path))
        conn.execute(
            "INSERT INTO sessions (id, agent_id, title, last_accessed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "agent-ep", "Untitled", now, now, now),
        )
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, updated_at, sent) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (str(uuid4()), sid, "user", "the aardvark migration plan", now, now),
        )
        conn.commit()
        conn.close()

        resp = client.get("/api/search/sessions", params={"query": "aardvark"})
        assert resp.status_code == 200
        data = resp.json()
        ids = [d["id"] for d in data]
        assert sid in ids
        hit = next(d for d in data if d["id"] == sid)
        # response_model=ChatSessionResponse → snake_case last_accessed_at present
        assert "last_accessed_at" in hit
        assert hit["agent_id"] == "agent-ep"

    def test_endpoint_requires_query(self, client):
        """Missing query → rejected (app error-handler maps validation → 400)."""
        resp = client.get("/api/search/sessions")
        assert resp.status_code in (400, 422)
