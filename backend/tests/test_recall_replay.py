"""Tests for ``scripts/recall_replay.py`` — the read-only recall replay tool.

What is tested (on a tiny synthetic data.db in tmp_path, never the real one):
- every file DB opened through the tool's connect wrapper is read-only;
- while a message is replayed, ``messages`` only shows rows created before it;
- a cutoff that is not an ISO timestamp is refused (it is spliced into SQL);
- the counterfactual dedups as production does: against the system prompt, the
  first query's kept block (its DDD block included) and the recall history.
"""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recall_replay.py"
_spec = importlib.util.spec_from_file_location("recall_replay", _PATH)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?)", [
        ("s1", "user", "old", "2026-10-01T10:00:00.000001"),
        ("s2", "user", "new", "2026-10-20T10:00:00.000001"),
    ])
    conn.commit()
    conn.close()
    yield path
    rr._CUTOFF.ts = None


def test_connections_are_read_only(db):
    conn = rr._ro_connect(str(db))
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO messages VALUES ('x','user','x','2026-10-02T00:00:00')")
    conn.close()


def test_cutoff_hides_later_messages(db):
    rr._CUTOFF.ts = "2026-10-10T00:00:00.000000"
    conn = rr._ro_connect(str(db))
    rows = conn.execute("SELECT m.rowid, content FROM messages m").fetchall()
    conn.close()
    assert [r[1] for r in rows] == ["old"]
    assert rows[0][0] == 1          # rowid survives the view (the FTS join needs it)


def test_cutoff_must_be_iso(db):
    rr._CUTOFF.ts = "2026-10-10'; DROP TABLE messages; --"
    with pytest.raises(ValueError):
        rr._ro_connect(str(db))


def test_counterfactual_dedups_against_the_first_querys_block(monkeypatch):
    """A follow-up recall in production skips text the first query already carried
    (the kept session-context block, with turn 0's DDD block). The replay's
    counterfactual must see the same text."""
    from unittest.mock import MagicMock
    from core import session_router as sr

    monkeypatch.setattr(rr, "_ro_connect", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(rr, "_tool_events", lambda *_a, **_k: [])
    calls = {"n": 0}

    async def fake_recall(msg, opts, unit, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            sr._deliver_block(unit, "\n\n## Project DDD — [DDD:X]\nTURN0-DDD-TEXT")
    seen = []

    def fake_unified(keywords, project, seen_text, *a, **k):
        seen.append(seen_text)
        return "", None
    monkeypatch.setattr(sr, "_maybe_inject_recall", fake_recall)
    monkeypatch.setattr(sr, "_unified_recall_body", fake_unified)
    msgs = [("2026-10-20T10:00:00", "explain the pipeline worktree isolation"),
            ("2026-10-20T10:05:00", "now the recall budget and ranking please")]
    rr._replay_session(("sess-x", msgs, "SYSTEM PROMPT", True))
    rr._CUTOFF.ts = None
    assert seen and all(t.startswith("SYSTEM PROMPT") for t in seen)
    assert "TURN0-DDD-TEXT" in seen[-1]
