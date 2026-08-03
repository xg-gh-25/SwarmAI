"""
Tests for Sprint 3: Health Alerting — health findings in session briefing.

Acceptance criteria:
1. ContextHealthHook persists findings to health_findings.json
2. proactive_intelligence reads health_findings and shows alerts in briefing
3. Critical findings auto-create Radar todos
4. Weekly memory_health results surface in briefing
5. All existing tests pass
"""

from __future__ import annotations

import json

import pytest


# ── AC1: ContextHealthHook persists findings ──────────────────────────

class TestHealthFindingsPersistence:
    def test_persist_findings_writes_json(self, tmp_path):
        """_persist_findings writes structured findings to health_findings.json."""
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()

        findings = [
            "EMPTY: MEMORY.md (0 bytes)",
            "UNCOMMITTED: 2 context file(s): MEMORY.md, EVOLUTION.md",
            "AUTO-FIXED: removed stale .git/index.lock",
        ]

        hook._persist_findings(tmp_path, findings)

        findings_file = tmp_path / "Services" / "swarm-jobs" / "health_findings.json"
        assert findings_file.exists()
        data = json.loads(findings_file.read_text())
        assert "timestamp" in data
        assert len(data["findings"]) == 3
        assert data["findings"][0]["level"] == "critical"  # EMPTY
        assert data["findings"][1]["level"] == "warning"   # UNCOMMITTED
        assert data["findings"][2]["level"] == "info"       # AUTO-FIXED

    def test_persist_findings_preserves_memory_health(self, tmp_path):
        """_persist_findings should keep existing memory_health data."""
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()

        # Pre-existing health_findings.json with memory_health from weekly job
        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "old",
            "findings": [],
            "memory_health": {"actions": ["Pruned 2 stale entries"], "summary": "Done"},
        }))

        hook._persist_findings(tmp_path, ["MISSING: DailyActivity"])

        data = json.loads((findings_dir / "health_findings.json").read_text())
        # New findings should be there
        assert len(data["findings"]) == 1
        # Memory health should be preserved from previous run
        assert data["memory_health"]["actions"] == ["Pruned 2 stale entries"]



# ── AC3: Critical findings create Radar todos (direct sqlite, run_e681a61d) ──
# Rewritten from the old mock-based test: the prior version patched
# ToDoManager.list_todos/create_todo — methods that DO NOT EXIST on the real
# async ToDoManager (it has async create/list). The mock auto-created them, so
# the test passed against a path that always threw in production. This version
# exercises the REAL direct-sqlite insert against a tmp DB.

@pytest.fixture
def tmp_todo_db(tmp_path, monkeypatch):
    """A real sqlite DB with the todos table, wired in as jobs.paths.DB_PATH."""
    import sqlite3
    db = tmp_path / "data.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE todos (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT, source TEXT, source_type TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'pending', priority TEXT NOT NULL DEFAULT 'none',
            due_date TEXT, linked_context TEXT, task_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()
    import jobs.paths
    monkeypatch.setattr(jobs.paths, "DB_PATH", db)
    return db


class TestHealthRadarTodos:
    def _rows(self, db):
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM todos").fetchall()
        conn.close()
        return rows

    def test_critical_finding_creates_todo(self, tmp_todo_db):
        """A critical finding inserts exactly one Radar todo via direct sqlite."""
        from core.proactive_intelligence import _create_health_todo
        _create_health_todo("Empty context file detected: MEMORY.md", severity="critical")
        rows = self._rows(tmp_todo_db)
        assert len(rows) == 1
        assert "Health Alert" in rows[0]["title"]
        # severity 'critical' must clamp to a valid priority (CHECK set has no 'critical')
        assert rows[0]["priority"] in ("high", "medium", "low", "none")
        assert rows[0]["workspace_id"]  # NOT NULL satisfied
        assert rows[0]["status"] == "pending"

    def test_warning_severity_does_not_create_by_default(self, tmp_todo_db):
        """Default (non-escalated) warning must NOT create a todo — preserves the
        existing ddd_orchestrator caller's no-op contract."""
        from core.proactive_intelligence import _create_health_todo
        _create_health_todo("some routine warning", severity="warning")
        assert len(self._rows(tmp_todo_db)) == 0

    def test_explicit_escalate_creates_todo(self, tmp_todo_db):
        """An explicit escalate=True lets a high-priority warning create a todo."""
        from core.proactive_intelligence import _create_health_todo
        _create_health_todo("recurring high-priority gap", severity="warning", escalate=True)
        assert len(self._rows(tmp_todo_db)) == 1

    def test_dedup_no_double_create(self, tmp_todo_db):
        """The same finding must not create duplicate todos across calls."""
        from core.proactive_intelligence import _create_health_todo
        _create_health_todo("Empty context file detected: MEMORY.md", severity="critical")
        _create_health_todo("Empty context file detected: MEMORY.md", severity="critical")
        assert len(self._rows(tmp_todo_db)) == 1

    def test_completed_todo_not_recreated_immediately(self, tmp_todo_db):
        """HIGH (adversarial run_e681a61d): after a user COMPLETES (status=handled)
        an escalated todo, the same recurring finding must NOT immediately recreate
        it next briefing — else the user can never make it go away by acting.
        Dedup must consider recently-handled todos, not only pending/in_discussion."""
        import sqlite3
        from core.proactive_intelligence import _create_health_todo
        msg = "Mid-stream halt requiring user intervention (4x)"
        _create_health_todo(msg, severity="critical")
        # user completes it
        conn = sqlite3.connect(str(tmp_todo_db))
        conn.execute("UPDATE todos SET status='handled' WHERE title LIKE 'Health Alert:%'")
        conn.commit(); conn.close()
        # next briefing, same finding recurs
        _create_health_todo(msg, severity="critical")
        rows = self._rows(tmp_todo_db)
        # Must NOT have a 2nd (pending) recreate of the just-handled finding
        pending = [r for r in rows if r["status"] == "pending"]
        assert len(pending) == 0, "recently-handled finding must not be immediately recreated"

