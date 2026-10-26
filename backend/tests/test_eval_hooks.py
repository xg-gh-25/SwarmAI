"""Tests for OS Eval P4 — auto-growth hooks (seeder, change-trigger, stable promotion, IV)."""

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from core.eval_service import EvalService
from tests._eval_golden_fixture import install_golden_fixture


@pytest.fixture
def eval_workspace(tmp_path, monkeypatch):
    """Minimal eval workspace."""
    project_dir = tmp_path / "Eval"
    project_dir.mkdir(parents=True)
    history_dir = project_dir / "EvalHistory"
    history_dir.mkdir()

    golden_set = {
        "version": 2,
        "categories": ["compliance", "recall"],
        "dimensions": ["compliance", "factual_accuracy"],
        "cases": [
            {
                "id": "GS001",
                "category": "compliance",
                "dimension": "compliance",
                "level": "session",
                "title": "Pipeline mandatory",
                "source": "C011",
                "affected_by": ["AGENT.md", "STEERING.md"],
                "evaluators": ["file_contains"],
                "tier": "active",
                "verification": {"file": "test.py", "grep": "pipeline"},
            },
            {
                "id": "GS002",
                "category": "recall",
                "dimension": "factual_accuracy",
                "level": "session",
                "title": "Memory recall",
                "source": "KD01",
                "affected_by": ["MEMORY.md"],
                "evaluators": ["canary_pass"],
                "tier": "active",
                "verification": {"command": "echo OK", "expected_contains": "OK"},
            },
        ],
    }

    install_golden_fixture(monkeypatch, tmp_path, golden_set)

    # Write multiple runs with consecutive passes for GS001
    for i in range(12):
        run = {
            "run_id": f"eval_2026060{i:02d}_manual",
            "triggered_by": "manual",
            "triggered_at": f"2026-06-{i+1:02d}T04:00:00Z",
            "overall_score": 100.0,
            "dimensions": {"compliance": 100.0, "factual_accuracy": 100.0},
            "cases": [
                {"id": "GS001", "status": "passed", "duration_ms": 50},
                {"id": "GS002", "status": "passed" if i < 8 else "failed", "duration_ms": 30},
            ],
            "total_cases": 2,
            "cases_passed": 2 if i < 8 else 1,
            "cases_failed": 0 if i < 8 else 1,
            "cases_skipped": 0,
            "duration_seconds": 0.08,
        }
        (history_dir / f"2026-06-{i+1:02d}_manual.json").write_text(json.dumps(run))

    return tmp_path


@pytest.fixture
def svc(eval_workspace):
    return EvalService(workspace_root=eval_workspace)


# ─── Auto-Seed Case from Correction ─────────────────────────────────────────


class TestAutoSeedCase:
    def test_seed_creates_draft_case(self, svc):
        """Correction auto-seeds a golden set case with tier=draft."""
        result = svc.auto_seed_case(
            correction_id="C037",
            correction_text="Skipped adversarial review on trivial fix",
            class_name="CLASS_A",
        )
        assert result is not None
        assert result["id"].startswith("GS_C037")
        assert result["tier"] == "draft"
        assert result["source"] == "C037"
        # M5 Part 2: seeded skeleton is a trajectory_capture behavior draft
        # (excluded from score), NOT the old goal_success+does-not-repeat shape.
        assert result["evaluators"] == ["trajectory_capture"]
        assert result["eval_method"] == "behavior"
        assert result["expected_trajectory"], "skeleton needs a non-empty trajectory"

    def test_seed_does_not_duplicate(self, svc):
        """Same correction_id doesn't create duplicate case."""
        svc.auto_seed_case("C037", "text", "CLASS_A")
        result2 = svc.auto_seed_case("C037", "text again", "CLASS_A")
        assert result2 is None  # Already exists

    def test_seed_persists_to_disk(self, svc, eval_workspace):
        svc.auto_seed_case("C038", "Some correction", "CLASS_B")
        svc2 = EvalService(workspace_root=eval_workspace)
        case = svc2.get_case_detail("GS_C038")
        assert case is not None
        assert case["tier"] == "draft"


# ─── Change-Triggered Eval ───────────────────────────────────────────────────


class TestChangeTrigger:
    def test_get_affected_cases(self, svc):
        """Cases affected by AGENT.md are returned."""
        cases = svc.get_affected_cases(["AGENT.md"])
        assert len(cases) == 1
        assert cases[0]["id"] == "GS001"

    def test_get_affected_cases_multiple_files(self, svc):
        """Cases affected by AGENT.md or MEMORY.md."""
        cases = svc.get_affected_cases(["AGENT.md", "MEMORY.md"])
        assert len(cases) == 2

    def test_get_affected_cases_no_match(self, svc):
        """No cases affected by random file."""
        cases = svc.get_affected_cases(["RANDOM.md"])
        assert len(cases) == 0

    def test_get_affected_cases_excludes_live_tier(self, svc):
        """A live-tier case is NOT auto-triggered by get_affected_cases.

        get_affected_cases is a tier-set site (P8): a 'live' case depends on
        runtime state (a built index) and must NEVER be auto-triggered by a
        file edit via the change-trigger hook — a transient runtime-state miss
        would surface as a spurious canary failure for an ordinary edit (the
        exact flap the live tier exists to prevent). Same class as the behavior
        exclusion already enforced here.
        """
        svc._cases.append({
            "id": "GS_LIVE_AFF",
            "category": "recall",
            "dimension": "factual_accuracy",
            "level": "session",
            "title": "Live case affected by AGENT.md",
            "affected_by": ["AGENT.md"],
            "evaluators": ["file_contains"],
            "tier": "live",
            "verification": {"file": "test.py", "grep": "x"},
        })
        cases = svc.get_affected_cases(["AGENT.md"])
        ids = [c["id"] for c in cases]
        # GS001 (active, affected_by AGENT.md) is still returned; the live one is NOT
        assert "GS001" in ids
        assert "GS_LIVE_AFF" not in ids


# ─── Stable Tier Promotion ───────────────────────────────────────────────────


class TestStablePromotion:
    def test_promote_after_10_consecutive_passes(self, svc):
        """GS001 has 12 consecutive passes → should be promoted to stable."""
        promoted = svc.promote_stable_cases(min_consecutive_passes=10)
        assert "GS001" in promoted

    def test_no_promote_if_recent_failure(self, svc):
        """GS002 has a failure in recent runs → not promoted."""
        promoted = svc.promote_stable_cases(min_consecutive_passes=10)
        assert "GS002" not in promoted

    def test_promotion_persists(self, svc, eval_workspace):
        svc.promote_stable_cases(min_consecutive_passes=10)
        svc2 = EvalService(workspace_root=eval_workspace)
        case = svc2.get_case_detail("GS001")
        assert case["tier"] == "stable"

    def test_live_tier_not_promoted(self, tmp_path, monkeypatch):
        """A tier='live' case with a long passing streak must NEVER auto-promote to
        'stable'. 'stable' IS gate-eligible, so promoting a live case
        would silently re-admit a runtime-state-dependent case to the BVT release
        gate — the exact leak the live tier exists to prevent. promote_stable_cases
        must exclude live alongside archived/stable/draft."""
        from scripts.eval_runner import LIVE_TIER
        project_dir = tmp_path / "Eval"
        project_dir.mkdir(parents=True)
        history_dir = project_dir / "EvalHistory"
        history_dir.mkdir()
        golden_set = {
            "version": 2,
            "categories": ["code_aware"],
            "dimensions": ["capability"],
            "cases": [{
                "id": "GS_LIVE1",
                "category": "code_aware",
                "dimension": "capability",
                "level": "trace",
                "title": "runtime-state case",
                "source": "live-tier-test",
                "affected_by": ["x.py"],
                "evaluators": ["canary_pass"],
                "tier": LIVE_TIER,
                "verification": {"command": "echo OK", "expected_contains": "OK"},
            }],
        }
        install_golden_fixture(monkeypatch, tmp_path, golden_set)
        # 12 consecutive passes — would promote any active case
        for i in range(12):
            run = {
                "run_id": f"eval_live_{i:02d}",
                "triggered_by": "manual",
                "triggered_at": f"2026-06-{i+1:02d}T04:00:00Z",
                "overall_score": 100.0,
                "dimensions": {"capability": 100.0},
                "cases": [{"id": "GS_LIVE1", "status": "passed", "duration_ms": 50}],
                "total_cases": 1, "cases_passed": 1, "cases_failed": 0, "cases_skipped": 0,
                "duration_seconds": 0.05,
            }
            (history_dir / f"2026-06-{i+1:02d}_manual.json").write_text(json.dumps(run))

        svc = EvalService(workspace_root=tmp_path)
        promoted = svc.promote_stable_cases(min_consecutive_passes=10)
        assert "GS_LIVE1" not in promoted, "a live case must not auto-promote to stable"
        assert svc.get_case_detail("GS_LIVE1")["tier"] == LIVE_TIER, "tier stays live"


# ─── Intelligence Velocity ───────────────────────────────────────────────────


class TestIntelligenceVelocity:
    def test_iv_returns_numeric(self, svc):
        """IV is a numeric value."""
        iv = svc.compute_intelligence_velocity()
        assert isinstance(iv, (int, float))
        assert iv >= 0

    def test_iv_components_present(self, svc):
        """IV breakdown includes components."""
        breakdown = svc.compute_intelligence_velocity(detail=True)
        assert "score" in breakdown
        assert "components" in breakdown
        assert "golden_set_size" in breakdown["components"]

    # ─── Error-coverage discount (finding-1) ────────────────────────────────
    # A run where the judge infra broke (cases_error > 0) measured only a
    # subset; its IV must be discounted by COVERAGE, but pass_rate (quality)
    # must stay honest so infra-broke and agent-bad remain distinguishable.

    def _iv(self, svc, run):
        svc._runs = [run]
        return svc.compute_intelligence_velocity(detail=True)["components"]

    def test_clean_run_full_coverage(self, svc):
        c = self._iv(svc, {"overall_score": 100.0, "scored_count": 40,
                           "cases_error": 0, "cases_passed": 40, "cases_failed": 0})
        assert c["pass_rate"] == 100.0
        assert c["coverage"] == 1.0

    def test_judge_broke_discounts_iv_not_pass_rate(self, svc):
        # 40 scored @100%, 88 errored. pass_rate stays 100 (honest quality);
        # coverage drops; final IV is discounted.
        full = self._iv(svc, {"overall_score": 100.0, "scored_count": 40,
                              "cases_error": 0, "cases_passed": 40, "cases_failed": 0})
        broke = self._iv(svc, {"overall_score": 100.0, "scored_count": 40,
                               "cases_error": 88, "cases_passed": 40, "cases_failed": 0})
        assert broke["pass_rate"] == 100.0, "quality axis must stay honest"
        assert abs(broke["coverage"] - 40 / 128) < 0.01
        # base score identical (quality+structure unchanged); only coverage moved it
        assert broke["base_score_pre_coverage"] == full["base_score_pre_coverage"]

    def test_bad_agent_distinct_from_infra_break(self, svc):
        # Genuinely bad agent: low pass_rate, FULL coverage — must NOT look
        # like an infra break (which has high pass_rate, low coverage).
        bad = self._iv(svc, {"overall_score": 50.0, "scored_count": 40,
                             "cases_error": 0, "cases_passed": 20, "cases_failed": 20})
        assert bad["pass_rate"] == 50.0
        assert bad["coverage"] == 1.0

    def test_legacy_run_without_scored_count(self, svc):
        # No scored_count key (legacy) → derive from pass+fail, 0 errors → full coverage.
        c = self._iv(svc, {"overall_score": 90.0, "cases_passed": 9, "cases_failed": 1})
        assert c["pass_rate"] == 90.0
        assert c["coverage"] == 1.0

    def test_no_runs_no_crash(self, svc):
        svc._runs = []
        c = svc.compute_intelligence_velocity(detail=True)["components"]
        assert c["pass_rate"] == 0.0
        assert c["coverage"] == 1.0
