"""Tests for core.engine_metrics — Core Engine growth metrics collector.

Tests: memory effectiveness analysis, DDD change suggestions,
session stats, engine level computation, unified metrics collection.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.engine_metrics import (
    collect_memory_effectiveness,
    collect_ddd_change_suggestions,
    collect_engine_metrics,
    _analyze_section,
    _collect_session_stats,
    _collect_ddd_health,
    _compute_engine_level,
)


@pytest.fixture
def ws_dir(tmp_path):
    """Create a minimal workspace structure."""
    # .context/MEMORY.md
    context = tmp_path / ".context"
    context.mkdir()
    # Knowledge/DailyActivity/
    da_dir = tmp_path / "Knowledge" / "DailyActivity"
    da_dir.mkdir(parents=True)
    # Projects/
    projects = tmp_path / "Projects"
    projects.mkdir()
    return tmp_path


class TestMemoryEffectiveness:
    def test_missing_memory_md(self, ws_dir):
        result = collect_memory_effectiveness(ws_dir)
        assert result["status"] == "missing"

    def test_empty_memory_md(self, ws_dir):
        (ws_dir / ".context" / "MEMORY.md").write_text("")
        result = collect_memory_effectiveness(ws_dir)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0

    def test_counts_sections_and_entries(self, ws_dir):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=45)).isoformat()
        content = f"""## Recent Context

- {today}: Something fresh happened
- {today}: Another fresh thing
- {old}: Old stale entry

## Key Decisions

- {today}: Decided to build X
"""
        (ws_dir / ".context" / "MEMORY.md").write_text(content)
        result = collect_memory_effectiveness(ws_dir)

        assert result["status"] == "ok"
        assert result["total_entries"] == 4
        assert result["dated_entries"] == 4
        assert result["recent_entries_14d"] == 3
        assert result["stale_entries_30d"] == 1
        assert result["freshness_score"] == 75  # 3/4
        assert "Recent Context" in result["sections"]
        assert "Key Decisions" in result["sections"]
        assert result["sections"]["Recent Context"]["count"] == 3
        assert result["sections"]["Key Decisions"]["count"] == 1

    def test_undated_entries_not_counted_as_stale(self, ws_dir):
        content = """## Open Threads

- Something without a date
- Another undated entry
"""
        (ws_dir / ".context" / "MEMORY.md").write_text(content)
        result = collect_memory_effectiveness(ws_dir)
        assert result["total_entries"] == 2
        assert result["dated_entries"] == 0
        assert result["stale_entries_30d"] == 0
        assert result["freshness_score"] == 0  # no dated entries

    def test_stale_samples_capped_at_5(self, ws_dir):
        old = (date.today() - timedelta(days=60)).isoformat()
        entries = "\n".join(f"- {old}: Stale entry {i}" for i in range(10))
        content = f"## Recent Context\n\n{entries}\n"
        (ws_dir / ".context" / "MEMORY.md").write_text(content)
        result = collect_memory_effectiveness(ws_dir)
        assert len(result["stale_samples"]) == 5


class TestAnalyzeSection:
    def test_empty_section(self):
        result = _analyze_section([], "2026-01-01", "2025-12-01")
        assert result["count"] == 0
        assert result["dated"] == 0

    def test_mixed_dates(self):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=45)).isoformat()
        entries = [
            f"- {today}: Fresh",
            f"- {old}: Stale",
            "- undated entry",
        ]
        result = _analyze_section(
            entries,
            (date.today() - timedelta(days=14)).isoformat(),
            (date.today() - timedelta(days=30)).isoformat(),
        )
        assert result["count"] == 3
        assert result["dated"] == 2
        assert result["recent_14d"] == 1
        assert result["stale_30d"] == 1


class TestDDDChangeSuggestions:
    def test_no_swarmai_root_returns_empty(self, ws_dir, monkeypatch):
        # Patch _find_swarmai_root to not find the real codebase
        monkeypatch.setattr(
            "core.engine_metrics._find_swarmai_root",
            lambda ws: None,
        )
        result = collect_ddd_change_suggestions(ws_dir)
        assert result == []


class TestDDDHealth:
    def test_no_projects_dir(self, ws_dir):
        (ws_dir / "Projects").rmdir()
        result = _collect_ddd_health(ws_dir)
        assert result["projects"] == []

    def test_project_with_docs(self, ws_dir):
        proj = ws_dir / "Projects" / "TestProject"
        proj.mkdir()
        (proj / "PRODUCT.md").write_text("# Product")
        (proj / "TECH.md").write_text("# Tech")
        result = _collect_ddd_health(ws_dir)
        assert len(result["projects"]) == 1
        assert result["projects"][0]["name"] == "TestProject"
        assert result["projects"][0]["docs"]["PRODUCT.md"]["exists"] is True
        assert result["projects"][0]["docs"]["IMPROVEMENT.md"]["exists"] is False


class TestSessionStats:
    def test_no_daily_activity(self, ws_dir):
        (ws_dir / "Knowledge" / "DailyActivity").rmdir()
        result = _collect_session_stats(ws_dir)
        assert result["available"] is False

    def test_counts_sessions(self, ws_dir):
        da_dir = ws_dir / "Knowledge" / "DailyActivity"
        today = date.today().isoformat()
        content = f"""---
sessions_count: 3
---
## 10:00 | Session 1
Stuff
## 11:00 | Session 2
Stuff
## 12:00 | Session 3
Stuff
"""
        (da_dir / f"{today}.md").write_text(content)
        result = _collect_session_stats(ws_dir)
        assert result["last_7d_sessions"] == 3
        assert result["last_7d_active_days"] == 1


class TestComputeEngineLevel:
    def test_returns_structure(self, ws_dir):
        result = _compute_engine_level(ws_dir)
        assert "current" in result
        assert "l3_progress" in result
        assert "l3_features" in result
        assert "levels" in result
        assert result["levels"]["L0_reactive"] == "complete"
        assert result["levels"]["L4_autonomous"] == "future"


class TestCollectEngineMetrics:
    def test_full_collection(self, ws_dir):
        # Create minimal MEMORY.md
        today = date.today().isoformat()
        (ws_dir / ".context" / "MEMORY.md").write_text(
            f"## Recent Context\n\n- {today}: Test entry\n"
        )
        result = collect_engine_metrics(str(ws_dir))
        assert "collected_at" in result
        assert "engine_level" in result
        assert "learning" in result
        assert "memory" in result
        assert "ddd_suggestions" in result
        assert "ddd_health" in result
        assert "context_health" in result
        assert "sessions" in result
        assert result["memory"]["status"] == "ok"

    def test_resilient_to_missing_workspace(self):
        result = collect_engine_metrics("/nonexistent/path")
        assert "engine_level" in result
        assert result["memory"]["status"] == "missing"
