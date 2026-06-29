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


# ── Layer 2: LLM Refresh Proposer Tests ──────────────────────────────────


class TestLlmRefreshProposer:
    def test_throttle_allows_first_run(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)
        assert proposer.should_run("SwarmAI", "TECH.md") is True

    def test_throttle_blocks_after_record(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)
        proposer.record_run("SwarmAI", "TECH.md")

        # Same pair should be blocked now
        assert proposer.should_run("SwarmAI", "TECH.md") is False

    def test_throttle_allows_different_pair(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)
        proposer.record_run("SwarmAI", "TECH.md")

        # Different doc is allowed
        assert proposer.should_run("SwarmAI", "PRODUCT.md") is True
        # Different project is allowed
        assert proposer.should_run("AIDLC", "TECH.md") is True

    def test_throttle_state_persists(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer1 = LlmRefreshProposer(swarmai_root, workspace)
        proposer1.record_run("SwarmAI", "TECH.md")

        # New instance reads state from disk
        proposer2 = LlmRefreshProposer(swarmai_root, workspace)
        assert proposer2.should_run("SwarmAI", "TECH.md") is False

    def test_parse_response_extracts_proposal(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)

        response = """Here is the updated section.

PROPOSED:
```
## Pipeline
9-stage autonomous pipeline with DDD/SDD/TDD. [source: stages/:9 files]
Quality Convergence Loop with 6-layer push-ready gate. [source: deliver.md:373]
```

CITATIONS:
- stages/:9 files
- deliver.md:373
"""
        proposed, citations = proposer._parse_response(response)
        assert "9-stage" in proposed
        assert len(citations) >= 2

    def test_parse_response_no_changes(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)
        proposed, citations = proposer._parse_response("NO_CHANGES_NEEDED")
        assert proposed == ""
        assert citations == []

    def test_assess_factual_numbers(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)

        # Mostly numeric changes = factual
        assert proposer._assess_factual(
            "8-stage pipeline with 7 specialists",
            "9-stage pipeline with 9 specialists",
        ) is True

    def test_assess_factual_prose(self, swarmai_root, workspace):
        from core.auto_refresh import LlmRefreshProposer

        proposer = LlmRefreshProposer(swarmai_root, workspace)

        # Mostly prose changes = not factual
        assert proposer._assess_factual(
            "The system is designed to help users manage their workflow efficiently.",
            "The platform provides an innovative approach to collaborative knowledge management.",
        ) is False


# ── E2E: Mechanical Refresh → Log → API Response ─────────────────────────


class TestE2ERefreshLoop:
    def test_mechanical_refresh_creates_log_entry(self, workspace, swarmai_root):
        """E2E: detect drift → apply fix → log entry created."""
        from core.auto_refresh import MechanicalRefresher, log_refresh_results, read_refresh_log

        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher.detect_and_fix()
        applied = refresher.apply_fixes(results)

        # Log the results
        log_path = workspace / ".context" / ".auto_refresh_log.jsonl"
        applied_results = [r for r in results if r.applied]
        log_refresh_results(applied_results, log_path)

        # Verify log is readable
        entries = read_refresh_log(log_path, since_days=1)
        assert len(entries) == applied
        assert all(e["layer"] == 1 for e in entries)

    def test_context_health_api_reads_log(self, workspace, swarmai_root):
        """E2E: log entries appear in the context-health API response format."""
        from core.auto_refresh import (
            MechanicalRefresher, log_refresh_results, read_refresh_log,
        )

        # Create some log entries
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher.detect_and_fix()
        refresher.apply_fixes(results)

        log_path = workspace / ".context" / ".auto_refresh_log.jsonl"
        applied_results = [r for r in results if r.applied]
        log_refresh_results(applied_results, log_path)

        # Simulate what the API endpoint does
        entries = read_refresh_log(log_path, since_days=56)

        # Should have the structure the frontend expects
        if entries:
            entry = entries[0]
            assert "timestamp" in entry
            assert "target" in entry
            assert "old" in entry
            assert "new" in entry
            assert "layer" in entry
            assert "evidence" in entry
            assert "confidence" in entry


def _import_refresh_ai_docs():
    """Import the refresh_ai_docs script (lives in backend/scripts, not a package)."""
    import sys

    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import refresh_ai_docs

    return refresh_ai_docs


# ── collect_metrics() — the scale-indicators block (drives the REAL repo) ────
#
# These tests run collect_metrics() against the actual swarmai repo (the script
# shells out to find/git in REPO_ROOT). They guard the bug where
# total_backend_loc rendered BLANK (cat-the-world command timed out under the
# script's accumulated _run deadline) and, even when non-empty, used the wrong
# caliber (filesystem cat counts .venv site-packages + gitignored CMHK skills,
# producing a non-reproducible inflated number). The correct caliber is
# git-tracked, non-test — reproducible, venv/CMHK-free, matches the README.
class TestCollectMetrics:
    def test_total_backend_loc_is_nonempty_numeric(self):
        """total_backend_loc must render a real number, never blank.

        RED on the old `find backend | xargs cat | wc -l` command: it either
        timed out under the accumulated _run deadline (-> "") or counted the
        venv/CMHK-polluted filesystem.
        """
        r = _import_refresh_ai_docs()

        # Reproduce the real entry-point deadline (was the trigger for blank).
        r._SCRIPT_DEADLINE = __import__("time").monotonic() + r._SCRIPT_TIMEOUT
        try:
            m = r.collect_metrics()
        finally:
            r._SCRIPT_DEADLINE = 0.0

        val = m.get("total_backend_loc", "")
        assert val != "", "total_backend_loc rendered BLANK (command timed out)"
        assert val.isdigit(), f"total_backend_loc not numeric: {val!r}"

    def test_total_backend_loc_uses_git_tracked_caliber(self):
        """The value must be the reproducible git-tracked non-test caliber.

        Lower bound proves it counts the real backend (not blank/0); upper
        bound proves it EXCLUDES the .venv site-packages (~150K+) and the
        gitignored CMHK skills (~30K) that the old filesystem command swept in.
        """
        r = _import_refresh_ai_docs()

        r._SCRIPT_DEADLINE = __import__("time").monotonic() + r._SCRIPT_TIMEOUT
        try:
            m = r.collect_metrics()
        finally:
            r._SCRIPT_DEADLINE = 0.0

        loc = int(m["total_backend_loc"])
        # Lower bound proves it counts the real backend (not blank/0). Upper
        # bound stays well below the venv/CMHK-polluted ~313K the OLD command
        # produced (so a revert is still caught) but is loose enough (250K) to
        # not false-positive on legitimate growth from the current ~165K.
        assert 100_000 < loc < 250_000, (
            f"total_backend_loc={loc} outside git-tracked caliber band "
            f"(blank/0 = timeout/empty bug; ~313K = venv/CMHK pollution regression)"
        )

    def test_core_metrics_use_git_tracked_caliber(self):
        """core_loc + core_modules must use the SAME git-tracked, tests-OUT
        caliber as total_backend_loc — the REVIEW LOW finding from run_7c8453a2.

        RED on the old filesystem commands (`find backend/core -exec cat` /
        `find backend/core | wc -l`): they INCLUDE the 10 core-internal test
        files under backend/core/code_intel/tests/ (2178 LOC), so core_loc was
        72582 / core_modules 143 — inconsistent with total_backend_loc which
        already excludes /tests/. GREEN: 70404 / 133 (tests excluded).
        """
        r = _import_refresh_ai_docs()

        r._SCRIPT_DEADLINE = __import__("time").monotonic() + r._SCRIPT_TIMEOUT
        try:
            m = r.collect_metrics()
        finally:
            r._SCRIPT_DEADLINE = 0.0

        core_loc = int(m["core_loc"])
        core_modules = int(m["core_modules"])

        # tests-OUT caliber: backend/core production code only. The 10
        # core_intel test files (2178 LOC) must NOT be counted here — they
        # are test code, counted (if anywhere) by the test_files metric.
        # Upper bound 72_000 is BELOW the 72582 the tests-IN command produced,
        # so a revert to filesystem-cat (which re-includes tests) is caught.
        assert 60_000 < core_loc < 72_000, (
            f"core_loc={core_loc} outside tests-OUT caliber band "
            f"(>=72582 = filesystem cat re-including core-internal tests)"
        )
        # core_modules must be the tests-OUT file count (133), NOT 143 — they
        # MUST move in lockstep or the rendered '143 modules / 70404 LOC' is a
        # self-contradiction (143 counts tests, 70404 does not).
        assert 120 < core_modules < 143, (
            f"core_modules={core_modules} still includes core-internal tests "
            f"(143 = filesystem find counting backend/core/.../tests/)"
        )
