"""Tests for the 知识 tab's read side: GET /api/recall/knowledge and the two db reads
behind it (database/sqlite.py get_recall_hit_windows, count_message_sessions).

Pins the window math: rows are stamped LOCAL-NAIVE '%Y-%m-%dT%H:%M:%S' by the writer and
compared as strings, this window is [now-7d, now], the one before [now-14d, now-7d), a
run "hits" when hit_count > 0, and a part that cannot be read degrades on its own.
"""
import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 11, 24, 17, 0, 0)
FMT = "%Y-%m-%dT%H:%M:%S"


def _at(**delta) -> str:
    return (NOW - timedelta(**delta)).strftime(FMT)


def _row(context: str, ts: str, hit: int) -> dict:
    return {"context": context, "domains": "x", "latency_ms": 5.0, "hit_count": hit, "timestamp": ts}


@pytest.fixture
def fresh_db(tmp_path):
    from database.sqlite import SQLiteDatabase
    db = SQLiteDatabase(str(tmp_path / "k.db"))
    asyncio.run(db.initialize())
    return db


class TestHitWindows:
    def test_counts_runs_and_hits_per_context_in_both_windows(self, fresh_db):
        asyncio.run(fresh_db.bulk_insert_recall_metrics([
            _row("session_prompt", _at(hours=1), 1),
            _row("session_prompt", _at(days=2), 0),
            _row("session_prompt", _at(days=6, hours=23), 1),
            _row("session_prompt", _at(days=8), 1),       # the week before
            _row("session_prompt", _at(days=13), 0),      # the week before
            _row("session_ddd", _at(days=1), 3),          # ddd records its section count
            _row("session_ddd", _at(days=1), 0),
            _row("session_followup", _at(days=9), 1),
        ]))
        got = asyncio.run(fresh_db.get_recall_hit_windows(7, now=NOW))
        assert got["session_prompt"] == {"runs": 3, "hits": 2, "prev_runs": 2, "prev_hits": 1}
        assert got["session_ddd"] == {"runs": 2, "hits": 1, "prev_runs": 0, "prev_hits": 0}
        assert got["session_followup"] == {"runs": 0, "hits": 0, "prev_runs": 1, "prev_hits": 1}

    def test_window_edges(self, fresh_db):
        asyncio.run(fresh_db.bulk_insert_recall_metrics([
            _row("session_prompt", _at(days=7), 1),            # exactly the cut: this week
            _row("session_prompt", _at(days=7, seconds=1), 1),  # one second older: before
            _row("session_prompt", _at(days=14), 1),           # exactly 14 days: before
            _row("session_prompt", _at(days=14, seconds=1), 1),  # older: not counted
            _row("session_prompt", (NOW + timedelta(seconds=5)).strftime(FMT), 1),  # after now
        ]))
        got = asyncio.run(fresh_db.get_recall_hit_windows(7, now=NOW))
        assert got["session_prompt"] == {"runs": 1, "hits": 1, "prev_runs": 2, "prev_hits": 2}

    def test_empty_table_is_empty(self, fresh_db):
        assert asyncio.run(fresh_db.get_recall_hit_windows(7, now=NOW)) == {}

    def test_unreadable_db_is_empty_not_raise(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "never-initialized.db"))   # no recall_metrics table
        assert asyncio.run(db.get_recall_hit_windows(7, now=NOW)) == {}


class TestMessageSessions:
    def test_counts_distinct_sessions_with_messages(self, fresh_db):
        import aiosqlite

        async def seed():
            async with aiosqlite.connect(str(fresh_db.db_path)) as conn:
                for i, sid in enumerate(["s1", "s1", "s2", "s3", "s3", "s3"]):
                    await conn.execute(
                        "INSERT INTO messages (id, session_id, role, content, created_at, updated_at) "
                        "VALUES (?, ?, 'user', 'x', 't', 't')", (f"m{i}", sid))
                await conn.commit()
        asyncio.run(seed())
        assert asyncio.run(fresh_db.count_message_sessions()) == 3

    def test_unreadable_is_none(self, tmp_path):
        from database.sqlite import SQLiteDatabase
        db = SQLiteDatabase(str(tmp_path / "never-initialized.db"))
        assert asyncio.run(db.count_message_sessions()) is None


@pytest.fixture
def knowledge_client(fresh_db, monkeypatch, tmp_path):
    import database
    import routers.recall_metrics_api as api_mod
    from core.initialization_manager import initialization_manager

    monkeypatch.setattr(database, "db", fresh_db, raising=False)
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    (ws / ".context" / "MEMORY-archive-2026-10.md").write_text(
        "- [pitfall] **a** — x\n- [pitfall] **b** — y\n", encoding="utf-8")
    (ws / ".context" / "EVOLUTION-archive-2026-11.md").write_text(
        "### C001 | 2026-11-01\n- x\n", encoding="utf-8")
    monkeypatch.setattr(initialization_manager, "get_cached_workspace_path", lambda: str(ws))
    monkeypatch.setattr(api_mod, "_project_brains", lambda: {"brains": 2, "entries": 40})
    monkeypatch.setattr(api_mod, "_general_library", lambda: {"files": 7})
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


class TestKnowledgeEndpoint:
    def test_shape_hits_and_stores(self, knowledge_client, fresh_db):
        asyncio.run(fresh_db.bulk_insert_recall_metrics([
            _row("session_prompt", datetime.now().strftime(FMT), 1),
            _row("session_prompt", datetime.now().strftime(FMT), 0),
        ]))
        body = knowledge_client.get("/api/recall/knowledge").json()
        assert body["window_days"] == 7
        assert body["contexts"]["session_prompt"] == {"runs": 2, "hits": 1, "prev_runs": 0, "prev_hits": 0}
        assert body["stores"] == {
            "projects": {"brains": 2, "entries": 40},
            "general": {"files": 7},
            # the same shards and entry counts as /eval/archive-list
            "archives": {"files": 2, "entries": 3},
            "sessions": 0,
        }

    def test_a_store_that_fails_is_null_not_zero(self, knowledge_client, monkeypatch):
        import routers.recall_metrics_api as api_mod

        def boom():
            raise RuntimeError("unreadable")
        # the real helper swallows its own error into None; a raise past it is caught too
        monkeypatch.setattr(api_mod, "_file_stores", lambda ws: boom())
        resp = knowledge_client.get("/api/recall/knowledge")
        assert resp.status_code == 200
        stores = resp.json()["stores"]
        assert stores["projects"] is None and stores["general"] is None and stores["archives"] is None

    def test_no_workspace_has_no_archives(self, knowledge_client, monkeypatch):
        from core.initialization_manager import initialization_manager
        monkeypatch.setattr(initialization_manager, "get_cached_workspace_path", lambda: None)
        assert knowledge_client.get("/api/recall/knowledge").json()["stores"]["archives"] is None


def test_project_brains_counts_as_brain_hub_does(monkeypatch, tmp_path):
    """_project_brains reuses Brain Hub's helpers: every Projects/ child with a
    .project.json, its knowledge entries parsed the Brain Hub way."""
    import routers.ddd_brain as brain
    from routers.recall_metrics_api import _project_brains

    ws = tmp_path / "SwarmWS"
    for name, n in (("A", 2), ("B", 1)):
        d = ws / "Projects" / name
        (d / "2-understanding").mkdir(parents=True)
        (d / ".project.json").write_text("{}")
        (d / "2-understanding" / "TECH.md").write_text(
            "## X\n" + "".join(f"- [guideline] **E{i}** — e\n" for i in range(n)))
    (ws / "Projects" / "not-a-brain").mkdir()
    monkeypatch.setattr(brain, "_workspace_root", lambda: ws)
    got = _project_brains()
    assert got == {"brains": 2, "entries": sum(brain._entry_count(d) for d in brain._list_project_dirs())}
    assert got["entries"] == 3
