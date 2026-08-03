"""Tests for DDD & Memory Auto-Refresh Engine (Layer 1 mechanical + shared utilities).

Tests:
- MechanicalRefresher: stage count, specialist count, RP count detection+fix
- ValueGate: filters out archived/inactive content
- CitationVerifier: verifies file:line citations
- MemoryEntryRefresher: detects stale constants in MEMORY.md
- RefreshResult + log: serialization round-trip
"""

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
    def test_stage_count_verifier_disabled_in_pipeline(self, workspace, swarmai_root):
        """stage-count is DISABLED in detect_and_fix (run_254f5e52): file-count can't
        yield the canonical 9 (embedded adversarial has no file), so it would corrupt
        a correct "9-stage". The method still works if called directly, but must NOT
        run in the auto-fix pipeline."""
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher.detect_and_fix()
        # no stage-count fix should be produced by the pipeline
        assert not any("stage count" in r.evidence for r in results), (
            "stage-count must stay disabled — it cannot be deterministically correct"
        )

    def test_detects_stale_specialist_count(self, workspace, swarmai_root):
        refresher = MechanicalRefresher(swarmai_root, workspace)
        results = refresher._check_specialist_count()

        stale = [r for r in results if "7" in r.old_value]
        assert len(stale) >= 1
        assert "9" in stale[0].new_value

    def test_rp_count_verifier_disabled_in_pipeline(self, workspace, swarmai_root):
        """RP-count is DISABLED in detect_and_fix (run_254f5e52): RP1-RPN refs in DDD
        prose are mostly HISTORICAL (quoting code-at-the-time / dated snapshots), not
        live counts — auto-swapping falsifies the record. The method still works if
        called directly, but must NOT run in the auto-fix pipeline."""
        refresher = MechanicalRefresher(swarmai_root, workspace)
        # method intact when called directly
        assert isinstance(refresher._check_rp_count(), list)
        # but the pipeline must not emit any RP fix
        assert not any(
            "review pattern" in r.evidence.lower() or "RP1-RP" in r.new_value
            for r in refresher.detect_and_fix()
        )

    def test_apply_fixes_modifies_file(self, tmp_path):
        """apply_fixes writes a real correction to disk — via version-stamp, the ONLY
        FIXABLE verifier active in the pipeline (stage/decay/RP all disabled as unsafe)."""
        swarmai = tmp_path / "swarmai"
        swarmai.mkdir()
        (swarmai / "VERSION").write_text("1.25.0\n")
        ws = tmp_path / "ws"
        proj = ws / "Projects" / "SwarmAI"
        proj.mkdir(parents=True)
        project_md = proj / "PROJECT.md"
        project_md.write_text("### Version: v1.21.0 | stale stamp\n")

        refresher = MechanicalRefresher(swarmai, ws)
        results = refresher.detect_and_fix()
        assert len(results) > 0
        applied = refresher.apply_fixes(results)
        assert applied > 0

        content = project_md.read_text()
        assert "v1.25.0" in content
        assert "v1.21.0" not in content

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

        The bug this guards: the old filesystem commands (`find backend/core
        -exec cat` / `find backend/core | wc -l`) INCLUDE the core-internal
        test files under backend/core/code_intel/tests/, so core_loc/core_modules
        were inconsistent with total_backend_loc (which already excludes /tests/).

        Discriminator is SEMANTIC, not a frozen number (AGENT R30#4 — don't
        store drift-prone LOC ceilings; legit code growth pushed a prior fixed
        `< 72_000` ceiling RED at 72019). We measure BOTH calibers live and
        assert the tests-OUT metric is strictly smaller than the tests-IN
        caliber — i.e. it genuinely excluded the core-internal tests. A revert
        to the tests-IN command makes core_loc == the tests-IN number → RED.
        """
        r = _import_refresh_ai_docs()

        r._SCRIPT_DEADLINE = __import__("time").monotonic() + r._SCRIPT_TIMEOUT
        try:
            m = r.collect_metrics()

            core_loc = int(m["core_loc"])
            core_modules = int(m["core_modules"])

            # tests-IN caliber = the regression the metric must NOT match (it
            # re-includes backend/core/**/tests/). Measured live via the SAME
            # _run helper + cwd the metric uses, so it never drifts and the two
            # calibers are apples-to-apples (only the /tests/ filter differs).
            def _int(out: str) -> int:
                out = (out or "").strip()
                return int(out) if out.isdigit() else 0

            tests_in_loc = _int(r._run(
                "git ls-files '*.py' | grep '^backend/core/' "
                "| xargs wc -l | awk '$2!=\"total\"{n+=$1} END{print n}'"
            ))
            tests_in_mods = _int(r._run(
                "git ls-files '*.py' | grep '^backend/core/' | wc -l"
            ))
        finally:
            r._SCRIPT_DEADLINE = 0.0

        # Sanity floor: proves the metric counts the real core (not blank/0),
        # without pinning an upper bound that legit growth would trip.
        assert core_loc > 50_000, f"core_loc={core_loc} implausibly small (blank/0 bug?)"
        assert core_modules > 100, f"core_modules={core_modules} implausibly small"

        # Precondition: there ARE core-internal test files to exclude, else the
        # discriminator below is vacuous (tests-OUT would equal tests-IN through
        # no fault of the metric). If this ever fails, the test — not the metric
        # — needs revisiting (backend/core/**/tests/ was emptied).
        assert tests_in_mods > core_modules, (
            f"discriminator vacuous: tests-IN modules ({tests_in_mods}) not "
            f"greater than tests-OUT ({core_modules}) — are there any "
            f"backend/core/**/tests/ files left to exclude?"
        )

        # The real property: tests-OUT is strictly SMALLER than tests-IN —
        # i.e. the core-internal test files were genuinely excluded. A revert to
        # the filesystem-cat / tests-IN command makes these equal → RED.
        assert core_loc < tests_in_loc, (
            f"core_loc={core_loc} is NOT smaller than the tests-IN caliber "
            f"({tests_in_loc}) — core-internal tests are being re-included"
        )
        assert core_modules < tests_in_mods, (
            f"core_modules={core_modules} is NOT smaller than the tests-IN "
            f"caliber ({tests_in_mods}) — core-internal test files re-included"
        )


# ── Version + Decay drift verifiers (run_254f5e52) ──────────────────────────


class TestVersionAndDecayDrift:
    """FIXABLE-tier verifiers added for DDD zero-drift: version stamp + decay windows.
    Deterministic (filesystem SoT), context-scoped to avoid corrupting narrative prose."""

    def _mk_roots(self, tmp_path, version="1.25.0"):
        swarmai = tmp_path / "swarmai"
        swarmai.mkdir()
        (swarmai / "VERSION").write_text(version + "\n")
        # decay constants live in ddd_entry_lifecycle — the real module is the SoT
        # for _check_decay_windows (imported at call time), so no fixture needed.
        ws = tmp_path / "ws"
        proj = ws / "Projects" / "SwarmAI"
        proj.mkdir(parents=True)
        return swarmai, ws, proj

    def test_detects_and_fixes_stale_version_stamp(self, tmp_path):
        swarmai, ws, proj = self._mk_roots(tmp_path, version="1.25.0")
        (proj / "PROJECT.md").write_text(
            "### Version: v1.21.0 (pending release) | Core Engine\n"
            "Narrative: v1.20.1→v1.21.0 was a big release.\n"  # must NOT be touched
        )
        r = MechanicalRefresher(swarmai, ws)
        results = r.detect_and_fix()
        vfix = [x for x in results if "version stamp" in x.evidence]
        assert len(vfix) == 1, f"expected 1 version fix, got {[x.old_value for x in vfix]}"
        assert "v1.25.0" in vfix[0].new_value
        assert vfix[0].new_value.startswith("### Version:")
        # narrative line untouched: the fix targets only the header line
        assert "v1.20.1" not in vfix[0].old_value

    def test_version_stamp_no_false_positive_on_narrative(self, tmp_path):
        swarmai, ws, proj = self._mk_roots(tmp_path, version="1.25.0")
        # No `Version:` header — only narrative prose citing old versions
        (proj / "PROJECT.md").write_text(
            "The v1.20.1→v1.21.0 release shipped 274 commits. See Issue #77.\n"
        )
        r = MechanicalRefresher(swarmai, ws)
        vfix = [x for x in r.detect_and_fix() if "version stamp" in x.evidence]
        assert vfix == [], f"narrative prose wrongly flagged: {[x.old_value for x in vfix]}"

    def test_version_already_current_no_fix(self, tmp_path):
        swarmai, ws, proj = self._mk_roots(tmp_path, version="1.25.0")
        (proj / "PROJECT.md").write_text("### Version: v1.25.0 | current\n")
        r = MechanicalRefresher(swarmai, ws)
        assert [x for x in r.detect_and_fix() if "version stamp" in x.evidence] == []

    def test_decay_window_verifier_disabled_in_pipeline(self, tmp_path):
        """decay-window is DISABLED in detect_and_fix (run_254f5e52): a prose line
        carries multiple distinct day-windows (dormant/archived/grace/per-section)
        and a context-word gate cross-maps them, corrupting correct text. It is
        SEMANTIC, routed to the LLM tier — must NOT run in the deterministic pipeline."""
        swarmai, ws, proj = self._mk_roots(tmp_path)
        (proj / "TECH.md").write_text(
            "Decay: 90d dormant (180d if ref>=10), <30d immune. Archived after 180d.\n"
        )
        r = MechanicalRefresher(swarmai, ws)
        assert not any("decay window" in x.evidence for x in r.detect_and_fix()), (
            "decay-window must stay disabled — it cannot be deterministically correct"
        )

    def test_seeded_version_drift_auto_repairs_end_to_end(self, tmp_path):
        """DoD-E proof: seed a version drift, run detect_and_fix + apply_fixes
        directly (no scheduler), assert the file is corrected on disk."""
        swarmai, ws, proj = self._mk_roots(tmp_path, version="1.25.0")
        project_md = proj / "PROJECT.md"
        project_md.write_text("### Version: v1.21.0 | stale\n")
        r = MechanicalRefresher(swarmai, ws)
        results = r.detect_and_fix()
        applied = r.apply_fixes(results)
        assert applied >= 1
        # the loop closed: the file on disk now reflects the SoT, zero human action
        assert "v1.25.0" in project_md.read_text()
        assert "v1.21.0" not in project_md.read_text()
