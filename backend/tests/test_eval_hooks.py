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
