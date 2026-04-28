"""Tests for DDD auto-apply mechanical proposals in ContextHealthHook.

TDD tests for _auto_apply_ddd_proposals():
1. Mechanical change (new table row appended) gets auto-applied to DDD doc
2. Semantic change (existing content modified) is NOT auto-applied
3. Proposal with confidence < 8 is NOT auto-applied
4. Changes targeting Non-Goals section are NOT auto-applied
5. Processed proposal file gets renamed to .applied
6. Original DDD doc content is preserved when no changes apply
"""

import json
from pathlib import Path

import pytest

from hooks.context_health_hook import ContextHealthHook


def _make_proposal(
    confidence: int = 9,
    target_doc: str = "TECH.md",
    section: str = "## TECH.md Updates",
    current_block: str = "| Feature | Status |\n|---------|--------|\n| Auth | Done |",
    proposed_block: str = "| Feature | Status |\n|---------|--------|\n| Auth | Done |\n| Cache | Done |",
) -> str:
    """Build a minimal DDD refresh proposal."""
    return (
        f"# DDD Refresh Proposal\n\n"
        f"**Confidence:** {confidence}/10\n\n"
        f"{section}\n\n"
        f"### 1. Update feature table\n\n"
        f"**Current:**\n```\n{current_block}\n```\n\n"
        f"**Proposed:**\n```\n{proposed_block}\n```\n"
    )


def _make_tech_md(content: str = "| Feature | Status |\n|---------|--------|\n| Auth | Done |") -> str:
    return f"# TECH.md\n\n## Features\n\n{content}\n\n## Architecture\n\nSome architecture.\n"


class TestDDDAutoApplyMechanical:
    """Mechanical change (new table row appended) gets auto-applied."""

    def test_mechanical_append_applied(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        tech_md = project_dir / "TECH.md"
        tech_md.write_text(_make_tech_md())

        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal())

        findings_dir = ws / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        findings_file = findings_dir / "health_findings.json"
        findings_file.write_text(json.dumps({"timestamp": "", "findings": [], "memory_health": None}))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        content = tech_md.read_text()
        assert "Cache" in content, "Mechanical append should be applied"


class TestDDDAutoApplySemanticSkipped:
    """Semantic change (existing content modified) is NOT auto-applied."""

    def test_semantic_change_not_applied(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        tech_md = project_dir / "TECH.md"
        tech_md.write_text(_make_tech_md())

        # Semantic: modifies existing "Auth | Done" to "Auth | Deprecated"
        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal(
            current_block="| Feature | Status |\n|---------|--------|\n| Auth | Done |",
            proposed_block="| Feature | Status |\n|---------|--------|\n| Auth | Deprecated |",
        ))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        content = tech_md.read_text()
        assert "Deprecated" not in content, "Semantic change should NOT be applied"
        assert "Done" in content, "Original content should remain"


class TestDDDAutoApplyLowConfidence:
    """Proposal with confidence < 8 is NOT auto-applied."""

    def test_low_confidence_not_applied(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        tech_md = project_dir / "TECH.md"
        tech_md.write_text(_make_tech_md())

        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal(confidence=5))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        content = tech_md.read_text()
        assert "Cache" not in content, "Low confidence proposal should NOT be applied"


class TestDDDAutoApplyNonGoalsSkipped:
    """Changes targeting Non-Goals section are NOT auto-applied."""

    def test_nongoals_not_applied(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        tech_md = project_dir / "TECH.md"
        tech_md.write_text("# TECH.md\n\n## Non-Goals\n\nNo mobile support.\n")

        # Targets Non-Goals section (even as a mechanical append)
        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal(
            current_block="No mobile support.",
            proposed_block="No mobile support.\nNo desktop app.",
            section="## TECH.md Updates\n\n_Targets: Non-Goals_",
        ))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        content = tech_md.read_text()
        assert "desktop app" not in content, "Non-Goals changes should NOT be applied"


class TestDDDAutoApplyRename:
    """Processed proposal file gets renamed to .applied."""

    def test_proposal_renamed(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        tech_md = project_dir / "TECH.md"
        tech_md.write_text(_make_tech_md())

        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal())

        findings_dir = ws / "Services" / "swarm-jobs"
        findings_dir.mkdir(parents=True)
        findings_file = findings_dir / "health_findings.json"
        findings_file.write_text(json.dumps({"timestamp": "", "findings": [], "memory_health": None}))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        assert not proposal.exists(), "Original proposal should be renamed"
        applied = artifacts_dir / "ddd-refresh-2026-04-28.md.applied"
        assert applied.exists(), "Proposal should be renamed to .applied"


class TestDDDAutoApplyPreservesOriginal:
    """Original DDD doc content is preserved when no changes apply."""

    def test_no_changes_preserves_doc(self, tmp_path):
        ws = tmp_path / "ws"
        project_dir = ws / "Projects" / "TestProject"
        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        original_content = _make_tech_md()
        tech_md = project_dir / "TECH.md"
        tech_md.write_text(original_content)

        # Low confidence = no apply
        proposal = artifacts_dir / "ddd-refresh-2026-04-28.md"
        proposal.write_text(_make_proposal(confidence=3))

        hook = ContextHealthHook()
        hook._auto_apply_ddd_proposals(ws)

        assert tech_md.read_text() == original_content, "Doc should be unchanged"
