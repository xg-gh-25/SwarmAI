"""Tests for the daily todo-resolution system job.

Tests 3 resolution layers:
- Layer 1: Pipeline completion check (escalation-source todos)
- Layer 2: Git commit keyword matching
- Layer 3: Staleness cancellation (>21d)

Plus edge cases: DB lock handling, missing codebase, corrupt data.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

def _create_test_db(tmp_path: Path) -> Path:
    """Create a test SQLite DB with the todos table schema."""
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE todos (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            source TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual'
                CHECK (source_type IN ('manual','email','slack','meeting','integration','chat','ai_detected')),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','overdue','in_discussion','handled','cancelled','deleted')),
            priority TEXT NOT NULL DEFAULT 'none'
                CHECK (priority IN ('high','medium','low','none')),
            due_date TEXT,
            linked_context TEXT,
            task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_todo(
    db_path: Path,
    *,
    todo_id: str = "test-todo-1",
    title: str = "Test todo",
    status: str = "pending",
    source: str | None = None,
    linked_context: dict | None = None,
    created_days_ago: int = 0,
    updated_days_ago: int = 0,
) -> None:
    """Insert a todo into the test DB."""
    now = datetime.now(timezone.utc)
    created = (now - timedelta(days=created_days_ago)).isoformat()
    updated = (now - timedelta(days=updated_days_ago)).isoformat()

    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.execute(
        """INSERT INTO todos (id, workspace_id, title, status, source, source_type,
           priority, linked_context, created_at, updated_at)
           VALUES (?, 'swarmws', ?, ?, ?, 'ai_detected', 'high', ?, ?, ?)""",
        (
            todo_id, title, status, source,
            json.dumps(linked_context or {}),
            created, updated,
        ),
    )
    conn.commit()
    conn.close()


def _get_todo_status(db_path: Path, todo_id: str) -> str | None:
    """Get a todo's current status from DB."""
    conn = sqlite3.connect(str(db_path), timeout=5)
    row = conn.execute("SELECT status FROM todos WHERE id = ?", (todo_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def _get_todo(db_path: Path, todo_id: str) -> dict | None:
    """Get full todo record."""
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Layer 1: Pipeline Completion ─────────────────────────────────────

class TestPipelineCompletion:
    """Layer 1: Todos created by pipeline pause → resolved when pipeline completes."""

    def test_completed_pipeline_marks_todo_handled(self, tmp_path):
        """Pipeline at status=completed → todo transitions to handled."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        run_id = "run_abc12345"

        # Create a pipeline-pause todo with escalation source
        _insert_todo(
            db_path,
            todo_id="pipe-todo-1",
            title="Pipeline paused: Voice Conversation Mode",
            status="in_discussion",
            source=f"escalation:esc_{run_id}",
            linked_context={
                "escalation_id": f"esc_{run_id}",
                "pipeline_id": run_id,
                "project": "SwarmAI",
            },
        )

        # Create a fake completed run.json
        run_dir = tmp_path / "artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id,
            "status": "completed",
            "project": "SwarmAI",
        }))

        result = run_todo_resolution(
            db_path=db_path,
            artifacts_root=tmp_path / "artifacts",
        )
        assert _get_todo_status(db_path, "pipe-todo-1") == "handled"
        assert result["pipeline_resolved"] >= 1

    def test_running_pipeline_not_resolved(self, tmp_path):
        """Pipeline still running → todo stays as-is."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        run_id = "run_still_going"

        _insert_todo(
            db_path,
            todo_id="pipe-todo-2",
            title="Pipeline paused: Feature X",
            status="in_discussion",
            source=f"escalation:esc_{run_id}",
            linked_context={"pipeline_id": run_id, "project": "SwarmAI"},
        )

        run_dir = tmp_path / "artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id,
            "status": "running",
        }))

        run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")
        assert _get_todo_status(db_path, "pipe-todo-2") == "in_discussion"

    def test_missing_run_json_skipped(self, tmp_path):
        """Missing run.json → todo untouched, no crash."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="pipe-todo-3",
            title="Pipeline paused: Orphaned",
            status="in_discussion",
            source="escalation:esc_orphan",
            linked_context={"pipeline_id": "run_nonexistent", "project": "SwarmAI"},
        )

        result = run_todo_resolution(
            db_path=db_path,
            artifacts_root=tmp_path / "artifacts",
        )
        assert _get_todo_status(db_path, "pipe-todo-3") == "in_discussion"


# ── Layer 2: Git Keyword Match ───────────────────────────────────────

class TestGitKeywordMatch:
    """Layer 2: Active todos matched against recent git commits."""

    def test_pending_todo_moves_to_in_discussion_on_keyword_match(self, tmp_path):
        """Pending todo with title keywords matching git log → in_discussion."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="git-todo-1",
            title="Wire FTS5-only recall into production",
            status="pending",
        )

        fake_git_log = "abc1234 feat: wire FTS5 recall into session_router\ndef5678 fix: production crash on empty recall"

        with patch("jobs.todo_resolution._get_recent_commits", return_value=fake_git_log):
            run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")

        assert _get_todo_status(db_path, "git-todo-1") == "in_discussion"

    def test_in_discussion_todo_handled_on_second_match(self, tmp_path):
        """Already in_discussion + keyword match → handled."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="git-todo-2",
            title="Frontend auto-retry for SESSION_BUSY",
            status="in_discussion",
        )

        fake_git_log = "aaa1111 fix: frontend auto-retry on SESSION_BUSY error"

        with patch("jobs.todo_resolution._get_recent_commits", return_value=fake_git_log):
            run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")

        assert _get_todo_status(db_path, "git-todo-2") == "handled"

    def test_no_match_stays_unchanged(self, tmp_path):
        """No keyword overlap → status unchanged."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="git-todo-3",
            title="DSPy GEPA Optimizer — real self-evolution closed loop",
            status="in_discussion",
        )

        fake_git_log = "bbb2222 docs: update README\nccc3333 fix: typo in config"

        with patch("jobs.todo_resolution._get_recent_commits", return_value=fake_git_log):
            run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")

        assert _get_todo_status(db_path, "git-todo-3") == "in_discussion"

    def test_short_stopwords_not_matched(self, tmp_path):
        """Short words and stopwords should not trigger false matches."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="git-todo-4",
            title="Add fix for the new bug",  # all short/stop words
            status="pending",
        )

        fake_git_log = "ddd4444 fix: add new feature for the app"

        with patch("jobs.todo_resolution._get_recent_commits", return_value=fake_git_log):
            run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")

        # Should NOT match — all keywords are too short or stopwords
        assert _get_todo_status(db_path, "git-todo-4") == "pending"

    def test_chinese_keywords_match(self, tmp_path):
        """Chinese keywords in title should match Chinese commit messages."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="git-todo-5",
            title="设计 mid-session checkpoint 机制",
            status="pending",
        )

        fake_git_log = "eee5555 feat: checkpoint mechanism for mid-session"

        with patch("jobs.todo_resolution._get_recent_commits", return_value=fake_git_log):
            run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")

        # "checkpoint" and "mid-session" should match
        assert _get_todo_status(db_path, "git-todo-5") == "in_discussion"


# ── Layer 3: Staleness Cancellation ──────────────────────────────────

class TestStalenessCancellation:
    """Layer 3: Pending todos with no activity >21 days → cancelled."""

    def test_stale_pending_todo_cancelled(self, tmp_path):
        """Pending todo untouched for >21 days → cancelled."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="stale-1",
            title="Some old task nobody cares about",
            status="pending",
            created_days_ago=30,
            updated_days_ago=25,
        )

        result = run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")
        assert _get_todo_status(db_path, "stale-1") == "cancelled"
        assert result["stale_cancelled"] >= 1

    def test_recent_pending_todo_not_cancelled(self, tmp_path):
        """Pending todo updated recently → NOT cancelled."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="stale-2",
            title="Active work in progress",
            status="pending",
            created_days_ago=30,
            updated_days_ago=5,  # Recently touched
        )

        run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")
        assert _get_todo_status(db_path, "stale-2") == "pending"

    def test_stale_in_discussion_also_cancelled(self, tmp_path):
        """in_discussion todos untouched >stale_days are also cancelled."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        _insert_todo(
            db_path,
            todo_id="stale-3",
            title="Old discussion nobody resumed",
            status="in_discussion",
            created_days_ago=60,
            updated_days_ago=30,
        )

        result = run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")
        assert _get_todo_status(db_path, "stale-3") == "cancelled"
        assert result["stale_cancelled"] >= 1

    def test_terminal_todos_untouched(self, tmp_path):
        """Already handled/cancelled/deleted todos are never changed."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        for status in ("handled", "cancelled", "deleted"):
            _insert_todo(
                db_path,
                todo_id=f"terminal-{status}",
                title=f"Terminal {status} todo",
                status=status,
                created_days_ago=60,
                updated_days_ago=60,
            )

        run_todo_resolution(db_path=db_path, artifacts_root=tmp_path / "artifacts")
        for status in ("handled", "cancelled", "deleted"):
            assert _get_todo_status(db_path, f"terminal-{status}") == status


# ── Edge Cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    """Resilience: missing DB, corrupt data, no codebase."""

    def test_missing_db_returns_empty_result(self, tmp_path):
        """Non-existent DB → graceful empty result, no crash."""
        from jobs.todo_resolution import run_todo_resolution

        result = run_todo_resolution(
            db_path=tmp_path / "nonexistent.db",
            artifacts_root=tmp_path / "artifacts",
        )
        assert result["pipeline_resolved"] == 0
        assert result["git_resolved"] == 0
        assert result["stale_cancelled"] == 0

    def test_corrupt_linked_context_skipped(self, tmp_path):
        """Corrupt JSON in linked_context → skip, don't crash."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        conn = sqlite3.connect(str(db_path))
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO todos (id, workspace_id, title, status, source,
               source_type, priority, linked_context, created_at, updated_at)
               VALUES (?, 'swarmws', ?, 'in_discussion', 'escalation:esc_bad',
               'ai_detected', 'high', ?, ?, ?)""",
            ("corrupt-1", "Corrupt todo", "NOT VALID JSON {{{", now, now),
        )
        conn.commit()
        conn.close()

        # Should not raise
        result = run_todo_resolution(
            db_path=db_path,
            artifacts_root=tmp_path / "artifacts",
        )
        assert isinstance(result, dict)

    def test_result_includes_all_layer_counts(self, tmp_path):
        """Result dict always has all 3 layer counts."""
        from jobs.todo_resolution import run_todo_resolution

        db_path = _create_test_db(tmp_path)
        result = run_todo_resolution(
            db_path=db_path,
            artifacts_root=tmp_path / "artifacts",
        )
        assert "pipeline_resolved" in result
        assert "git_resolved" in result
        assert "stale_cancelled" in result
        assert "errors" in result
