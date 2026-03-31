"""Tests for ToDo context validation, structured producer protocol, and lifecycle purge.

Pipeline run_1b8e8cde — ToDo System Redesign.
Tests cover:
  1. validate_linked_context() enforcement per source_type
  2. Structured RADAR_TODOS JSON parser in executor
  3. Lifecycle purge: archive + hard delete terminal todos
  4. Overdue auto-cancel escalation
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Part 1: Context Validation ──────────────────────────────────────


class TestValidateLinkedContext:
    """validate_linked_context() enforces required fields per source_type."""

    def test_email_required_fields(self):
        """Email todos require email_subject, email_from, email_date, email_snippet, suggested_action."""
        from schemas.todo import validate_linked_context

        # Complete email context — should pass cleanly
        ctx = {
            "email_subject": "Promote feedback due April 5",
            "email_from": "hr@amazon.com",
            "email_date": "2026-03-30T08:15:00Z",
            "email_snippet": "Please complete your promote feedback by...",
            "suggested_action": "reply",
            "next_step": "Open promote portal",
            "created_by": "job:morning-inbox",
        }
        result = validate_linked_context("email", ctx)
        assert "_missing_fields" not in result

    def test_email_missing_fields_warns(self):
        """Email context missing required fields gets _missing_fields tag."""
        from schemas.todo import validate_linked_context

        ctx = {"next_step": "Do something", "created_by": "job:morning-inbox"}
        result = validate_linked_context("email", ctx)
        assert "_missing_fields" in result
        missing = result["_missing_fields"]
        assert "email_subject" in missing
        assert "email_from" in missing

    def test_slack_required_fields(self):
        """Slack todos require channel_name, sender, message_snippet, thread_url."""
        from schemas.todo import validate_linked_context

        ctx = {
            "channel_name": "#general",
            "sender": "alice",
            "message_snippet": "Can someone review...",
            "thread_url": "https://slack.com/...",
            "next_step": "Reply in thread",
            "created_by": "job:slack-monitor",
        }
        result = validate_linked_context("slack", ctx)
        assert "_missing_fields" not in result

    def test_chat_required_fields(self):
        """Chat todos require session_id, user_intent, next_step."""
        from schemas.todo import validate_linked_context

        ctx = {
            "session_id": "abc123",
            "user_intent": "Build feature X",
            "next_step": "Read design doc",
            "created_by": "agent:session_abc",
        }
        result = validate_linked_context("chat", ctx)
        assert "_missing_fields" not in result

    def test_ai_detected_required_fields(self):
        """AI-detected todos require detection_reason, next_step, files."""
        from schemas.todo import validate_linked_context

        ctx = {
            "detection_reason": "COE follow-up needed",
            "next_step": "Fix the root cause",
            "files": ["backend/core/session_unit.py"],
            "created_by": "agent:session_xyz",
        }
        result = validate_linked_context("ai_detected", ctx)
        assert "_missing_fields" not in result

    def test_meeting_required_fields(self):
        """Meeting todos require meeting_title, meeting_date, attendees, action_item."""
        from schemas.todo import validate_linked_context

        ctx = {
            "meeting_title": "Sprint Review",
            "meeting_date": "2026-03-31",
            "attendees": "XG, Bo Wang",
            "action_item": "Review design doc",
            "next_step": "Read and comment on design doc by Friday",
            "created_by": "job:meeting-notes",
        }
        result = validate_linked_context("meeting", ctx)
        assert "_missing_fields" not in result

    def test_manual_only_requires_next_step(self):
        """Manual todos only require next_step."""
        from schemas.todo import validate_linked_context

        ctx = {"next_step": "Do the thing", "created_by": "user:manual"}
        result = validate_linked_context("manual", ctx)
        assert "_missing_fields" not in result

    def test_all_types_require_next_step(self):
        """Every source_type requires next_step as universal field."""
        from schemas.todo import validate_linked_context

        for source_type in ["email", "slack", "chat", "ai_detected", "meeting", "manual"]:
            ctx = {"created_by": "test"}
            result = validate_linked_context(source_type, ctx)
            assert "next_step" in result.get("_missing_fields", []), (
                f"{source_type} should require next_step"
            )

    def test_unknown_source_type_only_requires_next_step(self):
        """Unknown source_type falls back to next_step only."""
        from schemas.todo import validate_linked_context

        ctx = {"next_step": "Something", "created_by": "unknown"}
        result = validate_linked_context("integration", ctx)
        assert "_missing_fields" not in result

    def test_validation_never_blocks(self):
        """Even completely empty context returns a dict (with warnings), never raises."""
        from schemas.todo import validate_linked_context

        result = validate_linked_context("email", {})
        assert isinstance(result, dict)
        assert "_missing_fields" in result


# ── Part 2: Structured Producer Protocol ─────────────────────────────


class TestStructuredTodoExtraction:
    """_parse_structured_todos extracts RADAR_TODOS JSON from agent output."""

    def test_parse_radar_todos_json_block(self):
        """Extract todos from <!-- RADAR_TODOS [...] --> block."""
        from jobs.executor import _parse_structured_todos

        text = """Here's your inbox summary.

<!-- RADAR_TODOS
[
  {
    "title": "Promote feedback due April 5",
    "priority": "high",
    "context": {
      "email_subject": "Promote Self-Assessment Reminder",
      "email_from": "hr-no-reply@amazon.com",
      "email_date": "2026-03-30T08:15:00Z",
      "email_snippet": "Please complete your self-assessment by April 5",
      "suggested_action": "reply",
      "next_step": "Open promote portal and complete form"
    }
  }
]
-->

That's all for today."""

        items = _parse_structured_todos(text, source_type="email", job_id="morning-inbox", job_name="Morning Inbox Check")
        assert len(items) == 1
        item = items[0]
        assert item["title"] == "Promote feedback due April 5"
        assert item["priority"] == "high"
        ctx = json.loads(item["linked_context"])
        assert ctx["email_subject"] == "Promote Self-Assessment Reminder"
        assert ctx["email_from"] == "hr-no-reply@amazon.com"
        assert ctx["next_step"] == "Open promote portal and complete form"
        assert ctx["created_by"] == "job:morning-inbox"

    def test_parse_multiple_todos(self):
        """Extract multiple todos from a single block."""
        from jobs.executor import _parse_structured_todos

        text = """<!-- RADAR_TODOS
[
  {"title": "Reply to HR", "priority": "high", "context": {"email_subject": "HR", "email_from": "hr@co.com", "email_date": "2026-03-30", "email_snippet": "...", "suggested_action": "reply", "next_step": "Reply"}},
  {"title": "Review doc", "priority": "medium", "context": {"email_subject": "Doc", "email_from": "bob@co.com", "email_date": "2026-03-30", "email_snippet": "...", "suggested_action": "read", "next_step": "Read doc"}}
]
-->"""

        items = _parse_structured_todos(text, source_type="email", job_id="test", job_name="Test")
        assert len(items) == 2

    def test_fallback_to_regex_on_no_json_block(self):
        """When no RADAR_TODOS block, falls back to legacy regex parsing."""
        from jobs.executor import _parse_structured_todos

        text = "urgent: Fix the build\nfollow up: Check deploy status"
        items = _parse_structured_todos(text, source_type="email", job_id="test", job_name="Test")
        # Should return empty — regex fallback is in the caller, not this function
        assert items == []

    def test_max_todos_respected(self):
        """Respects max_todos limit."""
        from jobs.executor import _parse_structured_todos

        todos = [{"title": f"Todo {i}", "priority": "low", "context": {"next_step": f"Do {i}"}} for i in range(10)]
        text = f"<!-- RADAR_TODOS\n{json.dumps(todos)}\n-->"
        items = _parse_structured_todos(text, source_type="chat", job_id="t", job_name="T", max_todos=3)
        assert len(items) == 3

    def test_context_inherits_job_metadata(self):
        """Each todo's linked_context includes job_id, job_name, created_by."""
        from jobs.executor import _parse_structured_todos

        text = '<!-- RADAR_TODOS\n[{"title":"Test","priority":"low","context":{"next_step":"Do it"}}]\n-->'
        items = _parse_structured_todos(text, source_type="email", job_id="inbox-123", job_name="Morning Inbox")
        ctx = json.loads(items[0]["linked_context"])
        assert ctx["job_id"] == "inbox-123"
        assert ctx["job_name"] == "Morning Inbox"
        assert ctx["created_by"] == "job:inbox-123"


# ── Part 3: Lifecycle Purge ──────────────────────────────────────────


class TestLifecyclePurge:
    """Terminal todos get archived then hard-deleted after retention period."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Create a test SQLite DB with todos table."""
        db_file = tmp_path / "test_data.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE todos (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                source TEXT,
                source_type TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'none',
                due_date TEXT,
                linked_context TEXT,
                task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        return db_file

    def _insert_todo(self, db_path, status, days_ago, **kwargs):
        """Insert a test todo aged `days_ago` days."""
        conn = sqlite3.connect(str(db_path))
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        todo_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO todos (id, workspace_id, title, description, source,
               source_type, status, priority, due_date, linked_context, task_id,
               created_at, updated_at)
               VALUES (?, 'swarmws', ?, ?, NULL, 'manual', ?, 'none', NULL, ?, NULL, ?, ?)""",
            (
                todo_id,
                kwargs.get("title", f"Test {status} {days_ago}d"),
                kwargs.get("description", ""),
                status,
                kwargs.get("linked_context", "{}"),
                ts, ts,
            ),
        )
        conn.commit()
        conn.close()
        return todo_id

    def _count_todos(self, db_path, status=None):
        conn = sqlite3.connect(str(db_path))
        if status:
            count = conn.execute("SELECT count(*) FROM todos WHERE status = ?", (status,)).fetchone()[0]
        else:
            count = conn.execute("SELECT count(*) FROM todos").fetchone()[0]
        conn.close()
        return count

    def test_purge_deletes_old_terminal_todos(self, db_path, tmp_path):
        """handled/cancelled/deleted todos older than retention_days are hard-deleted."""
        from jobs.executor import _purge_terminal_todos

        # Old terminal todos (should be purged)
        self._insert_todo(db_path, "handled", 20)
        self._insert_todo(db_path, "cancelled", 30)
        self._insert_todo(db_path, "deleted", 15)
        # Recent terminal (should survive)
        self._insert_todo(db_path, "handled", 5)
        # Active todos (should survive)
        self._insert_todo(db_path, "pending", 60)
        self._insert_todo(db_path, "overdue", 20)

        archive_dir = tmp_path / "archives"
        result = _purge_terminal_todos(
            retention_days=14,
            archive_before_purge=True,
            db_path=db_path,
            archive_dir=archive_dir,
        )

        # 3 old terminal purged
        assert result["purged_count"] == 3
        # 3 survive (1 recent handled + 1 pending + 1 overdue)
        assert self._count_todos(db_path) == 3
        # Active todos untouched
        assert self._count_todos(db_path, "pending") == 1
        assert self._count_todos(db_path, "overdue") == 1

    def test_purge_archives_before_delete(self, db_path, tmp_path):
        """Purged todos are archived to JSONL before deletion."""
        from jobs.executor import _purge_terminal_todos

        self._insert_todo(db_path, "handled", 20, title="Archived task")
        archive_dir = tmp_path / "archives"

        _purge_terminal_todos(
            retention_days=14,
            archive_before_purge=True,
            db_path=db_path,
            archive_dir=archive_dir,
        )

        # Archive file should exist
        archive_file = archive_dir / "todo-archive.jsonl"
        assert archive_file.exists()
        lines = archive_file.read_text().strip().splitlines()
        assert len(lines) == 1
        archived = json.loads(lines[0])
        assert archived["title"] == "Archived task"
        assert archived["status"] == "handled"

    def test_purge_skips_archive_when_disabled(self, db_path, tmp_path):
        """When archive_before_purge=False, deletes without archiving."""
        from jobs.executor import _purge_terminal_todos

        self._insert_todo(db_path, "cancelled", 20)
        archive_dir = tmp_path / "archives"

        _purge_terminal_todos(
            retention_days=14,
            archive_before_purge=False,
            db_path=db_path,
            archive_dir=archive_dir,
        )

        archive_file = archive_dir / "todo-archive.jsonl"
        assert not archive_file.exists()
        assert self._count_todos(db_path) == 0

    def test_purge_never_touches_active_todos(self, db_path, tmp_path):
        """pending, overdue, in_discussion are NEVER purged regardless of age."""
        from jobs.executor import _purge_terminal_todos

        self._insert_todo(db_path, "pending", 100)
        self._insert_todo(db_path, "overdue", 100)
        self._insert_todo(db_path, "in_discussion", 100)

        result = _purge_terminal_todos(
            retention_days=14,
            db_path=db_path,
            archive_dir=tmp_path,
        )
        assert result["purged_count"] == 0
        assert self._count_todos(db_path) == 3


class TestOverdueEscalation:
    """Overdue todos that stay overdue too long get auto-cancelled."""

    @pytest.fixture
    def db_path(self, tmp_path):
        db_file = tmp_path / "test_data.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE todos (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                source TEXT,
                source_type TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'pending',
                priority TEXT NOT NULL DEFAULT 'none',
                due_date TEXT,
                linked_context TEXT,
                task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        return db_file

    def test_overdue_auto_cancel(self, db_path):
        """Overdue todos > cancel_days old get cancelled."""
        from jobs.executor import _escalate_overdue_todos

        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        recent_ts = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

        conn = sqlite3.connect(str(db_path))
        # Old overdue — should be cancelled
        conn.execute(
            "INSERT INTO todos VALUES (?, 'swarmws', ?, NULL, NULL, 'email', 'overdue', 'high', NULL, '{}', NULL, ?, ?)",
            (str(uuid.uuid4()), "Old overdue task", old_ts, old_ts),
        )
        # Recent overdue — should survive
        conn.execute(
            "INSERT INTO todos VALUES (?, 'swarmws', ?, NULL, NULL, 'email', 'overdue', 'high', NULL, '{}', NULL, ?, ?)",
            (str(uuid.uuid4()), "Recent overdue task", recent_ts, recent_ts),
        )
        conn.commit()
        conn.close()

        result = _escalate_overdue_todos(cancel_days=14, db_path=db_path)
        assert result["cancelled_count"] == 1

        conn = sqlite3.connect(str(db_path))
        statuses = conn.execute("SELECT title, status FROM todos ORDER BY title").fetchall()
        conn.close()
        assert ("Old overdue task", "cancelled") in statuses
        assert ("Recent overdue task", "overdue") in statuses
