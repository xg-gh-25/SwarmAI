"""
Tests for Health Alerting — ContextHealthHook persists its findings.

Acceptance criteria:
1. ContextHealthHook persists findings to health_findings.json
2. A later write keeps the weekly memory_health results
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
