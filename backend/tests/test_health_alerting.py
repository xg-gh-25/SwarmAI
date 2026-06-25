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
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

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


# ── AC2: Proactive intelligence reads health findings ─────────────────

class TestHealthInBriefing:
    def test_get_health_highlights_returns_findings(self, tmp_path):
        """_get_health_highlights reads health_findings.json and formats alerts."""
        from core.proactive_intelligence import _get_health_highlights

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-03-26T03:00:00Z",
            "findings": [
                {"level": "warning", "message": "MEMORY.md has uncommitted changes"},
                {"level": "info", "message": "All 11 context files present"},
            ],
            "memory_health": {
                "actions": ["Removed stale memory: 2026-02-01", "Resolved thread: Signal fetcher"],
                "summary": "Light maintenance done",
            },
        }))

        highlights = _get_health_highlights(str(tmp_path))
        assert len(highlights) >= 1
        assert any("uncommitted" in h.lower() or "memory" in h.lower() for h in highlights)

    def test_get_health_highlights_missing_file(self, tmp_path):
        """Returns empty list when no health_findings.json exists."""
        from core.proactive_intelligence import _get_health_highlights
        highlights = _get_health_highlights(str(tmp_path))
        assert highlights == []

    def test_get_health_highlights_corrupt_file(self, tmp_path):
        """Returns empty list on corrupt JSON."""
        from core.proactive_intelligence import _get_health_highlights
        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text("not json{{{")
        highlights = _get_health_highlights(str(tmp_path))
        assert highlights == []


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


# ── AC4: Memory health results in briefing ────────────────────────────

class TestMemoryHealthInBriefing:
    def test_memory_health_gaps_in_highlights(self, tmp_path):
        """Capability gaps from weekly memory health appear in highlights."""
        from core.proactive_intelligence import _get_health_highlights

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-03-26T03:00:00Z",
            "findings": [],
            "memory_health": {
                "capability_gaps": [
                    {
                        "pattern": "Memory pipeline fails on large files",
                        "priority": "medium",
                        "occurrences": 3,
                        "suggested_action": "add size guard",
                    },
                ],
                "actions": [
                    "Removed stale memory: 2026-02-01: Ancient entry",
                ],
                "summary": "1 gap detected, 1 item maintained",
            },
        }))

        highlights = _get_health_highlights(str(tmp_path))
        assert any("gap" in h.lower() or "memory" in h.lower() for h in highlights)
        # Routine maintenance actions should NOT appear (noise suppression)
        assert not any("removed stale" in h.lower() for h in highlights)

    def test_high_priority_gap_escalates_to_todo(self, tmp_path, tmp_todo_db):
        """Active-maintenance reflex: a HIGH-priority capability gap escalates to
        a Radar todo (not just passive display). Medium/low stay display-only."""
        from core.proactive_intelligence import _get_health_highlights
        import sqlite3

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-06-25T03:00:00Z",
            "findings": [],
            "memory_health": {
                "capability_gaps": [
                    {"pattern": "Mid-stream response halt requiring user intervention",
                     "priority": "high", "occurrences": 4,
                     "suggested_action": "add E2E streaming guard"},
                    {"pattern": "minor formatting drift", "priority": "low",
                     "occurrences": 2, "suggested_action": "lint"},
                ],
            },
        }))

        _get_health_highlights(str(tmp_path))

        conn = sqlite3.connect(str(tmp_todo_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM todos").fetchall()
        conn.close()
        # Exactly the HIGH gap escalated; the low one did not.
        assert len(rows) == 1, f"expected 1 escalated todo, got {len(rows)}"
        assert "Mid-stream" in rows[0]["title"]


# ── AC5: Governance promotion signal in briefing ───────────────────────

class TestGovernancePromotionInBriefing:
    def test_governance_signal_surfaces_in_briefing(self, tmp_path):
        """When .governance_promotion_candidates.json exists, alert appears."""
        from core.proactive_intelligence import _get_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        (ctx_dir / ".governance_promotion_candidates.json").write_text(json.dumps({
            "detected_at": "2026-05-19T10:00:00Z",
            "candidates": {"A": 6, "C": 3},
            "message": "Governance promotion candidates detected: Bias A (6x), Bias C (3x)",
        }))

        # Also need health_findings.json for the function to not short-circuit
        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-05-19T10:00:00Z",
            "findings": [],
        }))

        highlights = _get_health_highlights(str(tmp_path))
        governance_alerts = [h for h in highlights if "governance" in h.lower()]
        assert len(governance_alerts) == 1, f"Expected 1 governance alert, got: {highlights}"
        assert "Bias A (6x)" in governance_alerts[0]
        assert "Bias C (3x)" in governance_alerts[0]
        assert "PROMOTE" in governance_alerts[0]

    def test_no_governance_signal_no_alert(self, tmp_path):
        """When signal file doesn't exist, no governance alert."""
        from core.proactive_intelligence import _get_health_highlights

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-05-19T10:00:00Z",
            "findings": [],
        }))

        highlights = _get_health_highlights(str(tmp_path))
        governance_alerts = [h for h in highlights if "governance" in h.lower()]
        assert len(governance_alerts) == 0

    def test_governance_signal_corrupt_json_graceful(self, tmp_path):
        """Corrupt signal file doesn't crash, just ignored."""
        from core.proactive_intelligence import _get_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        (ctx_dir / ".governance_promotion_candidates.json").write_text("broken{{{")

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-05-19T10:00:00Z",
            "findings": [],
        }))

        highlights = _get_health_highlights(str(tmp_path))
        governance_alerts = [h for h in highlights if "governance" in h.lower()]
        assert len(governance_alerts) == 0  # Graceful — no crash

    def test_governance_signal_empty_candidates_no_alert(self, tmp_path):
        """Signal file exists but candidates dict is empty → no alert."""
        from core.proactive_intelligence import _get_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        (ctx_dir / ".governance_promotion_candidates.json").write_text(json.dumps({
            "detected_at": "2026-05-19T10:00:00Z",
            "candidates": {},
        }))

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-05-19T10:00:00Z",
            "findings": [],
        }))

        highlights = _get_health_highlights(str(tmp_path))
        governance_alerts = [h for h in highlights if "governance" in h.lower()]
        assert len(governance_alerts) == 0
