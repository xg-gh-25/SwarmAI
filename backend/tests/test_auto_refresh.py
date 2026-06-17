"""Tests for DDD & Memory Auto-Refresh Engine (Layer 1 mechanical + shared utilities).

Tests:
- MechanicalRefresher: stage count, specialist count, RP count detection+fix
- ValueGate: filters out archived/inactive content
- CitationVerifier: verifies file:line citations
- MemoryEntryRefresher: detects stale constants in MEMORY.md
- RefreshResult + log: serialization round-trip
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.auto_refresh import (
    CitationVerifier,
    MechanicalRefresher,
    MemoryEntryRefresher,
    RefreshResult,
    ValueGate,
    classify_confidence,
    is_strategy_section,
    log_refresh_results,
    read_refresh_log,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal workspace for testing."""
    # .context/MEMORY.md with a stale reference
    context_dir = tmp_path / ".context"
    context_dir.mkdir()
    memory = context_dir / "MEMORY.md"
    memory.write_text(
        "## Key Decisions\n"
        "- KD40: SwarmAI = 8-stage pipeline with adversarial review\n"
        "- KD06: task_budget=800K for desktop\n"
    )

    # Projects/SwarmAI/TECH.md with stale count
    project_dir = tmp_path / "Projects" / "SwarmAI"
    project_dir.mkdir(parents=True)
    tech_md = project_dir / "TECH.md"
    tech_md.write_text(
        "## Pipeline\n"
        "8-stage autonomous pipeline with 7 specialists.\n"
        "Review patterns: RP1-RP29.\n"
    )

    return tmp_path


@pytest.fixture
def swarmai_root(tmp_path):
    """Create a minimal swarmai repo structure."""
    root = tmp_path / "swarmai"
    root.mkdir()

    # Pipeline stages (9 files)
    stages_dir = root / "backend" / "skills" / "s_autonomous-pipeline" / "stages"
    stages_dir.mkdir(parents=True)
    for name in ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect", "goal_cycle"]:
        (stages_dir / f"{name}.md").write_text(f"# {name}")

    # Specialists (9 files)
    specialists_dir = stages_dir / "specialists"
    specialists_dir.mkdir()
    for name in ["correctness", "security", "performance", "api-contract",
                 "integration", "operational", "red-team", "state-machine", "concurrency"]:
        (specialists_dir / f"{name}.md").write_text(f"# {name}")

    # Review patterns
    rp_file = root / "backend" / "skills" / "s_autonomous-pipeline" / "REVIEW_PATTERNS.md"
    rp_content = "\n".join(f"| RP{i:02d} | pattern {i} |" for i in range(1, 38))
    rp_file.write_text(f"# Review Patterns\n{rp_content}\n")

    # Skills directory
    skills_dir = root / "backend" / "skills"
    for i in range(85):
        (skills_dir / f"s_skill-{i}").mkdir(parents=True, exist_ok=True)

    # Source file for constant checking
    session_unit = root / "backend" / "core" / "session_unit.py"
    session_unit.parent.mkdir(parents=True, exist_ok=True)
    session_unit.write_text("max_turns = 400\n")

    return root


# ── ValueGate Tests ───────────────────────────────────────────────────────


class TestValueGate:
    def test_context_files_are_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing(".context/MEMORY.md", workspace) is True
        assert ValueGate.is_worth_refreshing(".context/KNOWLEDGE.md", workspace) is True

    def test_project_ddd_docs_are_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing("Projects/SwarmAI/TECH.md", workspace) is True
        assert ValueGate.is_worth_refreshing("Projects/AIDLC/PRODUCT.md", workspace) is True

    def test_archives_are_not_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing("Knowledge/Archives/old.md", workspace) is False

    def test_old_reports_are_not_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing("Knowledge/Reports/2026-04-01-report.md", workspace) is False

    def test_artifact_runs_are_not_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing("Projects/SwarmAI/.artifacts/runs/run_abc/REPORT.md", workspace) is False

    def test_random_files_are_not_worth_refreshing(self, workspace):
        assert ValueGate.is_worth_refreshing("some/random/file.md", workspace) is False


# ── MechanicalRefresher Tests ─────────────────────────────────────────────


class TestMechanicalRefresher:
    def test_detects_stale_stage_count(self, workspace, swarmai_root):
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher._check_stage_count()

        # Should find "8-stage" in TECH.md and MEMORY.md
        stale = [r for r in results if r.old_value == "8-stage"]
        assert len(stale) >= 1
        assert stale[0].new_value == "9-stage"
        assert stale[0].confidence == 1.0

    def test_detects_stale_specialist_count(self, workspace, swarmai_root):
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher._check_specialist_count()

        stale = [r for r in results if "7" in r.old_value]
        assert len(stale) >= 1
        assert "9" in stale[0].new_value

    def test_detects_stale_rp_count(self, workspace, swarmai_root):
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher._check_rp_count()

        stale = [r for r in results if "RP1-RP29" in r.old_value]
        assert len(stale) >= 1
        assert stale[0].new_value == "RP1-RP37"

    def test_apply_fixes_modifies_file(self, workspace, swarmai_root):
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher.detect_and_fix()

        assert len(results) > 0
        applied = refresher.apply_fixes(results)
        assert applied > 0

        # Verify file was actually modified
        tech_md = workspace / "Projects" / "SwarmAI" / "TECH.md"
        content = tech_md.read_text()
        assert "9-stage" in content
        assert "8-stage" not in content

    def test_no_false_positives_when_current(self, swarmai_root, tmp_path):
        """If values are already correct, no results."""
        ws = tmp_path / "ws"
        context = ws / ".context"
        context.mkdir(parents=True)
        (context / "MEMORY.md").write_text("9-stage pipeline with 9 specialists and RP1-RP37")

        projects = ws / "Projects" / "SwarmAI"
        projects.mkdir(parents=True)
        (projects / "TECH.md").write_text("9-stage pipeline")

        refresher = MechanicalRefresher(swarmai_root, ws)
        results = refresher.detect_and_fix()
        stale = [r for r in results if r.old_value != r.new_value]
        # Should find nothing stale (specialist pattern may match differently)
        # but stage count should definitely not be flagged
        stage_stale = [r for r in stale if "stage" in r.old_value]
        assert len(stage_stale) == 0


# ── MemoryEntryRefresher Tests ────────────────────────────────────────────


class TestMemoryEntryRefresher:
    def test_detects_stale_stage_count_in_memory(self, workspace, swarmai_root):
        refresher = MemoryEntryRefresher(swarmai_root, workspace)
        results = refresher.scan_memory()

        stale = [r for r in results if "stage" in r.old_value]
        assert len(stale) >= 1
        assert "9-stage" in stale[0].new_value


# ── CitationVerifier Tests ────────────────────────────────────────────────


class TestCitationVerifier:
    def test_verifies_existing_file(self, swarmai_root, workspace):
        verifier = CitationVerifier(swarmai_root, workspace)

        # This file exists in our test swarmai_root
        assert verifier.verify_citation("backend/core/session_unit.py:1") is True

    def test_rejects_nonexistent_file(self, swarmai_root, workspace):
        verifier = CitationVerifier(swarmai_root, workspace)
        assert verifier.verify_citation("nonexistent/file.py:1") is False

    def test_verifies_content_pattern(self, swarmai_root, workspace):
        verifier = CitationVerifier(swarmai_root, workspace)
        assert verifier.verify_citation("backend/core/session_unit.py:max_turns") is True
        assert verifier.verify_citation("backend/core/session_unit.py:nonexistent_var") is False

    def test_verify_all_returns_counts(self, swarmai_root, workspace):
        verifier = CitationVerifier(swarmai_root, workspace)
        verified, total = verifier.verify_all_citations([
            "backend/core/session_unit.py:1",
            "nonexistent.py:1",
        ])
        assert verified == 1
        assert total == 2


# ── Confidence Classifier Tests ───────────────────────────────────────────


class TestConfidenceClassifier:
    def test_high_confidence_all_verified(self):
        score = classify_confidence(
            citations_verified=5, citations_total=5,
            changes_are_factual=True, touches_strategy_section=False,
        )
        assert score >= 0.8  # HIGH

    def test_medium_confidence_partial_citations(self):
        score = classify_confidence(
            citations_verified=3, citations_total=5,
            changes_are_factual=True, touches_strategy_section=False,
        )
        assert 0.5 <= score < 0.8  # MEDIUM

    def test_low_confidence_strategy_section(self):
        score = classify_confidence(
            citations_verified=5, citations_total=5,
            changes_are_factual=False, touches_strategy_section=True,
        )
        # Even with good citations, strategy + non-factual = lower confidence
        assert score < 0.8

    def test_zero_citations_gives_low(self):
        score = classify_confidence(
            citations_verified=0, citations_total=0,
            changes_are_factual=True, touches_strategy_section=False,
        )
        assert score < 0.5  # LOW


class TestStrategySection:
    def test_strategy_sections_detected(self):
        assert is_strategy_section("Vision") is True
        assert is_strategy_section("Non-Goals") is True
        assert is_strategy_section("Strategic Priorities") is True

    def test_non_strategy_sections_pass(self):
        assert is_strategy_section("Architecture") is False
        assert is_strategy_section("Runtime Traps") is False
        assert is_strategy_section("What Worked") is False


# ── Refresh Log Tests ─────────────────────────────────────────────────────


class TestRefreshLog:
    def test_log_and_read_round_trip(self, tmp_path):
        log_path = tmp_path / "log.jsonl"

        results = [
            RefreshResult(
                target_file=".context/MEMORY.md",
                old_value="8-stage",
                new_value="9-stage",
                evidence="stages/*.md count = 9",
                layer=1,
                applied=True,
                confidence=1.0,
            )
        ]

        log_refresh_results(results, log_path)

        entries = read_refresh_log(log_path, since_days=1)
        assert len(entries) == 1
        assert entries[0]["old"] == "8-stage"
        assert entries[0]["new"] == "9-stage"
        assert entries[0]["layer"] == 1

    def test_unapplied_results_not_logged(self, tmp_path):
        log_path = tmp_path / "log.jsonl"

        results = [
            RefreshResult(
                target_file="test.md",
                old_value="old",
                new_value="new",
                evidence="test",
                layer=1,
                applied=False,  # Not applied
            )
        ]

        log_refresh_results(results, log_path)

        entries = read_refresh_log(log_path, since_days=1)
        assert len(entries) == 0
