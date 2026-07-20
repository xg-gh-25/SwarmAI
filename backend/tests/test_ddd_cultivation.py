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
            assert result == "applied"

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
            assert result == "duplicate"  # Full content substring match

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
            assert result == "not_safe"

    def test_missing_whitelisted_section_is_auto_created(self):
        """Structural drift fix (run_45ab67c7, user-chosen): when a whitelisted
        section heading is ABSENT, apply_to_ddd CREATES it at end-of-doc and
        writes the entry — the lesson is NEVER dropped. The section name is
        trusted (from ROUTING_TABLE via SAFE_APPEND_SECTIONS), so creating it is
        safe. Returns 'created_section' (observable, not silent)."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            # Doc exists, but the whitelisted 'What Worked' heading is absent.
            doc.write_text("# Lessons\n\n## What Failed\n\n- old failure\n")
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",  # whitelisted, but missing here
                content="A genuinely new lesson",
                source_run_id="run_drift",
                confidence=0.7,
            )
            assert apply_to_ddd(p, project_dir) == "created_section"
            content = doc.read_text()
            # Heading was created AND the lesson written under it (not dropped).
            assert "## What Worked" in content
            assert "A genuinely new lesson" in content
            assert "- old failure" in content  # existing content preserved

            # Duplicate content in an EXISTING section = benign no-op.
            doc.write_text(
                "# Lessons\n\n## What Worked\n\n"
                "- dup lesson (2026-01-01, run_x, auto-cultivated)\n"
            )
            p2 = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content="dup lesson",
                source_run_id="run_dup2",
                confidence=0.7,
            )
            assert apply_to_ddd(p2, project_dir) == "duplicate"

    def test_duplicate_check_is_section_scoped_not_whole_doc(self):
        """Adversarial HIGH: duplicate detection must be scoped to the TARGET
        section, not the whole document. The same text in a DIFFERENT section is
        NOT a duplicate for this section — dropping it would lose a real lesson."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            # Same text already exists, but under a DIFFERENT section.
            doc.write_text(
                "# L\n\n## What Failed\n\n"
                "- shared insight text (2026-01-01, run_x, auto-cultivated)\n\n"
                "## What Worked\n\n- unrelated\n"
            )
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",  # different section than the match
                content="shared insight text",
                source_run_id="run_new",
                confidence=0.7,
            )
            # Must NOT be treated as duplicate — it's new to 'What Worked'.
            assert apply_to_ddd(p, project_dir) == "applied"
            assert doc.read_text().count("shared insight text") == 2

    def test_duplicate_check_not_fooled_by_substring(self):
        """Adversarial MED: a short lesson that is a SUBSTRING of an existing
        longer bullet is a distinct lesson, not a duplicate."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text(
                "# L\n\n## What Worked\n\n"
                "- invalidate the cache on write because stale reads corrupt state "
                "(2026-01-01, run_x, auto-cultivated)\n"
            )
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content="invalidate the cache on write",  # substring of existing
                source_run_id="run_new",
                confidence=0.7,
            )
            assert apply_to_ddd(p, project_dir) == "applied"

    def test_exact_duplicate_in_section_still_detected(self):
        """Regression: a genuinely-identical lesson in the SAME section is still
        a duplicate (idempotency preserved after the scoping fix)."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text(
                "# L\n\n## What Worked\n\n"
                "- exact same lesson (2026-01-01, run_x, auto-cultivated)\n"
            )
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content="exact same lesson",
                source_run_id="run_dup",
                confidence=0.7,
            )
            assert apply_to_ddd(p, project_dir) == "duplicate"

    def test_applied_returns_status_string(self):
        """Successful append returns 'applied' (status contract, not bool True)."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Worked\n\n- existing\n")
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Worked",
                content="brand new lesson text",
                source_run_id="run_ok",
                confidence=0.7,
            )
            assert apply_to_ddd(p, project_dir) == "applied"


class TestContentSignature:
    """content_signature() — format-agnostic normalizer that lets the two
    IMPROVEMENT.md writers (cultivation + writeback hook) dedup against each
    other. THE root fix: writeback's `- **DATE** (session xxx): text` and
    cultivation's `- text (date, run, label)` must normalize to the SAME
    signature for the same lesson text. (Gate-1 killer finding: the old
    _extract_bullet_content strips only the TRAILING paren, so the writeback
    prefix survives → the two never matched → 43K-corpus dup was a no-op.)"""

    def test_cross_format_same_text_same_signature(self):
        """The load-bearing invariant: same lesson text in BOTH bullet formats
        → identical signature. This is what makes cross-writer dedup work."""
        from core.ddd_cultivation import content_signature

        text = "Both are valid workspace-relative paths that the resolver handles"
        cultivation_fmt = f"- {text} (2026-06-08, run_abc123, auto-cultivated)"
        writeback_fmt = f"- **2026-06-08** (session f1f7201b): {text}"
        assert content_signature(cultivation_fmt) == content_signature(writeback_fmt)

    def test_type_prefix_stripped(self):
        """A [type]-prefixed cultivation bullet normalizes to the same sig as
        the bare text."""
        from core.ddd_cultivation import content_signature

        text = "prevention over recovery beats runtime error handling"
        typed = f"- [pitfall] **{text}** (2026-01-01, run_x, auto-cultivated)"
        # bold-title cultivation form and plain form share the core signature
        assert content_signature(typed) == content_signature(f"- {text}")

    def test_distinct_text_distinct_signature(self):
        """Guard against over-collision: genuinely different lessons that share
        a common opening stem must NOT collapse to the same signature (Gate-1
        #2 — whole-string normalize, NOT first-N-chars)."""
        from core.ddd_cultivation import content_signature

        a = "- Lesson learned: always verify the remote ref with gh api not local"
        b = "- Lesson learned: always run the full build before pushing to main"
        assert content_signature(a) != content_signature(b)

    def test_whitespace_and_case_normalized(self):
        """Signature is case- and whitespace-insensitive (cosmetic diffs are not
        distinct lessons)."""
        from core.ddd_cultivation import content_signature

        assert content_signature("- The  Fix   Works") == content_signature("- the fix works")

    def test_writeback_prefix_stripped_for_dashed_uuid_session(self):
        """Gate-2 hardening: the writeback front-prefix must strip regardless of
        session-id shape (8-hex today, but a full dashed UUID or longer slice must
        not silently survive → the two formats would stop deduping). Whole point
        is robustness to a future context.session_id[:N] change."""
        from core.ddd_cultivation import content_signature

        text = "prevention over recovery beats runtime error handling"
        cultivation_fmt = f"- {text} (2026-06-08, run_abc123, auto-cultivated)"
        dashed_uuid = f"- **2026-06-08** (session 0024aab4-dfd3-459f-8a1b-deadbeef0001): {text}"
        assert content_signature(dashed_uuid) == content_signature(cultivation_fmt)


class TestCrossFormatDedup:
    """AC2: apply_to_ddd dedup catches a duplicate even when the existing entry
    is in the WRITEBACK format (the 43K-corpus shape). Mutation target: if the
    dedup does not signature-normalize the EXISTING bullets, a writeback-format
    dup slips through."""

    def test_writeback_format_existing_blocks_cultivation_dup(self):
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd

        text = "governance co-authorship erodes compliance over time"
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            # Existing entry is in the WRITEBACK format (the shape that silted
            # the 43K archive) — a naive exact-string dedup misses it.
            doc.write_text(
                "# L\n\n## What Failed\n\n"
                f"- **2026-06-08** (session abc12345): {text}\n"
            )
            p = CultivationProposal(
                target_doc="IMPROVEMENT.md",
                target_section="What Failed",
                content=text,
                source_run_id="run_dup2",
                confidence=0.7,
            )
            assert apply_to_ddd(p, project_dir) == "duplicate"


class TestSafeAppendSectionsExistInDocs:
    """Drift guard (AC3, PIT28 single-source pattern): every section the
    cultivation engine appends to. Since apply_to_ddd now AUTO-CREATES a missing
    whitelisted section (drift is self-healing, no longer a silent drop), this is
    a HYGIENE check for the PRIMARY project (SwarmAI): its canonical sections
    should already exist so cultivation appends in place rather than triggering a
    surprise auto-create. Derived from SAFE_APPEND_SECTIONS, never hardcoded."""

    def test_swarmai_canonical_sections_present_no_surprise_autocreate(self):
        import re
        from core.ddd_cultivation import SAFE_APPEND_SECTIONS

        import pytest
        # Resolve the workspace the SAME way production does — never hardcode a
        # developer-machine path (that would make the check pass VACUOUSLY in CI
        # where the path is absent). Adversarial MED.
        from core.initialization_manager import initialization_manager
        workspace = Path(initialization_manager.get_cached_workspace_path())
        project_dir = workspace / "Projects" / "SwarmAI"
        if not project_dir.exists():
            pytest.skip(f"SwarmAI project dir not present at {project_dir} — "
                        "hygiene check cannot run (skip != vacuous pass)")
        missing = []
        checked = 0  # non-vacuous guard: at least one section must be verified
        for doc_name, sections in SAFE_APPEND_SECTIONS.items():
            doc_path = project_dir / doc_name
            if not doc_path.exists():
                continue
            content = doc_path.read_text(encoding="utf-8")
            for section in sections:
                checked += 1
                section_re = re.compile(
                    r"^## " + re.escape(section) + r"\s*$", re.MULTILINE
                )
                if not section_re.search(content):
                    missing.append(f"{doc_name} § '{section}'")
        assert checked > 0, (
            "Hygiene check verified ZERO sections — vacuous. SAFE_APPEND_SECTIONS "
            f"({SAFE_APPEND_SECTIONS}) or the target docs are missing under {project_dir}.")
        # SwarmAI is the primary project — its canonical sections should exist so
        # cultivation appends in place (a miss here = a surprise auto-create, which
        # is safe but signals the SwarmAI template drifted from ROUTING_TABLE).
        assert not missing, (
            f"SwarmAI is missing canonical sections {missing} — cultivation will "
            "auto-create them (safe, but reconcile the template/ROUTING_TABLE).")


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
            assert result == {"applied": 0, "escalated": 0, "rejected": 0, "retired": 0, "drift_errors": []}


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
            assert result == {"applied": 0, "escalated": 0, "rejected": 0, "retired": 0, "drift_errors": []}

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


# --- M2: lesson quality gate + authoritative-zone write-protect (run_123a6530) ---

class TestLessonQualityGate:
    """M2: auto-cultivation must reject instance-logs, require generalizable
    class-tagged sentences, and never write to authoritative zones."""

    def test_instance_log_rejected(self):
        from core.ddd_cultivation import is_quality_lesson
        # stdout fragment / instance log / slip — NOT a generalizable lesson
        assert is_quality_lesson("stdout: foo=3 bar=7") is False
        assert is_quality_lesson("EXIT:0") is False
        assert is_quality_lesson("run_abc123 completed in 4.2s") is False

    def test_no_sentence_rejected(self):
        from core.ddd_cultivation import is_quality_lesson
        # fragments without a complete sentence
        assert is_quality_lesson("done") is False
        assert is_quality_lesson("tests pass") is False

    def test_generalizable_sentence_accepted(self):
        from core.ddd_cultivation import is_quality_lesson
        # a real lesson: generalizable claim, complete sentence
        assert is_quality_lesson(
            "Always verify a sub-agent's factual claims against the code before "
            "acting on them, because the verdict can be right while a cited number is wrong."
        ) is True

    def test_first_person_narration_rejected(self):
        from core.ddd_cultivation import is_quality_lesson
        # The EXACT conversational fragments that leaked into IMPROVEMENT.md via
        # improvement_writeback_hook keyword-matching (run_d7cb3941). Process-chatter
        # that keyword-matches ("root cause", "diagnose") but teaches nothing.
        assert is_quality_lesson("I have enough to diagnose the root cause with confidence") is False
        assert is_quality_lesson("This crosses your threshold → I'll diagnose root cause, then open a fix run") is False
        assert is_quality_lesson("I'll diagnose the root cause and open a fix run") is False
        assert is_quality_lesson("Let me check the root cause of this regression") is False
        assert is_quality_lesson("Now I'll verify the failed assertion") is False

    def test_plural_imperative_lessons_accepted(self):
        from core.ddd_cultivation import is_quality_lesson
        # Gate-2 finding C: "We should/need …" is a LEGITIMATE lesson voice, NOT
        # narration — must NOT be rejected (silent knowledge-loss > filtered noise).
        assert is_quality_lesson("We should always validate input at the boundary layer") is True
        assert is_quality_lesson("We need to add a lock spanning the whole read-modify-write") is True

    def test_real_root_cause_lesson_still_accepted(self):
        from core.ddd_cultivation import is_quality_lesson
        # MUST NOT false-negative a genuine root-cause lesson (narration guard is
        # anchored at the START, so a factual claim about a root cause still passes).
        assert is_quality_lesson(
            "Root cause: the WAL file never shrinks because no code path runs "
            "wal_checkpoint(TRUNCATE); PASSIVE autocheckpoint only resets the header."
        ) is True
        assert is_quality_lesson(
            "The regression broke because the mapper dropped a backend field, "
            "silently disabling the downstream visibility filter."
        ) is True

    def test_authoritative_zone_blocks_autocultivation(self):
        from core.ddd_cultivation import is_protected_zone
        # NEGATIVE: auto-cultivation must be structurally blocked from these
        assert is_protected_zone("TECH.md", "Architecture") is True
        assert is_protected_zone("PRODUCT.md", "Vision") is True
        assert is_protected_zone("PRODUCT.md", "Non-Goals") is True
        # SELF.md is fully protected (human/distill only)
        assert is_protected_zone("SELF.md", "anything") is True
        # normal append target is NOT protected
        assert is_protected_zone("IMPROVEMENT.md", "What Failed") is False

    def test_classify_lesson_rejects_instance_log(self):
        # Integration: the choke point _classify_lesson rejects an instance-log
        # even if it is long enough to pass MIN_LESSON_LENGTH.
        from core.ddd_cultivation import _classify_lesson
        assert _classify_lesson("stdout: foo=3 bar=7 baz=9 qux=11 extra padding here") is None

    def test_regex_does_not_overmatch_prose(self):
        # Adversarial: prose that MENTIONS log-ish phrases mid-sentence must NOT
        # be dropped — err toward accepting real lessons.
        from core.ddd_cultivation import is_quality_lesson
        assert is_quality_lesson(
            "The build completed in 4s which is faster than before so caching works") is True
        assert is_quality_lesson(
            "The service exits 0 on success and 1 on any validation failure here") is True
        assert is_quality_lesson(
            "The returncode handling pattern is brittle and should be refactored") is True

    def test_architecture_zone_blocks_auto_apply_integration(self):
        # Adversarial #2 (load-bearing): TECH.md/Architecture IS in
        # SAFE_APPEND_SECTIONS, so without the zone guard it would auto-apply.
        # is_safe_append MUST return False for it (escalate, never auto-apply).
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(
            target_doc="TECH.md", target_section="Architecture",
            content="x" * 40, source_run_id="r", confidence=0.9)
        assert p.is_safe_append() is False


class TestEvidenceDrivenRetire:
    """run_b8f10185 — evidence-driven DELETE/REWRITE: supersession detection,
    entry-locator, retire proposal, apply_retire_proposal, backward-compat.

    All tests drive the REAL functions (no mock of the code under change) —
    _detect_supersession/_locate_target_entry are pure; apply_retire_proposal
    exercises the real retire_entry against a real temp doc (PIT13/GUI32: prove
    the behavior, don't assert a mocked shape).
    """

    # ── AC1: supersession language → change_type='retire', not append ──────────
    def test_detect_supersession_positive(self):
        from core.ddd_cultivation import _detect_supersession
        assert _detect_supersession("The vector recall leg is no longer used — torn out")
        assert _detect_supersession("This replaces the old hybrid scorer entirely")
        assert _detect_supersession("The 0.7 threshold claim was wrong; it's actually 0.15")
        assert _detect_supersession("recall 现在不再使用 vector leg,已废弃")
        assert _detect_supersession("This supersedes the prior COE06 diagnosis")

    def test_detect_supersession_negative(self):
        from core.ddd_cultivation import _detect_supersession
        # Additive lessons — no supersession marker → False (AC4)
        assert not _detect_supersession("Adversarial sub-agent caught a race condition")
        assert not _detect_supersession("Gate-1 plan-attack found 2 blockers before code")
        assert not _detect_supersession("")
        assert not _detect_supersession(None)

    # ── AC2: locator returns the EXACT parsed (title, section) ─────────────────
    def test_locate_target_entry_finds_real_entry(self, tmp_path):
        from core.ddd_cultivation import _locate_target_entry
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text(
            "## What Worked\n\n"
            "- [guideline] **Vector recall hybrid scorer works well** — the 0.6v+0.4k blend\n\n"
            "- [guideline] **Session resume race condition fixed** — DEAD idempotent recovery\n\n"
            "## What Failed\n\n"
            "- [pitfall] **Unrelated topic here** — something about frontend rendering\n",
            encoding="utf-8",
        )
        # Lesson refutes the vector recall entry — strong unambiguous overlap.
        located = _locate_target_entry(
            "The vector recall hybrid scorer is no longer used — torn out",
            "IMPROVEMENT.md", tmp_path,
        )
        assert located is not None
        title, section, confident = located
        assert "Vector recall hybrid scorer" in title
        assert section == "What Worked"
        # ≥3 overlap (vector/recall/hybrid/scorer), ≥60% coverage, clear margin
        # over the unrelated runner-up, non-keep-class → confident (auto-eligible).
        assert confident is True

    def test_locate_target_entry_weak_overlap_returns_none(self, tmp_path):
        """<2 token overlap → None → append (never retire on a weak guess)."""
        from core.ddd_cultivation import _locate_target_entry
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text(
            "## What Worked\n\n- [guideline] **Frontend reconcile race fix** — store authority\n",
            encoding="utf-8",
        )
        located = _locate_target_entry(
            "Something totally unrelated about database indexing performance now replaces old",
            "IMPROVEMENT.md", tmp_path,
        )
        assert located is None

    def test_locate_target_entry_no_doc_returns_none(self, tmp_path):
        from core.ddd_cultivation import _locate_target_entry
        assert _locate_target_entry("x replaces y no longer", "NOPE.md", tmp_path) is None

    # ── AC1+AC2+AC4: full filter path produces retire vs append ────────────────
    def test_filter_produces_retire_proposal(self, tmp_path):
        from core.ddd_cultivation import filter_lessons_for_ddd, _classify_lesson
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text(
            "## What Worked\n\n"
            "- [guideline] **Adversarial gate scorer approach worked** — caught race conditions\n",
            encoding="utf-8",
        )
        # Lesson must (a) classify to a doc/section (routing keyword 'adversarial'
        # → IMPROVEMENT) AND (b) carry supersession language AND (c) share ≥2 topic
        # tokens with the seeded entry title. Precondition-assert (a) so the test
        # can't silently go vacuous if routing changes.
        lesson = ("The adversarial gate scorer approach is no longer used — "
                  "the old approach was wrong, superseded by mutation testing")
        assert _classify_lesson(lesson, project="SwarmAI") is not None
        proposals = filter_lessons_for_ddd([lesson], "run_test", "SwarmAI", tmp_path)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.change_type == "retire"
        assert p.target_title  # non-empty located title
        assert "Adversarial gate scorer approach" in p.target_title
        assert p.evidence  # verbatim lesson captured

    def test_filter_additive_lesson_stays_append(self, tmp_path):
        """AC4: no supersession marker → append even with project_dir passed."""
        from core.ddd_cultivation import filter_lessons_for_ddd
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("## What Worked\n\n- [guideline] **Some entry** — text\n", encoding="utf-8")
        lessons = ["Adversarial Gate-2 caught a CRITICAL data-loss bug that unit tests missed"]
        proposals = filter_lessons_for_ddd(lessons, "run_test", "SwarmAI", tmp_path)
        assert len(proposals) == 1
        assert proposals[0].change_type == "append"
        assert proposals[0].target_title == ""

    def test_filter_no_project_dir_never_retires(self, tmp_path):
        """Backward-compat: omitting project_dir → pure append behavior even on
        supersession language (no locator without a doc to read)."""
        from core.ddd_cultivation import filter_lessons_for_ddd
        lessons = ["The old approach is no longer valid and was wrong, superseded entirely"]
        proposals = filter_lessons_for_ddd(lessons, "run_test", "SwarmAI")
        assert all(p.change_type == "append" for p in proposals)

    # ── AC5: retire proposal is NEVER safe-append + apply_to_ddd refuses it ─────
    def test_retire_proposal_never_safe_append(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="x" * 40, source_run_id="r", confidence=0.9,
            change_type="retire", target_title="Some Entry",
        )
        # Even in an otherwise safe-append section, retire is never auto-applicable.
        assert p.is_safe_append() is False

    def test_apply_to_ddd_hard_refuses_retire_independent_of_is_safe_append(self):
        """HIGH-3 defense-in-depth: apply_to_ddd's change_type guard refuses a
        non-append EVEN IF is_safe_append were (wrongly) True. This ISOLATES the
        belt from the suspenders — we force is_safe_append→True so ONLY the HIGH-3
        guard can produce 'not_safe'. Mutation-proven: removing the guard line at
        apply_to_ddd makes this go RED (Gate-2 caught the earlier vacuous version
        that also passed via is_safe_append)."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="x" * 40, source_run_id="r", confidence=0.9,
            change_type="retire", target_title="X",
        )
        # Force the suspenders open — is_safe_append lies True. The ONLY thing
        # standing between a retire and an append-to-doc is now the HIGH-3 guard.
        object.__setattr__(p, "is_safe_append", lambda: True)
        assert apply_to_ddd(p, Path("/nonexistent")) == "not_safe"

    def test_retire_never_safe_append_via_real_gate(self, tmp_path):
        """The is_safe_append guard (suspenders) independently refuses retire."""
        from core.ddd_cultivation import CultivationProposal, apply_to_ddd
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("## What Worked\n\n- **X** — y\n", encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="x" * 40, source_run_id="r", confidence=0.9,
            change_type="retire", target_title="X",
        )
        assert apply_to_ddd(p, tmp_path) == "not_safe"

    def test_cultivate_retire_always_escalates_never_applies(self, tmp_path):
        """AC5 end-to-end: a retire proposal returned from the cultivate path is
        ESCALATED (written to queue), never auto-applied to the doc."""
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal
        doc = tmp_path / "IMPROVEMENT.md"
        original = "## What Worked\n\n- [guideline] **Target entry title here** — body text\n"
        doc.write_text(original, encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="Target entry title here is no longer true — was wrong",
            source_run_id="r", confidence=0.9,
            change_type="retire", target_title="Target entry title here",
            evidence="ev",
        )
        result = _cultivate_proposals([p], tmp_path)
        assert result["escalated"] == 1
        assert result["applied"] == 0
        # Doc unchanged — retire did NOT auto-apply.
        assert doc.read_text(encoding="utf-8") == original
        # Proposal written to the escalation queue.
        proposals_dir = tmp_path / ".artifacts" / "proposals"
        assert proposals_dir.exists()
        assert list(proposals_dir.glob("*.json"))

    # ── AC3: apply_retire_proposal archives + strips + .bak (REAL retire_entry) ─
    def test_apply_retire_proposal_archives_and_strips(self, tmp_path):
        from core.ddd_cultivation import apply_retire_proposal, CultivationProposal
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text(
            "## What Failed\n\n"
            "- [pitfall] **Stale caliber residue in query templates** — the fbr_flag drift\n\n"
            "- [pitfall] **Keep this one** — unrelated surviving entry\n",
            encoding="utf-8",
        )
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Failed",
            content="superseded", source_run_id="r", confidence=0.9,
            change_type="retire",
            target_title="Stale caliber residue in query templates",
            evidence="proven false",
        )
        status = apply_retire_proposal(p, tmp_path)
        assert status == "retired", f"got {status}"
        after = doc.read_text(encoding="utf-8")
        # Entry stripped from source
        assert "Stale caliber residue" not in after
        # Sibling preserved (identity strip, not title-only)
        assert "Keep this one" in after
        # Archived to doc-matched archive (BLOCKER-1: not IMPROVEMENT default when TECH)
        archive = tmp_path / "IMPROVEMENT-archive.md"
        assert archive.exists()
        assert "Stale caliber residue" in archive.read_text(encoding="utf-8")
        # Dated .bak snapshot of pre-strip state
        baks = list(tmp_path.glob("IMPROVEMENT.md.*.bak"))
        assert baks, "expected a dated .bak snapshot"
        assert "Stale caliber residue" in baks[0].read_text(encoding="utf-8")

    def test_apply_retire_no_target_refuses(self, tmp_path):
        from core.ddd_cultivation import apply_retire_proposal, CultivationProposal
        doc = tmp_path / "IMPROVEMENT.md"
        doc.write_text("## What Failed\n\n- **X** — y\n", encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Failed",
            content="c", source_run_id="r", confidence=0.9,
            change_type="retire", target_title="",  # locator found nothing
        )
        assert apply_retire_proposal(p, tmp_path) == "no_target"

    def test_apply_retire_no_match_fails_loud(self, tmp_path):
        """retire_entry is fail-loud: a title with no match → retire_failed, never
        a silent zero-strip (data-loss guard)."""
        from core.ddd_cultivation import apply_retire_proposal, CultivationProposal
        doc = tmp_path / "IMPROVEMENT.md"
        original = "## What Failed\n\n- [pitfall] **Real entry** — body\n"
        doc.write_text(original, encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Failed",
            content="c", source_run_id="r", confidence=0.9,
            change_type="retire", target_title="Nonexistent title never present",
        )
        status = apply_retire_proposal(p, tmp_path)
        assert status.startswith("retire_failed:")
        # Doc untouched — nothing stripped on a failed match.
        assert doc.read_text(encoding="utf-8") == original

    def test_apply_retire_refuses_append_type(self, tmp_path):
        from core.ddd_cultivation import apply_retire_proposal, CultivationProposal
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="c", source_run_id="r", confidence=0.9,
            change_type="append",
        )
        assert apply_retire_proposal(p, tmp_path) == "not_retire"

    # ── AC6: backward-compatible serialization ─────────────────────────────────
    def test_from_dict_old_json_defaults_to_append(self):
        """OLD proposal JSON (no change_type key) → change_type='append'."""
        from core.ddd_cultivation import CultivationProposal
        old = {
            "id": "proposal_abc123", "target_doc": "IMPROVEMENT.md",
            "target_section": "What Worked", "content": "x" * 40,
            "source_run_id": "run_old", "confidence": 0.8,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        p = CultivationProposal.from_dict(old)
        assert p.change_type == "append"
        assert p.target_title == ""
        assert p.evidence == ""
        assert p.replacement_content == ""

    def test_new_fields_roundtrip(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(
            target_doc="TECH.md", target_section="Runtime Traps",
            content="x" * 40, source_run_id="r", confidence=0.7,
            change_type="retire", target_title="Some Title",
            evidence="verbatim quote", replacement_content="new text",
        )
        p2 = CultivationProposal.from_dict(p.to_dict())
        assert p2.change_type == "retire"
        assert p2.target_title == "Some Title"
        assert p2.evidence == "verbatim quote"
        assert p2.replacement_content == "new text"


class TestConfidentAutoRetire:
    """run_ecc7a32b — HIGH-CONFIDENCE retire AUTO-APPLIES (reversible); borderline
    / keep-class / over-cap ESCALATES. Drives the REAL _cultivate_proposals +
    apply_retire_proposal + retire_entry against real temp docs (no mocks)."""

    def _doc(self, tmp_path, body):
        d = tmp_path / "IMPROVEMENT.md"
        d.write_text(body, encoding="utf-8")
        return d

    # ── confident locate → auto_apply_ok True + actually deletes ───────────────
    def test_confident_retire_auto_applies(self, tmp_path):
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal
        doc = self._doc(
            tmp_path,
            "## What Worked\n\n"
            "- [guideline] **Vector recall hybrid scorer blend leg** — 0.6v 0.4k\n\n"
            "- [guideline] **Frontend reconcile store authority render** — layer six\n",
        )
        original = doc.read_text(encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="c", source_run_id="r", confidence=0.9,
            change_type="retire",
            target_title="Vector recall hybrid scorer blend leg",
            evidence="torn out", auto_apply_ok=True,
        )
        result = _cultivate_proposals([p], tmp_path)
        assert result["retired"] == 1, result
        assert result["escalated"] == 0
        after = doc.read_text(encoding="utf-8")
        assert "Vector recall hybrid scorer blend leg" not in after  # deleted
        assert "Frontend reconcile store authority render" in after  # sibling kept
        # Reversible: archived + .bak both present
        assert (tmp_path / "IMPROVEMENT-archive.md").exists()
        assert list(tmp_path.glob("IMPROVEMENT.md.*.bak"))
        assert original != after

    # ── borderline (auto_apply_ok False) → escalate, doc untouched ─────────────
    def test_unconfident_retire_escalates(self, tmp_path):
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal
        doc = self._doc(
            tmp_path,
            "## What Worked\n\n- [guideline] **Some entry title here** — body\n",
        )
        original = doc.read_text(encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="c", source_run_id="r", confidence=0.9,
            change_type="retire", target_title="Some entry title here",
            evidence="ev", auto_apply_ok=False,  # borderline
        )
        result = _cultivate_proposals([p], tmp_path)
        assert result["escalated"] == 1
        assert result["retired"] == 0
        assert doc.read_text(encoding="utf-8") == original  # untouched
        assert list((tmp_path / ".artifacts" / "proposals").glob("*.json"))

    # ── per-run cap: >MAX_AUTO_RETIRES_PER_RUN confident retires → rest escalate ─
    def test_auto_retire_capped_per_run(self, tmp_path):
        from core.ddd_cultivation import (
            _cultivate_proposals, CultivationProposal, MAX_AUTO_RETIRES_PER_RUN,
        )
        # Seed N+1 distinct entries, all confident retires.
        n = MAX_AUTO_RETIRES_PER_RUN + 1
        body = "## What Worked\n\n" + "".join(
            f"- [guideline] **Alpha bravo charlie topic number {i}** — body {i}\n\n"
            for i in range(n)
        )
        self._doc(tmp_path, body)
        props = [
            CultivationProposal(
                target_doc="IMPROVEMENT.md", target_section="What Worked",
                content=f"c{i}", source_run_id="r", confidence=0.9,
                change_type="retire",
                target_title=f"Alpha bravo charlie topic number {i}",
                evidence="ev", auto_apply_ok=True,
            )
            for i in range(n)
        ]
        result = _cultivate_proposals(props, tmp_path)
        assert result["retired"] == MAX_AUTO_RETIRES_PER_RUN
        assert result["escalated"] == n - MAX_AUTO_RETIRES_PER_RUN  # overflow queued

    # ── auto-retire that fails-loud (no match) → escalate, NOT silent drop ──────
    def test_auto_retire_failed_match_escalates(self, tmp_path):
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal
        doc = self._doc(
            tmp_path, "## What Worked\n\n- [guideline] **Real entry present** — body\n",
        )
        original = doc.read_text(encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="c", source_run_id="r", confidence=0.9,
            change_type="retire",
            target_title="Title that does not exist anywhere",  # will fail-loud
            evidence="ev", auto_apply_ok=True,
        )
        result = _cultivate_proposals([p], tmp_path)
        # Fail-loud retire_entry → not retired, escalated (never silently dropped)
        assert result["retired"] == 0
        assert result["escalated"] == 1
        assert doc.read_text(encoding="utf-8") == original  # nothing stripped

    # ── keep-class target is never auto-confident (locator sets confident False) ─
    def test_keep_class_target_not_confident(self, tmp_path):
        from core.ddd_cultivation import _locate_target_entry
        # A decision-type entry (keep-class) with strong overlap must NOT be
        # confident → escalate, not auto-delete.
        d = tmp_path / "MEMORY.md"
        d.write_text(
            "## Decisions\n\n"
            "- [decision] **Adopt vector recall hybrid scorer blend leg** — chosen 0.6v\n",
            encoding="utf-8",
        )
        located = _locate_target_entry(
            "The vector recall hybrid scorer blend leg decision is no longer valid — superseded",
            "MEMORY.md", d.parent if False else tmp_path,
        )
        assert located is not None
        _, _, confident = located
        assert confident is False  # keep-class → never auto (engine would refuse too)

    # ── full filter path: confident supersession lesson sets auto_apply_ok ──────
    def test_filter_sets_auto_apply_ok_on_confident(self, tmp_path):
        from core.ddd_cultivation import filter_lessons_for_ddd, _classify_lesson
        self._doc(
            tmp_path,
            "## What Worked\n\n"
            "- [guideline] **Adversarial gate scorer mutation approach** — caught races\n\n"
            "- [guideline] **Totally different frontend render topic** — unrelated\n",
        )
        lesson = ("The adversarial gate scorer mutation approach is no longer used — "
                  "was wrong, superseded")
        assert _classify_lesson(lesson, project="SwarmAI") is not None
        props = filter_lessons_for_ddd([lesson], "run_t", "SwarmAI", tmp_path)
        assert len(props) == 1
        assert props[0].change_type == "retire"
        assert props[0].auto_apply_ok is True

    # ── Gate-2 #1: distinguishing-token requirement (denylist-independent) ──────
    def test_shared_structural_tokens_not_confident(self, tmp_path):
        """A title made only of tokens SHARED across many entries (structural
        vocabulary) must NOT be auto-confident on a coincidental phrase — even if
        none are in the _GENERIC denylist. Requires a doc-frequency-1 token."""
        from core.ddd_cultivation import _locate_target_entry
        self._doc(
            tmp_path,
            "## What Worked\n\n"
            "- [guideline] **Stage layer module logic** — a\n\n"
            "- [guideline] **Stage layer module design** — b\n\n"
            "- [guideline] **Stage layer module render** — c\n",
        )
        # Lesson overlaps only the SHARED tokens (stage/layer/module) + a word not
        # in any title → no distinguishing (doc_freq==1) token → NOT confident.
        located = _locate_target_entry(
            "The stage layer module thing is no longer used — superseded",
            "IMPROVEMENT.md", tmp_path,
        )
        if located is not None:
            assert located[2] is False  # confident must be False (no unique token)

    def test_distinguishing_token_enables_confident(self, tmp_path):
        """The SAME structural doc, but the lesson names the UNIQUE token
        ('logic', doc_freq==1) → distinguishing → confident=True (margin also
        holds: 'logic' pushes best above the shared-only runners-up)."""
        from core.ddd_cultivation import _locate_target_entry
        self._doc(
            tmp_path,
            "## What Worked\n\n"
            "- [guideline] **Alpha bravo charlie logic** — a\n\n"
            "- [guideline] **Delta echo foxtrot render** — b\n",
        )
        located = _locate_target_entry(
            "The alpha bravo charlie logic is no longer used — was wrong, superseded",
            "IMPROVEMENT.md", tmp_path,
        )
        assert located is not None
        assert located[2] is True  # unique tokens present + clear margin

    # ── Gate-2 #2: keep-class via EVERGREEN SECTION (non-keep type) ─────────────
    def test_evergreen_section_guideline_not_confident(self, tmp_path):
        """A guideline-TYPE entry (not a keep-type) sitting in an EVERGREEN
        SECTION must be confident=False — is_keep_class now receives
        MEMORY_EVERGREEN_SECTIONS so section-rule-1 fires (matches retire_entry)."""
        from core.ddd_cultivation import _locate_target_entry
        d = tmp_path / "MEMORY.md"
        d.write_text(
            "## Open Threads\n\n"
            "- [guideline] **Alpha bravo charlie delta unique topic** — body\n",
            encoding="utf-8",
        )
        located = _locate_target_entry(
            "The alpha bravo charlie delta unique topic is no longer used — was wrong, superseded",
            "MEMORY.md", tmp_path,
        )
        assert located is not None
        assert located[2] is False  # evergreen-section → keep-class → escalate

    # ── Gate-2 #3: session/day-wide cap across entrypoints ──────────────────────
    def test_auto_retire_day_cap_across_calls(self, tmp_path):
        """MAX_AUTO_RETIRES_PER_DAY bounds the TOTAL autonomous retires per
        project/day across separate _cultivate_proposals calls (reflect +
        corrections + decisions). Without it, 3 calls × per-call-cap would delete
        more than the advertised ceiling."""
        import core.ddd_cultivation as m
        from core.ddd_cultivation import (
            _cultivate_proposals, CultivationProposal, MAX_AUTO_RETIRES_PER_DAY,
        )
        m._auto_retire_ledger.clear()
        body = "## What Worked\n\n" + "".join(
            f"- [guideline] **Unique alpha bravo topic number{i}** — b{i}\n\n"
            for i in range(9)
        )
        self._doc(tmp_path, body)

        def mk(i):
            return CultivationProposal(
                target_doc="IMPROVEMENT.md", target_section="What Worked",
                content=f"c{i}", source_run_id="r", confidence=0.9,
                change_type="retire",
                target_title=f"Unique alpha bravo topic number{i}",
                evidence="ev", auto_apply_ok=True,
            )

        total = 0
        for call in range(3):  # 3 separate entrypoint calls in one "session"
            r = _cultivate_proposals([mk(call * 3), mk(call * 3 + 1), mk(call * 3 + 2)], tmp_path)
            total += r["retired"]
        assert total == MAX_AUTO_RETIRES_PER_DAY  # day cap, NOT 3×per-call-cap
        m._auto_retire_ledger.clear()  # don't leak ledger into other tests

    # ── Gate-2 #5: rewrite NEVER auto-applies (only retire does) ────────────────
    def test_rewrite_never_auto_applies(self, tmp_path):
        """A confident rewrite proposal must ESCALATE, never auto-apply — the
        rewrite branch has a delete-then-failed-append partial-state trap, so
        autonomous rewrite is disallowed (a human approves it)."""
        import core.ddd_cultivation as m
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal
        m._auto_retire_ledger.clear()
        doc = self._doc(
            tmp_path,
            "## What Worked\n\n- [guideline] **Alpha bravo charlie unique topic** — body\n",
        )
        original = doc.read_text(encoding="utf-8")
        p = CultivationProposal(
            target_doc="IMPROVEMENT.md", target_section="What Worked",
            content="c", source_run_id="r", confidence=0.9,
            change_type="rewrite", target_title="Alpha bravo charlie unique topic",
            evidence="ev", replacement_content="new text", auto_apply_ok=True,
        )
        result = _cultivate_proposals([p], tmp_path)
        assert result["retired"] == 0
        assert result["escalated"] == 1
        assert doc.read_text(encoding="utf-8") == original  # nothing deleted
        m._auto_retire_ledger.clear()
