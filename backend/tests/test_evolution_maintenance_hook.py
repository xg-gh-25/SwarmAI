"""Unit tests for the EvolutionMaintenanceHook.

Tests entry parsing, deprecation logic, pruning logic, and changelog
writing against synthetic EVOLUTION.md content.

Testing methodology: unit tests with temp files.
Key invariants:
- Active entries idle >30 days with 0 usage → deprecated
- Deprecated entries with 0 usage → pruned (removed from file)
- All actions logged to EVOLUTION_CHANGELOG.jsonl
- Entries with usage_count > 0 are never deprecated or pruned
- File locking is used for prune operations
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hooks.evolution_maintenance_hook import (
    EvolutionMaintenanceHook,
    _parse_entries,
    _get_field,
    _append_changelog,
)
from core.session_hooks import HookContext


# Helper: date string N days ago
def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# Synthetic EVOLUTION.md content
def _make_evolution_md(entries: list[dict]) -> str:
    """Build a minimal EVOLUTION.md with given entries in Capabilities Built."""
    lines = [
        "# SwarmAI Evolution Registry\n",
        "## Capabilities Built\n",
    ]
    for e in entries:
        lines.append(
            f"### {e['id']} | reactive | skill | {e['date']}\n"
            f"- **Name**: {e.get('name', 'Test')}\n"
            f"- **Description**: {e.get('desc', 'Test entry')}\n"
            f"- **Usage Count**: {e.get('usage', 0)}\n"
            f"- **Status**: {e.get('status', 'active')}\n\n"
        )
    lines.append("## Optimizations Learned\n\n_None._\n")
    lines.append("## Corrections Captured\n\n_None._\n")
    lines.append("## Competence Learned\n\n_None._\n")
    lines.append("## Failed Evolutions\n\n_None._\n")
    return "".join(lines)


def _make_context(tmp: Path) -> HookContext:
    return HookContext(
        session_id="test-session",
        agent_id="default",
        message_count=10,
        session_start_time="2026-03-01T00:00:00Z",
        session_title="Test",
    )


class TestParseEntries:
    """Tests for _parse_entries helper."""

    def test_parses_single_entry(self):
        content = _make_evolution_md([
            {"id": "E001", "date": "2026-01-01", "usage": 3, "status": "active"},
        ])
        entries = _parse_entries(content, "Capabilities Built")
        assert len(entries) == 1
        assert entries[0]["id"] == "E001"
        assert entries[0]["usage_count"] == 3
        assert entries[0]["status"] == "active"

    def test_parses_multiple_entries(self):
        content = _make_evolution_md([
            {"id": "E001", "date": "2026-01-01"},
            {"id": "E002", "date": "2026-02-01"},
        ])
        entries = _parse_entries(content, "Capabilities Built")
        assert len(entries) == 2

    def test_empty_section_returns_empty(self):
        content = "# Title\n\n## Capabilities Built\n\n_None._\n\n## Other\n"
        entries = _parse_entries(content, "Capabilities Built")
        assert entries == []

    def test_missing_section_returns_empty(self):
        entries = _parse_entries("# Just a title\n", "Capabilities Built")
        assert entries == []


class TestGetField:
    """Tests for _get_field helper."""

    def test_extracts_field(self):
        block = "### E001\n- **Name**: Test\n- **Usage Count**: 5\n"
        assert _get_field(block, "Usage Count") == "5"
        assert _get_field(block, "Name") == "Test"

    def test_missing_field_returns_none(self):
        block = "### E001\n- **Name**: Test\n"
        assert _get_field(block, "Usage Count") is None


def _write_recent_evolution_state(ctx_dir: Path) -> None:
    """Write a recent .evolution_last_run so _maybe_run_evolution skips.

    Without this, the hook tries to run the full evolution cycle against
    real transcript files (~1152 JSONL), which takes minutes in CI/test.
    """
    state_file = ctx_dir / ".evolution_last_run"
    state_file.write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
    )


class TestEvolutionMaintenanceHook:
    """Integration tests for the full hook lifecycle."""

    @pytest.mark.asyncio
    async def test_deprecates_idle_entry(self, tmp_path):
        """Active entry idle >30 days with 0 usage → deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(45), "usage": 0, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "deprecated" in content

        log_lines = changelog.read_text().strip().split("\n")
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry["action"] == "deprecate"
        assert entry["id"] == "E001"

    @pytest.mark.asyncio
    async def test_skips_entry_with_usage(self, tmp_path):
        """Active entry with usage_count > 0 is never deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(45), "usage": 5, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "deprecated" not in content.split("## Optimizations")[0]
        assert changelog.read_text().strip() == ""

    @pytest.mark.asyncio
    async def test_prunes_deprecated_entry(self, tmp_path):
        """Deprecated entry with 0 usage and old date → removed."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(60), "usage": 0, "status": "deprecated"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "E001" not in content

        log_lines = changelog.read_text().strip().split("\n")
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry["action"] == "prune"

    @pytest.mark.asyncio
    async def test_no_context_dir_is_noop(self, tmp_path):
        """Missing .context directory → silent no-op."""
        hook = EvolutionMaintenanceHook(context_dir=tmp_path / "nonexistent")
        await hook.execute(_make_context(tmp_path))
        # No crash = pass

    @pytest.mark.asyncio
    async def test_recent_entry_untouched(self, tmp_path):
        """Entry created 5 days ago → not deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(5), "usage": 0, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "active" in content.split("## Optimizations")[0]
        assert changelog.read_text().strip() == ""


class TestEvolutionWeeklyTrigger:
    """Tests for the weekly evolution cycle trigger logic.

    These tests mock ``run_evolution_cycle`` to avoid scanning real
    transcript files (1000+ JSONL files that hang tests for minutes).
    """

    def test_evolution_runs_after_7_days(self, tmp_path, monkeypatch):
        """Evolution cycle triggers when last run > 7 days ago."""
        from core.evolution_optimizer import CycleReport

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        state_file = ctx_dir / ".evolution_last_run"
        state_file.write_text(_days_ago(10), encoding="utf-8")

        # Mock the actual cycle to return CycleReport
        called = []
        def _mock_cycle(*args, **kwargs):
            called.append(True)
            return CycleReport(
                cycle_id="test",
                skills_checked=0,
                eligible=0,
            )

        monkeypatch.setattr(
            "core.evolution_optimizer.run_evolution_cycle", _mock_cycle
        )

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        hook._maybe_run_evolution(ctx_dir)

        assert len(called) == 1, "Evolution cycle should have been triggered"
        # State file should be updated to today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert state_file.read_text(encoding="utf-8").strip() == today

    def test_evolution_skips_if_recent(self, tmp_path):
        """Evolution cycle does NOT trigger when last run < 7 days ago."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        state_file = ctx_dir / ".evolution_last_run"
        state_file.write_text(_days_ago(2), encoding="utf-8")

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        hook._maybe_run_evolution(ctx_dir)

        # State file should NOT be updated
        assert state_file.read_text(encoding="utf-8") == _days_ago(2)

    def test_evolution_runs_if_no_state_file(self, tmp_path, monkeypatch):
        """Evolution cycle triggers when state file doesn't exist."""
        from core.evolution_optimizer import CycleReport

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        state_file = ctx_dir / ".evolution_last_run"
        assert not state_file.exists()

        called = []
        def _mock_cycle(*args, **kwargs):
            called.append(True)
            return CycleReport(
                cycle_id="test",
                skills_checked=0,
                eligible=0,
            )

        monkeypatch.setattr(
            "core.evolution_optimizer.run_evolution_cycle", _mock_cycle
        )

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        hook._maybe_run_evolution(ctx_dir)

        assert len(called) == 1, "Should trigger when no state file exists"

    def test_evolution_with_valid_skills_dir(self, tmp_path, monkeypatch):
        """Evolution cycle runs end-to-end when skills_dir exists."""
        from core.evolution_optimizer import CycleReport

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        state_file = ctx_dir / ".evolution_last_run"
        state_file.write_text(_days_ago(10), encoding="utf-8")

        called_with = []
        def _mock_cycle(skills_dir, transcripts_dir, evals_dir):
            called_with.append((skills_dir, transcripts_dir, evals_dir))
            return CycleReport(
                cycle_id="test",
                skills_checked=2,
                eligible=1,
                deployed=1,
            )

        monkeypatch.setattr(
            "core.evolution_optimizer.run_evolution_cycle", _mock_cycle
        )

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir)
        hook._maybe_run_evolution(ctx_dir)

        assert len(called_with) == 1
        # State file should be updated to today
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert state_file.read_text(encoding="utf-8").strip() == today
