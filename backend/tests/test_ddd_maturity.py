"""Tests for DDD Maturity Annotations (T4).

Tests: parser, writer, promotion logic, evidence accumulation, roundtrip.
Methodology: TDD vertical tracer bullets — one AC per test group.
"""

import json
import pytest
from datetime import datetime, timezone
from core.ddd_maturity import (
    MaturityState,
    parse_maturity,
    inject_maturity,
    evaluate_promotion,
)


class TestIllegalLevelFailLoud:
    """Fail-loud on an illegal maturity level (e.g. hand-written 'seeded').

    Behavior is PRESERVED (a safe default is still returned / coerced to sparse),
    but the illegal level must now emit a logger.warning naming it — so doc
    pollution is visible instead of silently discarding the section's evidence.
    """

    def test_parse_illegal_level_returns_safe_default(self):
        # AC1: behavior preserved — illegal level parses to a safe default.
        content = (
            "## Bad Section\n"
            "<!-- maturity: seeded | sources: 1 | verified: true | "
            "used: true | days: 0 | promoted: none -->\n"
            "Body.\n"
        )
        result = parse_maturity(content)
        state = result["Bad Section"]
        assert state.level == "sparse"  # coerced to safe default
        assert state.source_count == 0  # zeroed default (evidence discarded — unchanged)

    def test_parse_illegal_level_emits_warning_naming_it(self, caplog):
        # AC2: fail-loud — the warning names the illegal level.
        content = (
            "## Bad Section\n"
            "<!-- maturity: seeded | sources: 1 | verified: true | "
            "used: true | days: 0 | promoted: none -->\n"
            "Body.\n"
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="core.ddd_maturity"):
            parse_maturity(content)
        assert any(
            "seeded" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        ), f"expected a WARNING naming 'seeded', got: {[r.message for r in caplog.records]}"

    def test_construct_illegal_level_emits_warning(self, caplog):
        # AC2 (defense-in-depth): direct construction with an illegal level warns.
        import logging
        with caplog.at_level(logging.WARNING, logger="core.ddd_maturity"):
            state = MaturityState(level="bogus")
        assert state.level == "sparse"  # coerced — behavior preserved
        assert any("bogus" in r.message for r in caplog.records)

    def test_legal_default_construction_emits_no_warning(self, caplog):
        # AC3: no warn-storm — the legal 'sparse' default must NOT warn.
        import logging
        with caplog.at_level(logging.WARNING, logger="core.ddd_maturity"):
            MaturityState()  # default level='sparse' (legal)
            MaturityState(level="growing")
            # an un-annotated section → parse builds MaturityState() defaults
            parse_maturity("## Plain\n\nJust body, no annotation.\n")
        assert not caplog.records, (
            f"legal levels must not warn, got: {[r.message for r in caplog.records]}"
        )


# --- AC1+AC2+AC3: Parse/Inject roundtrip ---


class TestParseMaturiy:
    """AC3: HTML comment format parsing."""

    def test_parse_basic_annotation(self):
        content = (
            "# Doc Title\n\n"
            "## Architecture\n"
            "<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 45 | promoted: 2026-05-01 -->\n\n"
            "Some content here.\n"
        )
        result = parse_maturity(content)
        assert "Architecture" in result
        state = result["Architecture"]
        assert state.level == "growing"
        assert state.source_count == 3
        assert state.verified_by_production is True
        assert state.used_in_decision is True
        assert state.days_at_level == 45
        assert state.last_promoted == datetime(2026, 5, 1, tzinfo=timezone.utc)

    def test_parse_sparse_defaults(self):
        content = (
            "## Overview\n"
            "<!-- maturity: sparse | sources: 0 | verified: false | used: false | days: 0 | promoted: none -->\n\n"
            "Content.\n"
        )
        result = parse_maturity(content)
        assert result["Overview"].level == "sparse"
        assert result["Overview"].source_count == 0
        assert result["Overview"].verified_by_production is False
        assert result["Overview"].last_promoted is None

    def test_parse_no_annotation_returns_sparse(self):
        """AC8: Sections without annotations default to Sparse."""
        content = "## Architecture\n\nSome content.\n\n## Stack\n\nMore content.\n"
        result = parse_maturity(content)
        assert "Architecture" in result
        assert result["Architecture"].level == "sparse"
        assert result["Architecture"].source_count == 0
        assert "Stack" in result
        assert result["Stack"].level == "sparse"

    def test_parse_empty_content(self):
        result = parse_maturity("")
        assert result == {}

    def test_parse_no_sections(self):
        result = parse_maturity("Just a paragraph.\n\nNo headers.\n")
        assert result == {}

    def test_parse_malformed_annotation_treated_as_sparse(self):
        """Edge case: malformed HTML comment → Sparse with zeroed evidence."""
        content = "## Broken\n<!-- maturity: garbled nonsense -->\n\nContent.\n"
        result = parse_maturity(content)
        assert result["Broken"].level == "sparse"
        assert result["Broken"].source_count == 0

    def test_parse_multiple_sections_mixed(self):
        content = (
            "## Architecture\n"
            "<!-- maturity: mature | sources: 5 | verified: true | used: true | days: 60 | promoted: 2026-04-01 -->\n\n"
            "Arch content.\n\n"
            "## Stack\n"
            "Stack content without annotation.\n\n"
            "## Conventions\n"
            "<!-- maturity: growing | sources: 2 | verified: true | used: false | days: 15 | promoted: 2026-05-10 -->\n\n"
            "Conv content.\n"
        )
        result = parse_maturity(content)
        assert result["Architecture"].level == "mature"
        assert result["Stack"].level == "sparse"
        assert result["Conventions"].level == "growing"


class TestInjectMaturity:
    """AC1: inject_maturity writes annotations correctly."""

    def test_inject_new_annotation(self):
        content = "## Architecture\n\nSome content.\n"
        states = {
            "Architecture": MaturityState(
                level="growing", source_count=2,
                verified_by_production=True, used_in_decision=False,
                days_at_level=10, last_promoted=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
        }
        result = inject_maturity(content, states)
        assert "<!-- maturity: growing | sources: 2 | verified: true | used: false | days: 10 | trust: high | promoted: 2026-05-01 -->" in result
        # Content preserved
        assert "Some content." in result

    def test_inject_replaces_existing_annotation(self):
        content = (
            "## Architecture\n"
            "<!-- maturity: sparse | sources: 0 | verified: false | used: false | days: 0 | promoted: none -->\n\n"
            "Content.\n"
        )
        states = {
            "Architecture": MaturityState(
                level="growing", source_count=3,
                verified_by_production=True, used_in_decision=True,
                days_at_level=30, last_promoted=datetime(2026, 5, 15, tzinfo=timezone.utc),
            )
        }
        result = inject_maturity(content, states)
        assert "maturity: growing" in result
        assert "sources: 3" in result
        # Old annotation gone
        assert "maturity: sparse" not in result

    def test_inject_roundtrip(self):
        """Roundtrip: inject(states_from_parse(content)) preserves semantics."""
        original = (
            "## Architecture\n"
            "<!-- maturity: mature | sources: 5 | verified: true | used: true | days: 60 | promoted: 2026-04-01 -->\n\n"
            "Arch content.\n\n"
            "## Stack\n\n"
            "Stack content.\n"
        )
        states = parse_maturity(original)
        result = inject_maturity(original, states)
        # Re-parse should give same states
        reparsed = parse_maturity(result)
        assert reparsed["Architecture"].level == "mature"
        assert reparsed["Architecture"].source_count == 5
        assert reparsed["Stack"].level == "sparse"

    def test_inject_only_listed_sections(self):
        """Sections not in states dict are untouched."""
        content = "## A\n\nContent A.\n\n## B\n\nContent B.\n"
        states = {"A": MaturityState(level="growing", source_count=2)}
        result = inject_maturity(content, states)
        assert "maturity: growing" in result
        # B has no annotation added
        lines = result.split("\n")
        b_idx = next(i for i, l in enumerate(lines) if l == "## B")
        # Next line after ## B should NOT be a maturity comment
        assert "maturity:" not in lines[b_idx + 1]


class TestEvaluatePromotion:
    """AC4: Promotion rules match design doc."""

    def test_sparse_to_growing(self):
        """sparse→growing requires source_count>=2 AND verified_by_production."""
        state = MaturityState(
            level="sparse", source_count=2,
            verified_by_production=True, used_in_decision=False,
            days_at_level=7,
        )
        assert evaluate_promotion(state) == "growing"

    def test_sparse_not_promoted_insufficient_sources(self):
        state = MaturityState(
            level="sparse", source_count=1,
            verified_by_production=True, used_in_decision=False,
            days_at_level=30,
        )
        assert evaluate_promotion(state) is None

    def test_sparse_not_promoted_unverified(self):
        state = MaturityState(
            level="sparse", source_count=5,
            verified_by_production=False, used_in_decision=True,
            days_at_level=30,
        )
        assert evaluate_promotion(state) is None

    def test_growing_to_mature(self):
        """growing→mature requires source_count>=3 AND days_at_level>30 AND used_in_decision."""
        state = MaturityState(
            level="growing", source_count=4,
            verified_by_production=True, used_in_decision=True,
            days_at_level=31,
        )
        assert evaluate_promotion(state) == "mature"

    def test_growing_not_promoted_too_young(self):
        state = MaturityState(
            level="growing", source_count=5,
            verified_by_production=True, used_in_decision=True,
            days_at_level=29,  # needs >30
        )
        assert evaluate_promotion(state) is None

    def test_growing_not_promoted_unused(self):
        state = MaturityState(
            level="growing", source_count=5,
            verified_by_production=True, used_in_decision=False,
            days_at_level=60,
        )
        assert evaluate_promotion(state) is None

    def test_mature_never_auto_promoted(self):
        """AC4: mature→evergreen is MANUAL ONLY."""
        state = MaturityState(
            level="mature", source_count=10,
            verified_by_production=True, used_in_decision=True,
            days_at_level=100,
        )
        assert evaluate_promotion(state) is None

    def test_evergreen_never_promoted(self):
        state = MaturityState(level="evergreen", source_count=20, days_at_level=365)
        assert evaluate_promotion(state) is None


# --- AC5: Evidence accumulation from changelog ---


class TestEvidenceAccumulation:
    """AC5: source_count from distinct source_stages in changelog."""

    def test_compute_evidence_from_changelog(self, tmp_path):
        from core.ddd_maturity import compute_evidence_from_changelog

        # Create project with changelog
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir()
        changelog = artifacts / "ddd-changelog.jsonl"
        entries = [
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "reflect", "timestamp": "2026-05-01T10:00:00+00:00"},
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "correction", "timestamp": "2026-05-02T10:00:00+00:00"},
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "reflect", "timestamp": "2026-05-03T10:00:00+00:00"},  # duplicate source
            {"target_doc": "TECH.md", "target_section": "Stack", "source_stage": "decision", "timestamp": "2026-05-01T10:00:00+00:00"},
            {"target_doc": "IMPROVEMENT.md", "target_section": "What Worked", "source_stage": "reflect", "timestamp": "2026-05-01T10:00:00+00:00"},
        ]
        changelog.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        result = compute_evidence_from_changelog(tmp_path)

        # Architecture: 2 distinct sources (reflect + correction)
        assert result[("TECH.md", "Architecture")]["source_count"] == 2
        # Stack: 1 distinct source (decision)
        assert result[("TECH.md", "Stack")]["source_count"] == 1
        # What Worked: 1 distinct source (reflect)
        assert result[("IMPROVEMENT.md", "What Worked")]["source_count"] == 1

    def test_compute_evidence_no_changelog(self, tmp_path):
        from core.ddd_maturity import compute_evidence_from_changelog
        result = compute_evidence_from_changelog(tmp_path)
        assert result == {}

    def test_update_evidence_writes_to_doc(self, tmp_path):
        from core.ddd_maturity import update_evidence_from_changelog

        # Create TECH.md with sparse annotation
        tech = tmp_path / "TECH.md"
        tech.write_text(
            "## Architecture\n"
            "<!-- maturity: sparse | sources: 0 | verified: false | used: false | days: 5 | promoted: none -->\n\n"
            "Content.\n",
            encoding="utf-8",
        )

        # Create changelog with 3 distinct sources
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir()
        changelog = artifacts / "ddd-changelog.jsonl"
        entries = [
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "reflect"},
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "correction"},
            {"target_doc": "TECH.md", "target_section": "Architecture", "source_stage": "decision"},
        ]
        changelog.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        result = update_evidence_from_changelog(tmp_path)
        assert result["updated"] == 1

        # Verify the file was updated
        updated_content = tech.read_text(encoding="utf-8")
        states = parse_maturity(updated_content)
        assert states["Architecture"].source_count == 3

    def test_days_at_level_refreshed_from_last_promoted(self, tmp_path):
        """Fix #2: days_at_level must be computed from last_promoted, not stored stale."""
        from core.ddd_maturity import update_evidence_from_changelog
        from datetime import timedelta

        # Section promoted 35 days ago — stored days=0 (stale)
        promoted_date = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%d")
        tech = tmp_path / "TECH.md"
        tech.write_text(
            f"## Architecture\n"
            f"<!-- maturity: growing | sources: 4 | verified: true | used: true | days: 0 | promoted: {promoted_date} -->\n\n"
            f"Content.\n",
            encoding="utf-8",
        )
        # Need artifacts dir (even if empty changelog)
        (tmp_path / ".artifacts").mkdir()

        update_evidence_from_changelog(tmp_path)

        # days_at_level should now be ~35
        updated = tech.read_text(encoding="utf-8")
        states = parse_maturity(updated)
        assert states["Architecture"].days_at_level >= 34  # allow 1 day tolerance

    def test_maturity_promotion_excluded_from_source_count(self, tmp_path):
        """Fix #3: maturity_promotion entries don't inflate source_count."""
        from core.ddd_maturity import compute_evidence_from_changelog

        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir()
        changelog = artifacts / "ddd-changelog.jsonl"
        entries = [
            {"target_doc": "TECH.md", "target_section": "Arch", "source_stage": "reflect"},
            {"target_doc": "TECH.md", "target_section": "Arch", "source_stage": "maturity_promotion"},
        ]
        changelog.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")

        result = compute_evidence_from_changelog(tmp_path)
        # Only 'reflect' counts — maturity_promotion excluded
        assert result[("TECH.md", "Arch")]["source_count"] == 1


# --- AC6: evaluate_all_promotions + promote_section ---


class TestPromotionExecution:
    """AC6: Integration — promotion evaluation + file writes."""

    def test_evaluate_all_promotions_finds_eligible(self, tmp_path):
        from core.ddd_maturity import evaluate_all_promotions

        tech = tmp_path / "TECH.md"
        tech.write_text(
            "## Architecture\n"
            "<!-- maturity: sparse | sources: 3 | verified: true | used: true | days: 10 | promoted: none -->\n\n"
            "Content.\n\n"
            "## Stack\n"
            "<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 5 | promoted: none -->\n\n"
            "Stack.\n",
            encoding="utf-8",
        )

        promotions = evaluate_all_promotions(tmp_path)
        assert len(promotions) == 1
        assert promotions[0]["doc"] == "TECH.md"
        assert promotions[0]["section"] == "Architecture"
        assert promotions[0]["from_level"] == "sparse"
        assert promotions[0]["to_level"] == "growing"

    def test_promote_section_writes_file(self, tmp_path):
        from core.ddd_maturity import promote_section

        tech = tmp_path / "TECH.md"
        tech.write_text(
            "## Architecture\n"
            "<!-- maturity: sparse | sources: 2 | verified: true | used: false | days: 10 | promoted: none -->\n\n"
            "Content.\n",
            encoding="utf-8",
        )

        result = promote_section(tmp_path, "TECH.md", "Architecture", "growing")
        assert result is True

        # Verify file updated
        updated = tech.read_text(encoding="utf-8")
        states = parse_maturity(updated)
        assert states["Architecture"].level == "growing"
        assert states["Architecture"].days_at_level == 0  # Reset on promotion

    def test_promote_section_nonexistent_doc(self, tmp_path):
        from core.ddd_maturity import promote_section
        result = promote_section(tmp_path, "MISSING.md", "Foo", "growing")
        assert result is False

    def test_promote_section_nonexistent_section(self, tmp_path):
        from core.ddd_maturity import promote_section
        tech = tmp_path / "TECH.md"
        tech.write_text("## Real\n\nContent.\n", encoding="utf-8")
        result = promote_section(tmp_path, "TECH.md", "Fake", "growing")
        assert result is False


# --- AC7: Pipeline integration (verified_by_production, used_in_decision) ---


class TestPipelineIntegration:
    """AC7: Pipeline stages set verified/used flags via evidence update."""

    def test_verified_by_production_flag(self, tmp_path):
        """Simulating pipeline deliver success: sections loaded get verified=true."""
        from core.ddd_maturity import parse_maturity, inject_maturity, MaturityState

        # Setup: TECH.md with unverified section
        tech = tmp_path / "TECH.md"
        tech.write_text(
            "## Architecture\n"
            "<!-- maturity: sparse | sources: 2 | verified: false | used: false | days: 10 | promoted: none -->\n\n"
            "Content.\n",
            encoding="utf-8",
        )

        # Simulate: pipeline deliver stage marks verified
        states = parse_maturity(tech.read_text(encoding="utf-8"))
        states["Architecture"].verified_by_production = True
        new_content = inject_maturity(tech.read_text(encoding="utf-8"), states)
        tech.write_text(new_content, encoding="utf-8")

        # Verify: now passes promotion check (sources>=2 + verified)
        updated_states = parse_maturity(tech.read_text(encoding="utf-8"))
        assert updated_states["Architecture"].verified_by_production is True
        from core.ddd_maturity import evaluate_promotion
        assert evaluate_promotion(updated_states["Architecture"]) == "growing"

    def test_used_in_decision_flag(self, tmp_path):
        """Simulating pipeline evaluate/think: sections loaded get used=true."""
        from core.ddd_maturity import parse_maturity, inject_maturity, evaluate_promotion

        tech = tmp_path / "TECH.md"
        tech.write_text(
            "## Architecture\n"
            "<!-- maturity: growing | sources: 4 | verified: true | used: false | days: 35 | promoted: 2026-04-01 -->\n\n"
            "Content.\n",
            encoding="utf-8",
        )

        # Simulate: pipeline evaluate stage marks used
        states = parse_maturity(tech.read_text(encoding="utf-8"))
        states["Architecture"].used_in_decision = True
        new_content = inject_maturity(tech.read_text(encoding="utf-8"), states)
        tech.write_text(new_content, encoding="utf-8")

        # Verify: now passes growing→mature (sources>=3, days>30, used=true)
        updated_states = parse_maturity(tech.read_text(encoding="utf-8"))
        assert evaluate_promotion(updated_states["Architecture"]) == "mature"


# --- AC8: Default behavior ---


class TestDefaultBehavior:
    """AC8: All new sections default to Sparse."""

    def test_new_section_no_annotation_is_sparse(self):
        content = "## BrandNew\n\nJust created.\n"
        result = parse_maturity(content)
        assert result["BrandNew"].level == "sparse"
        assert result["BrandNew"].source_count == 0
        assert result["BrandNew"].verified_by_production is False
        assert result["BrandNew"].used_in_decision is False
        assert result["BrandNew"].days_at_level == 0
        assert result["BrandNew"].last_promoted is None
