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


# ── AC3: Critical findings create Radar todos ─────────────────────────

class TestHealthRadarTodos:
    def test_critical_finding_creates_todo(self, tmp_path):
        """Critical health findings should create Radar todos."""
        from core.proactive_intelligence import _create_health_todo

        # Mock the todo creation — patched at import target
        with patch("core.todo_manager.ToDoManager") as MockTodo:
            mock_instance = MagicMock()
            MockTodo.return_value = mock_instance
            mock_instance.list_todos.return_value = []
            mock_instance.create_todo.return_value = {"id": 1}

            _create_health_todo(
                "Empty context file detected: MEMORY.md",
                severity="critical",
            )

            mock_instance.create_todo.assert_called_once()
            call_args_str = str(mock_instance.create_todo.call_args)
            assert "Health Alert" in call_args_str


# ── AC4: Memory health results in briefing ────────────────────────────

class TestMemoryHealthInBriefing:
    def test_memory_health_actions_in_highlights(self, tmp_path):
        """Weekly memory health actions should appear in health highlights."""
        from core.proactive_intelligence import _get_health_highlights

        findings_dir = tmp_path / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        (findings_dir / "health_findings.json").write_text(json.dumps({
            "timestamp": "2026-03-26T03:00:00Z",
            "findings": [],
            "memory_health": {
                "actions": [
                    "Removed stale memory: 2026-02-01: Ancient entry",
                    "Resolved thread: Signal fetcher service",
                ],
                "summary": "2 items maintained",
            },
        }))

        highlights = _get_health_highlights(str(tmp_path))
        assert any("maintenance" in h.lower() or "memory" in h.lower() for h in highlights)
