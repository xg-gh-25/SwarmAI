"""Tests for the EVOLUTION.md quality gate in EvolutionMaintenanceHook.

TDD tests for _quality_gate():
1. Garbage competence (<20 chars) is removed
2. Commit-hash-only competence is removed
3. Valid competence is NOT removed
4. Duplicate correction IDs are renumbered (second C011 -> C012)
5. Changes are logged to changelog
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hooks.evolution_maintenance_hook import (
    EvolutionMaintenanceHook,
    _parse_entries,
    _append_changelog,
)
from core.session_hooks import HookContext


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _make_context(tmp: Path) -> HookContext:
    return HookContext(
        session_id="test-session",
        agent_id="default",
        message_count=10,
        session_start_time="2026-03-01T00:00:00Z",
        session_title="Test",
    )


def _write_recent_evolution_state(ctx_dir: Path) -> None:
    """Write a recent .evolution_last_run so _maybe_run_evolution skips."""
    state_file = ctx_dir / ".evolution_last_run"
    state_file.write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
    )


def _make_evolution_with_competence(entries: list[dict], corrections: list[dict] | None = None) -> str:
    """Build EVOLUTION.md with Competence Learned and Corrections Captured sections."""
    lines = [
        "# SwarmAI Evolution Registry\n\n",
        "## Capabilities Built\n\n_None._\n\n",
        "## Optimizations Learned\n\n_None._\n\n",
        "## Corrections Captured\n\n",
    ]
    if corrections:
        for c in corrections:
            lines.append(
                f"### {c['id']} | reactive | correction | {c['date']}\n"
                f"- **Correction**: {c['text']}\n"
                f"- **Status**: active\n\n"
            )
    else:
        lines.append("_None._\n\n")

    lines.append("## Competence Learned\n\n")
    for e in entries:
        lines.append(
            f"### {e['id']} | {e.get('extra', 'reactive | skill |')} {e['date']}\n"
            f"- **Competence**: {e['desc']}\n"
            f"- **Status**: {e.get('status', 'active')}\n"
            f"- **Usage Count**: {e.get('usage', 0)}\n\n"
        )

    lines.append("## Failed Evolutions\n\n_None._\n")
    return "".join(lines)


class TestQualityGateGarbageCompetence:
    """Garbage competence (<20 chars) is removed."""

    @pytest.mark.asyncio
    async def test_short_competence_removed(self, tmp_path):
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_with_competence([
            {"id": "K001", "date": _days_ago(5), "desc": "short"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "K001" not in content, "Short competence (<20 chars) should be removed"


class TestQualityGateCommitHashCompetence:
    """Commit-hash-only competence is removed."""

    @pytest.mark.asyncio
    async def test_commit_hash_competence_removed(self, tmp_path):
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_with_competence([
            {"id": "K001", "date": _days_ago(5), "desc": "abc1234 some commit hash entry that is long enough"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "K001" not in content, "Commit-hash-starting competence should be removed"


class TestQualityGateValidCompetence:
    """Valid competence is NOT removed."""

    @pytest.mark.asyncio
    async def test_valid_competence_preserved(self, tmp_path):
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_with_competence([
            {"id": "K001", "date": _days_ago(5), "desc": "The locked_write pattern uses fcntl for atomic file operations"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "K001" in content, "Valid competence should be preserved"


class TestQualityGateDuplicateCorrections:
    """Duplicate correction IDs are renumbered."""

    @pytest.mark.asyncio
    async def test_duplicate_correction_ids_renumbered(self, tmp_path):
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_with_competence(
            entries=[],
            corrections=[
                {"id": "C011", "date": _days_ago(10), "text": "First correction about error handling"},
                {"id": "C011", "date": _days_ago(5), "text": "Second correction about logging patterns"},
            ],
        ))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "C011" in content, "First C011 should remain"
        assert "C012" in content, "Second duplicate C011 should be renumbered to C012"


class TestQualityGateChangelog:
    """Changes are logged to changelog."""

    @pytest.mark.asyncio
    async def test_removals_logged_to_changelog(self, tmp_path):
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_with_competence([
            {"id": "K001", "date": _days_ago(5), "desc": "short"},
            {"id": "K002", "date": _days_ago(5), "desc": "The locked_write pattern uses fcntl for atomic file operations"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        log_lines = [l for l in changelog.read_text().strip().split("\n") if l.strip()]
        assert len(log_lines) >= 1, "At least one changelog entry for garbage removal"
        entry = json.loads(log_lines[0])
        assert entry["action"] == "quality_gate_remove"
        assert entry["id"] == "K001"
