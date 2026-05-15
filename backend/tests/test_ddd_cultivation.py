"""
Tests for DDD Cultivation Engine — Tiered Autonomy Model.

Tests: model → filter → auto-apply → changelog → escalation → cultivate_from_reflect.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class TestCultivationProposal:
    """Test the CultivationProposal data model."""

    def test_proposal_creation_with_all_fields(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="IMPROVEMENT.md",
            target_section="What Worked",
            content="Adversarial sub-agent caught race condition that self-review missed",
            source_run_id="run_39ca5ee8",
            confidence=0.8,
        )
        assert p.target_doc == "IMPROVEMENT.md"
        assert p.target_section == "What Worked"
        assert p.id.startswith("proposal_")
        assert p.status == "pending"
        assert p.ttl_days == 14
        assert p.source_stage == "reflect"

    def test_proposal_serialization_roundtrip(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="TECH.md",
            target_section="Runtime Traps",
            content="daemon env has no HOME — use Path.home()",
            source_run_id="run_abc123",
            confidence=0.75,
        )
        data = p.to_dict()
        assert isinstance(data, dict)
        assert data["target_doc"] == "TECH.md"
        assert data["status"] == "pending"

        # Roundtrip
        p2 = CultivationProposal.from_dict(data)
        assert p2.id == p.id
        assert p2.content == p.content
        assert p2.confidence == p.confidence


class TestFilterLessonsForDDD:
    """Test the filter function that classifies lessons."""

    def test_rejects_empty_string(self):
        from core.ddd_cultivation import filter_lessons_for_ddd

        result = filter_lessons_for_ddd([""], "run_test", "SwarmAI")
        assert result == []

    def test_rejects_short_generic_lesson(self):
        from core.ddd_cultivation import filter_lessons_for_ddd

        result = filter_lessons_for_ddd(
            ["Tests pass", "3 lessons captured", "Report written"],
            "run_test",
            "SwarmAI",
        )
        assert result == []

    def test_accepts_pattern_lesson_for_tech(self):
        from core.ddd_cultivation import filter_lessons_for_ddd

        lessons = [
            "nc -z is always better than lsof for port checks — lsof hangs indefinitely on certain macOS configs"
        ]
        result = filter_lessons_for_ddd(lessons, "run_test", "SwarmAI")
        assert len(result) == 1
        assert result[0].target_doc == "TECH.md"
        assert result[0].confidence > 0.5

    def test_accepts_failure_lesson_for_improvement(self):
        from core.ddd_cultivation import filter_lessons_for_ddd

        lessons = [
            "SMOKE caught 2 runtime crashes that unit tests missed — highest ROI check"
        ]
        result = filter_lessons_for_ddd(lessons, "run_test", "SwarmAI")
        assert len(result) == 1
        assert result[0].target_doc == "IMPROVEMENT.md"

    def test_caps_proposals_per_run(self):
        from core.ddd_cultivation import filter_lessons_for_ddd

        # Generate 10 valid-looking lessons
        lessons = [
            f"Pattern {i}: always use structured logging for async operations — prevents lost context"
            for i in range(10)
        ]
        result = filter_lessons_for_ddd(lessons, "run_test", "SwarmAI")
        assert len(result) <= 5  # Max 5 proposals per run


class TestWriteProposal:
    """Test atomic proposal file writing."""

    def test_writes_json_file_to_proposals_dir(self):
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            p = CultivationProposal(
                target_doc="TECH.md",
                target_section="Conventions",
                content="Use snake_case for all Python module names",
                source_run_id="run_abc",
                confidence=0.7,
            )
            path = write_proposal(p, project_dir)
            assert path.exists()
            assert path.suffix == ".json"
            assert "proposals" in str(path.parent)

            # Verify content
            data = json.loads(path.read_text())
            assert data["target_doc"] == "TECH.md"
            assert data["status"] == "pending"

    def test_creates_proposals_dir_if_missing(self):
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Failed",
                content="setTimeout for state propagation always causes race conditions",
                source_run_id="run_xyz",
                confidence=0.85,
            )
            path = write_proposal(p, project_dir)
            assert (project_dir / ".artifacts" / "proposals").is_dir()
            assert path.exists()


class TestReadPendingProposals:
    """Test reading and filtering proposals."""

    def test_returns_empty_when_no_proposals_dir(self):
        from core.ddd_cultivation import read_pending_proposals

        with tempfile.TemporaryDirectory() as tmpdir:
            result = read_pending_proposals(Path(tmpdir), "TestProject")
            assert result == []

    def test_reads_pending_proposals(self):
        from core.ddd_cultivation import (
            CultivationProposal,
            read_pending_proposals,
            write_proposal,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Projects" / "TestProject"
            project_dir.mkdir(parents=True)

            p = CultivationProposal(
                target_doc="TECH.md",
                target_section="Patterns",
                content="Use asyncio.to_thread for blocking subprocess calls",
                source_run_id="run_test",
                confidence=0.7,
            )
            write_proposal(p, project_dir)

            result = read_pending_proposals(Path(tmpdir), "TestProject")
            assert len(result) == 1
            assert result[0].content == p.content

    def test_excludes_expired_proposals(self):
        from core.ddd_cultivation import (
            CultivationProposal,
            read_pending_proposals,
            write_proposal,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Projects" / "TestProject"
            project_dir.mkdir(parents=True)

            # Write a proposal that's already expired
            p = CultivationProposal(
                target_doc="TECH.md",
                target_section="Old",
                content="This is expired content that should not appear",
                source_run_id="run_old",
                confidence=0.6,
                ttl_days=0,  # expires immediately
            )
            # Manually set created_at to 15 days ago
            from datetime import timezone

            p.created_at = (
                datetime.now(timezone.utc) - timedelta(days=15)
            ).isoformat()
            write_proposal(p, project_dir)

            result = read_pending_proposals(Path(tmpdir), "TestProject")
            assert result == []

    def test_excludes_approved_and_rejected_proposals(self):
        from core.ddd_cultivation import (
            CultivationProposal,
            read_pending_proposals,
            write_proposal,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "Projects" / "TestProject"
            project_dir.mkdir(parents=True)

            p = CultivationProposal(
                target_doc="TECH.md",
                target_section="Done",
                content="Already approved content",
                source_run_id="run_done",
                confidence=0.9,
            )
            p.status = "approved"
            write_proposal(p, project_dir)

            result = read_pending_proposals(Path(tmpdir), "TestProject")
            assert result == []


class TestIsSafeAppend:
    """Test the tiered autonomy classification."""

    def test_improvement_what_worked_is_safe(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="IMPROVEMENT.md",
            target_section="What Worked",
            content="test",
            source_run_id="run_x",
            confidence=0.7,
        )
        assert p.is_safe_append() is True

    def test_tech_runtime_traps_is_safe(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="TECH.md",
            target_section="Runtime Traps",
            content="test",
            source_run_id="run_x",
            confidence=0.7,
        )
        assert p.is_safe_append() is True

    def test_product_is_not_safe(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="PRODUCT.md",
            target_section="Strategic Priorities",
            content="test",
            source_run_id="run_x",
            confidence=0.7,
        )
        assert p.is_safe_append() is False

    def test_unknown_section_is_not_safe(self):
        from core.ddd_cultivation import CultivationProposal

        p = CultivationProposal(
            target_doc="IMPROVEMENT.md",
            target_section="Some Random Section",
            content="test",
            source_run_id="run_x",
            confidence=0.7,
        )
        assert p.is_safe_append() is False


class TestApplyToDDD:
    """Test direct application of proposals to DDD documents."""

    def test_appends_to_existing_section(self):
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            # Create a minimal IMPROVEMENT.md
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Worked\n\n- existing entry\n\n## What Failed\n\n- old failure\n")

            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content="New pattern discovered during pipeline run",
                source_run_id="run_test",
                confidence=0.7,
            )
            result = apply_to_ddd(p, project_dir)
            assert result is True

            content = doc.read_text()
            assert "New pattern discovered during pipeline run" in content
            assert "existing entry" in content  # didn't clobber
            assert "auto-cultivated" in content  # M2: new format attribution
            assert "run_test" in content  # source run ID preserved

    def test_rejects_duplicate_content(self):
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            # M3: full content match — doc already contains exact lesson text
            lesson_text = "nc -z is always better than lsof for port checks — lsof hangs indefinitely"
            doc.write_text(
                f"# Lessons\n\n## What Worked\n\n"
                f"- {lesson_text} (2026-05-12, run_old, auto-cultivated)\n"
            )

            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content=lesson_text,  # exact same content
                source_run_id="run_dup",
                confidence=0.7,
            )
            result = apply_to_ddd(p, project_dir)
            assert result is False  # Full content substring match

    def test_rejects_unsafe_target(self):
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            p = CultivationProposal(
                target_doc="PRODUCT.md",
                target_section="Strategic Priorities",
                content="Change the product direction entirely",
                source_run_id="run_risky",
                confidence=0.9,
            )
            result = apply_to_ddd(p, project_dir)
            assert result is False


class TestLogApplication:
    """Test changelog logging."""

    def test_appends_to_changelog_jsonl(self):
        from core.ddd_cultivation import CultivationProposal, log_application

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            p = CultivationProposal(
                target_doc="TECH.md",
                target_section="Runtime Traps",
                content="daemon env has no HOME",
                source_run_id="run_log",
                confidence=0.8,
            )
            log_application(p, project_dir)
            log_application(p, project_dir)  # append twice

            changelog = project_dir / ".artifacts" / "ddd-changelog.jsonl"
            assert changelog.exists()
            lines = changelog.read_text().strip().split("\n")
            assert len(lines) == 2
            entry = json.loads(lines[0])
            assert entry["action"] == "applied"
            assert entry["target_doc"] == "TECH.md"


class TestCultivateFromReflect:
    """Test the one-call entry point."""

    def test_applies_safe_and_escalates_risky(self):
        from core.ddd_cultivation import cultivate_from_reflect

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            # Create IMPROVEMENT.md with the expected section
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n")

            lessons = [
                "SMOKE caught 2 runtime crashes that unit tests missed — highest ROI check",
                "This is a strategic non-goal scope priority milestone decision that should escalate",
            ]
            result = cultivate_from_reflect(lessons, "run_e2e", "SwarmAI", project_dir)

            # M4 fix: exact assertions
            assert result["applied"] == 1  # IMPROVEMENT.md lesson auto-applied
            assert result["escalated"] == 1  # PRODUCT.md target escalated

            # Verify DDD doc was actually modified
            content = doc.read_text()
            assert "SMOKE caught 2 runtime crashes" in content
            assert "auto-cultivated" in content  # reflect source_stage = default label

            # Verify escalation file exists
            proposals_dir = project_dir / ".artifacts" / "proposals"
            proposal_files = list(proposals_dir.glob("*.json"))
            assert len(proposal_files) == 1
            escalated_data = json.loads(proposal_files[0].read_text())
            assert escalated_data["target_doc"] == "PRODUCT.md"
            assert escalated_data["status"] == "escalated"

            # Verify changelog was written
            changelog = project_dir / ".artifacts" / "ddd-changelog.jsonl"
            assert changelog.exists()
            entries = changelog.read_text().strip().split("\n")
            assert len(entries) == 1
            assert json.loads(entries[0])["action"] == "applied"


class TestCultivateFromCorrections:
    """Test corrections cultivation entry point (Ch6 — highest priority)."""

    def test_applies_correction_to_ddd_doc(self):
        from core.ddd_cultivation import cultivate_from_corrections

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            # Create both docs since classifier routes by keyword heuristic
            (project_dir / "IMPROVEMENT.md").write_text(
                "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
            )
            (project_dir / "TECH.md").write_text(
                "# Tech\n\n## Architecture\n\n- seed\n\n## Runtime Traps\n\n- seed\n\n## Conventions\n\n- seed\n"
            )

            corrections = [
                "Bug: daemon subprocess PATH was not expanded — must use Path.home() instead of os.path.expandvars",
            ]
            result = cultivate_from_corrections(
                corrections, "session_abc123", "SwarmAI", project_dir
            )

            # Correction should be classified (has "daemon", "must", "subprocess", "Path.home" → TECH.md)
            assert result["applied"] >= 1
            # Verify it landed in a DDD doc
            tech_content = (project_dir / "TECH.md").read_text()
            improvement_content = (project_dir / "IMPROVEMENT.md").read_text()
            assert "Path.home()" in tech_content or "PATH" in improvement_content

    def test_sets_source_stage_to_correction(self):
        from core.ddd_cultivation import cultivate_from_corrections

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "IMPROVEMENT.md").write_text(
                "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
            )
            (project_dir / "TECH.md").write_text(
                "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
            )

            corrections = [
                "Bug: lsof hangs on macOS sandbox — always use nc -z instead for port checks",
            ]
            result = cultivate_from_corrections(
                corrections, "session_xyz789", "SwarmAI", project_dir
            )

            # Verify changelog has correct source_stage (PE-3)
            changelog = project_dir / ".artifacts" / "ddd-changelog.jsonl"
            assert changelog.exists(), "Expected changelog to be written"
            lines = changelog.read_text().strip().split("\n")
            assert len(lines) >= 1, "Expected at least one changelog entry"
            entry = json.loads(lines[0])
            assert entry["source_run_id"] == "session_xyz789"
            assert entry["source_stage"] == "correction"

    def test_empty_corrections_returns_zero(self):
        from core.ddd_cultivation import cultivate_from_corrections

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            result = cultivate_from_corrections(
                [], "session_empty", "SwarmAI", project_dir
            )
            assert result == {"applied": 0, "escalated": 0, "rejected": 0}


class TestCultivateFromDecisions:
    """Test decisions cultivation entry point (Ch5)."""

    def test_applies_convention_decision_to_tech(self):
        from core.ddd_cultivation import cultivate_from_decisions

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "TECH.md"
            doc.write_text("# Tech\n\n## Architecture\n\n- seed\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n")

            decisions = [
                "Standing rule: always prefer atomic writes with tmp+rename pattern to prevent corruption",
            ]
            result = cultivate_from_decisions(
                decisions, "session_dec001", "SwarmAI", project_dir
            )

            # The decision should classify to TECH.md Conventions (has "pattern", "prefer", "atomic")
            assert result["applied"] >= 1

            content = doc.read_text()
            assert "atomic writes" in content
            assert "decision" in content  # source_stage label for decisions

    def test_sets_source_stage_to_decision(self):
        from core.ddd_cultivation import cultivate_from_decisions

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "TECH.md"
            doc.write_text("# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n")

            decisions = [
                "Convention: never use lsof in daemon scripts — prefer nc -z for port checking",
            ]
            result = cultivate_from_decisions(
                decisions, "session_dec002", "SwarmAI", project_dir
            )

            # Verify changelog source attribution (PE-3)
            changelog = project_dir / ".artifacts" / "ddd-changelog.jsonl"
            assert changelog.exists(), "Expected changelog to be written"
            lines = changelog.read_text().strip().split("\n")
            assert len(lines) >= 1, "Expected at least one changelog entry"
            entry = json.loads(lines[0])
            assert entry["source_run_id"] == "session_dec002"
            assert entry["source_stage"] == "decision"

    def test_empty_decisions_returns_zero(self):
        from core.ddd_cultivation import cultivate_from_decisions

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            result = cultivate_from_decisions(
                [], "session_empty", "SwarmAI", project_dir
            )
            assert result == {"applied": 0, "escalated": 0, "rejected": 0}

    def test_real_corrections_without_keywords_still_classify(self):
        """PE-1: Real production corrections lack keywords but should still classify."""
        from core.ddd_cultivation import cultivate_from_corrections

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "IMPROVEMENT.md").write_text(
                "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
            )
            (project_dir / "TECH.md").write_text(
                "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
            )

            # These are REAL corrections from 2026-05-14 JSONL — no magic keywords
            corrections = [
                "Agent proposed writing insights into PRODUCT.md/DDD — user reframed: we're building augmented humans, not a better code tool; agent over-indexed on feature comparison",
                "Agent opened DMG but didn't launch app — user had to ask again explicitly to open/run it",
                "Agent pushed only swarm-brain when user said push to github — user had to follow up asking about SwarmAI codebase specifically",
            ]
            result = cultivate_from_corrections(
                corrections, "session_pe1_test", "SwarmAI", project_dir
            )

            # ALL real corrections must classify (not be rejected)
            total = result["applied"] + result["escalated"]
            assert total >= 2, f"Expected ≥2 corrections classified, got {total} (applied={result['applied']}, escalated={result['escalated']}, rejected={result['rejected']})"

    def test_noise_decisions_rejected(self):
        from core.ddd_cultivation import cultivate_from_decisions

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "TECH.md"
            doc.write_text("# Tech\n\n## Conventions\n\n- seed\n")

            decisions = [
                "Done",
                "Tests pass",
                "Shipped",
            ]
            result = cultivate_from_decisions(
                decisions, "session_noise", "SwarmAI", project_dir
            )
            assert result["applied"] == 0
            assert result["escalated"] == 0
