"""ToDo flow-closure — sweep auto-complete/confirm, purge protection, history/stats,
one-way invariant guard (run_d28de5fd, 2026-08-01).

Design: Knowledge/Designs/2026-08-01-todo-flow-closure-design.md
Covers AC1-AC7. Sweep tests call the private sweep helpers directly against a real
temp DB (no mocks of our own code — boundary-only mock discipline).
"""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ── AC6: pure stats helper (no DB) ──────────────────────────────────

class TestComputeHistoryStats:
    def _row(self, **kw):
        base = {
            "id": str(uuid.uuid4()), "created_at": "2026-07-01T10:00:00+00:00",
            "source_type": "manual", "status": "pending", "review_state": None,
            "review_kind": None, "completed_at": None,
        }
        base.update(kw)
        return base

    def test_hand_counted_fixture_matches_all_five(self):
        from core.todo_stats import compute_history_stats
        rows = [
            # week 27 (2026-06-29..): 2 created, 1 completed+confirmed(manual)
            self._row(created_at="2026-06-30T09:00:00+00:00", status="handled",
                      review_state="confirmed", review_kind="manual",
                      completed_at="2026-06-30T12:00:00+00:00"),
            self._row(created_at="2026-06-30T09:00:00+00:00", source_type="email"),
            # week 28: 1 created, completed+auto-confirmed
            self._row(created_at="2026-07-06T09:00:00+00:00", status="handled",
                      review_state="confirmed", review_kind="auto",
                      completed_at="2026-07-06T12:00:00+00:00"),
            # week 28: 1 created, rejected (still counts as completed work)
            self._row(created_at="2026-07-07T09:00:00+00:00", source_type="slack",
                      review_state="rejected", completed_at="2026-07-07T12:00:00+00:00"),
        ]
        s = compute_history_stats(rows)
        assert s["totals"] == {"created": 4, "completed": 3, "confirmed": 2,
                               "rejected": 1, "reviewed": 3}
        assert s["completion_rate"] == 3 / 4
        assert s["source_distribution"] == {"manual": 2, "email": 1, "slack": 1}
        assert s["confirm_vs_auto"] == {"manual": 1, "auto": 1}
        assert s["reject_rate"] == 1 / 3
        weeks = {w["week"]: w for w in s["throughput_weekly"]}
        assert weeks["2026-W27"]["created"] == 2
        assert weeks["2026-W28"]["created"] == 2
        assert weeks["2026-W28"]["completed"] == 2

    def test_empty_is_safe(self):
        from core.todo_stats import compute_history_stats
        s = compute_history_stats([])
        assert s["completion_rate"] == 0.0
        assert s["reject_rate"] == 0.0
        assert s["throughput_weekly"] == []


# ── AC4: purge review-window protection ─────────────────────────────

class TestPurgeReviewWindowProtection:
    @pytest.fixture
    def db_path(self, tmp_path):
        db_file = tmp_path / "test_data.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE todos (
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT, source TEXT, source_type TEXT NOT NULL DEFAULT 'manual',
                status TEXT NOT NULL DEFAULT 'pending', priority TEXT NOT NULL DEFAULT 'none',
                due_date TEXT, linked_context TEXT, task_id TEXT,
                review_state TEXT, review_kind TEXT, dispatched_session_id TEXT,
                dispatched_tab_label TEXT, dispatched_at TEXT, completed_at TEXT,
                reviewed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        conn.commit(); conn.close()
        return db_file

    def _insert(self, db_path, status, days_ago, review_state=None):
        conn = sqlite3.connect(str(db_path))
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
        tid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO todos (id, workspace_id, title, source_type, status, priority, "
            "review_state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, "swarmws", f"t {status} {days_ago}d", "manual", status, "none",
             review_state, ts, ts),
        )
        conn.commit(); conn.close()
        return tid

    def _count(self, db_path):
        conn = sqlite3.connect(str(db_path))
        n = conn.execute("SELECT count(*) FROM todos").fetchone()[0]
        conn.close(); return n

    def test_completed_awaiting_review_survives_purge_past_retention(self, db_path, tmp_path):
        """A review_state='completed' todo is NOT hard-deleted even past 14d."""
        from jobs.executor import _purge_terminal_todos
        # 20-day handled but STILL awaiting review → must survive
        self._insert(db_path, "handled", 20, review_state="completed")
        # 20-day handled + confirmed → normal purge target
        self._insert(db_path, "handled", 20, review_state="confirmed")
        _purge_terminal_todos(retention_days=14, archive_before_purge=True,
                              db_path=db_path, archive_dir=tmp_path / "arch")
        # only the confirmed one purged; the completed-awaiting-review survives
        assert self._count(db_path) == 1

    def test_pre_migration_null_review_state_still_purged(self, db_path, tmp_path):
        """COALESCE guard: NULL review_state (pre-migration rows) purge normally."""
        from jobs.executor import _purge_terminal_todos
        self._insert(db_path, "handled", 20, review_state=None)
        _purge_terminal_todos(retention_days=14, archive_before_purge=False,
                              db_path=db_path, archive_dir=tmp_path / "arch")
        assert self._count(db_path) == 0


# ── AC2/AC3: sweep auto-complete + auto-confirm (real db singleton) ──

class TestSweepFlowClosure:
    """Drives the real _sweep_auto_complete_todos / _sweep_auto_confirm_todos against
    the conftest test-db singleton (db.todos / db.messages). No mocks of our own code."""

    async def _put_todo(self, **kw):
        from database import db
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        row = {
            "id": str(uuid.uuid4()), "workspace_id": "swarmws",
            "title": kw.pop("title", "sweep test"), "source_type": "manual",
            "status": kw.pop("status", "pending"), "priority": "none",
            "created_at": now, "updated_at": now,
        }
        row.update(kw)
        await db.todos.put(row)
        return row["id"]

    async def _put_session_and_reply(self, session_id, reply_created_at):
        from database import db
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        await db.sessions.put({
            "id": session_id, "title": "s", "status": "idle",
            "created_at": now, "updated_at": now, "last_accessed": now,
        })
        # reply_created_at=None → let put() write NAIVE LOCAL time, exactly as the
        # real message path does (datetime.now().isoformat()). This reproduces the
        # Gate-2 timezone scenario; do NOT force an aware timestamp here.
        msg = {
            "id": str(uuid.uuid4()), "session_id": session_id, "role": "assistant",
            "content": "done",
        }
        if reply_created_at is not None:
            msg["created_at"] = reply_created_at
            msg["updated_at"] = reply_created_at
        await db.messages.put(msg)

    async def test_auto_complete_marks_completed_status_stays_pending(self):
        """AC2 + locked invariant: dispatched todo whose session replied after
        dispatch → review_state=completed, completed_at set, status STILL pending.
        Reply uses REAL naive-local created_at (put() default) — the Gate-2 case."""
        from database import db
        from core.lifecycle_manager import LifecycleManager

        # dispatched 1h ago (aware UTC — matches how a real dispatch would write it
        # if aware); the reply is written NOW as naive-local by put(). The tz-aware
        # comparison in assistant_replied_since must still see reply > dispatch.
        dispatched_at = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
        sid = str(uuid.uuid4())
        await self._put_session_and_reply(sid, None)  # naive-local reply, now
        tid = await self._put_todo(dispatched_session_id=sid, dispatched_at=dispatched_at,
                                   status="pending", review_state=None)

        n = await LifecycleManager(router=None)._sweep_auto_complete_todos()
        assert n == 1
        row = await db.todos.get(tid)
        assert row["review_state"] == "completed"
        assert row["completed_at"] is not None
        assert row["status"] == "pending"  # LOCKED invariant — not handled until Confirm

    async def test_auto_complete_ignores_reply_before_dispatch(self):
        """A reply that genuinely predates dispatch must NOT complete the todo.
        Reply written NOW (naive-local); dispatch is 1h in the FUTURE → reply < dispatch
        by true wall-clock. This is the Gate-2 regression: a naive-local reply must not
        false-positive against an aware future dispatch."""
        from database import db
        from core.lifecycle_manager import LifecycleManager

        future_dispatch = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
        sid = str(uuid.uuid4())
        await self._put_session_and_reply(sid, None)  # naive-local reply, now (< future dispatch)
        tid = await self._put_todo(dispatched_session_id=sid, dispatched_at=future_dispatch,
                                   review_state=None)

        n = await LifecycleManager(router=None)._sweep_auto_complete_todos()
        assert n == 0
        row = await db.todos.get(tid)
        assert row["review_state"] is None
        row = await db.todos.get(tid)
        assert row["review_state"] is None

    async def test_auto_confirm_after_7_days(self):
        """AC3: completed todo with completed_at older than 7d → confirmed/auto."""
        from database import db
        from core.lifecycle_manager import LifecycleManager

        old_completed = (datetime.now(timezone.utc) - timedelta(days=8)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
        tid = await self._put_todo(status="pending", review_state="completed",
                                   completed_at=old_completed)

        n = await LifecycleManager(router=None)._sweep_auto_confirm_todos()
        assert n == 1
        row = await db.todos.get(tid)
        assert row["review_state"] == "confirmed"
        assert row["review_kind"] == "auto"
        assert row["reviewed_at"] is not None

    async def test_auto_confirm_skips_recent(self):
        """A recently-completed todo (< 7d) is NOT auto-confirmed."""
        from database import db
        from core.lifecycle_manager import LifecycleManager

        recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00")
        tid = await self._put_todo(review_state="completed", completed_at=recent)

        n = await LifecycleManager(router=None)._sweep_auto_confirm_todos()
        assert n == 0
        row = await db.todos.get(tid)
        assert row["review_state"] == "completed"


# ── AC7: one-way invariant guard (static source assertion) ──────────

class TestOneWayInvariant:
    def test_no_todo_db_op_in_streaming_path(self):
        """Zero todo DB operation reachable from the SSE/streaming files."""
        root = Path(__file__).resolve().parents[1] / "core"
        for fname in ("streaming_orchestrator.py", "session_unit.py", "retry_manager.py"):
            text = (root / fname).read_text(encoding="utf-8").lower()
            assert "db.todos" not in text, f"{fname} touches db.todos — breaks one-way invariant"
            assert "todo_manager" not in text, f"{fname} imports todo_manager — breaks one-way invariant"
