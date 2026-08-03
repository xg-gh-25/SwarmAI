"""Tests for pipeline_pr.py — Auto PR creation after push-ready delivery.

Verifies: profile gating, title formatting, body structure, branch handling,
graceful failure on gh auth issues, dry-run mode.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys

# Add the skills scripts directory to path for direct import
_scripts_dir = Path(__file__).parent.parent / "skills" / "s_autonomous-pipeline" / "scripts"
sys.path.insert(0, str(_scripts_dir))

from pipeline_pr import create_pr, format_pr_body, format_pr_title


@pytest.fixture
def run_dir(tmp_path):
    """Create a minimal pipeline run directory with run.json + REPORT.md."""
    run_json = {
        "id": "run_test123",
        "project": "SwarmAI",
        "requirement": "Add widget feature for dashboard with real-time updates and filtering",
        "profile": "full",
        "status": "completed",
        "confidence_score": 9,
        "stages": [
            {"stage": "build", "status": "completed", "tdd": {"green_pass": True, "regressions": 0}},
            {"stage": "reflect", "status": "completed", "lessons": ["TDD caught 2 bugs early"]},
        ],
        "adversarial_review": {
            "profile_tier": "full",
            "user_findings": 2,
            "user_fixed": 2,
            "pe_findings": 4,
            "pe_fixed": 4,
            "pe_noted": 0,
        },
        "convergence": {"iterations": 1, "final_status": "push-ready"},
        "ac_verification": {"status": "verified", "matrix": []},
    }
    (tmp_path / "run.json").write_text(json.dumps(run_json))

    report = """# Autonomous Pipeline Report: Widget Feature

**Run ID:** run_test123 | **Project:** SwarmAI | **Profile:** full
**Date:** 2026-05-14 | **Confidence:** 9/12

## TL;DR
Added real-time widget with filtering to the dashboard. TDD-driven, 5 tests, 0 regressions.

## 5. TDD Results
| Metric | Value |
|---|---|
| Acceptance criteria | 5 |
| Tests generated | 7 |
| Regressions | 0 |

## 8. Files Changed
- `backend/routers/widget.py` (created, 45 lines)
- `backend/tests/test_widget.py` (created, 80 lines)
- `desktop/src/components/Widget.tsx` (created, 120 lines)
"""
    (tmp_path / "REPORT.md").write_text(report)
    return tmp_path


@pytest.fixture
def research_run_dir(tmp_path):
    """Run dir with research profile (should be skipped)."""
    run_json = {
        "id": "run_research1",
        "project": "SwarmAI",
        "requirement": "Research caching strategies",
        "profile": "research",
        "status": "completed",
    }
    (tmp_path / "run.json").write_text(json.dumps(run_json))
    return tmp_path


class TestProfileGating:
    """AC7: Only full/bugfix profiles trigger PR creation."""

    def test_skip_research_profile(self, research_run_dir):
        result = create_pr(str(research_run_dir), dry_run=True)
        assert result["skipped"] is True
        assert "research" in result["reason"]

    def test_skip_trivial_profile(self, tmp_path):
        run_json = {"id": "run_t", "profile": "trivial", "requirement": "fix typo"}
        (tmp_path / "run.json").write_text(json.dumps(run_json))
        result = create_pr(str(tmp_path), dry_run=True)
        assert result["skipped"] is True

    def test_skip_goal_profile(self, tmp_path):
        run_json = {"id": "run_g", "profile": "goal", "requirement": "coverage to 90%"}
        (tmp_path / "run.json").write_text(json.dumps(run_json))
        result = create_pr(str(tmp_path), dry_run=True)
        assert result["skipped"] is True

    def test_full_profile_proceeds(self, run_dir):
        result = create_pr(str(run_dir), dry_run=True)
        assert result.get("skipped") is not True

    def test_bugfix_profile_proceeds(self, tmp_path):
        run_json = {
            "id": "run_b",
            "profile": "bugfix",
            "requirement": "Fix login crash",
            "confidence_score": 8,
            "adversarial_review": {"pe_findings": 1, "pe_fixed": 1},
            "convergence": {"iterations": 0, "final_status": "push-ready"},
        }
        (tmp_path / "run.json").write_text(json.dumps(run_json))
        (tmp_path / "REPORT.md").write_text("# Report\n## TL;DR\nFixed login crash.")
        result = create_pr(str(tmp_path), dry_run=True)
        assert result.get("skipped") is not True


class TestTitleFormatting:
    """AC2: PR title <70 chars, meaningful."""

    def test_short_requirement_unchanged(self):
        title = format_pr_title("Fix login crash", ["backend/auth.py"])
        assert len(title) <= 70
        assert "login crash" in title.lower()

    def test_long_requirement_truncated(self):
        long_req = "Add widget feature for dashboard with real-time updates and filtering and sorting and pagination and export"
        title = format_pr_title(long_req, ["backend/routers/widget.py"])
        assert len(title) <= 70

    def test_scope_from_files(self):
        title = format_pr_title("Fix bug", ["backend/routers/auth.py", "backend/core/session.py"])
        assert "backend" in title.lower() or "fix bug" in title.lower()


class TestBodyFormatting:
    """AC3: PR body contains TL;DR, TDD, adversarial, files, confidence."""

    def test_body_has_required_sections(self, run_dir):
        run_json = json.loads((run_dir / "run.json").read_text())
        report_text = (run_dir / "REPORT.md").read_text()
        body = format_pr_body(run_json, report_text)

        assert "## Summary" in body
        assert "Pipeline Delivery" in body or "Confidence" in body
        assert "run_test123" in body
        assert "9/12" in body or "9" in body
        assert "REPORT.md" in body

    def test_body_under_65k(self, run_dir):
        run_json = json.loads((run_dir / "run.json").read_text())
        report_text = (run_dir / "REPORT.md").read_text()
        body = format_pr_body(run_json, report_text)
        assert len(body) < 65000


class TestGracefulFailure:
    """AC6: gh auth failure = non-blocking warning."""

    @patch("subprocess.run")
    def test_gh_auth_failure(self, mock_run, run_dir):
        mock_run.return_value = MagicMock(returncode=1, stderr="not logged in")
        result = create_pr(str(run_dir), dry_run=False)
        assert result["success"] is False
        assert "auth" in result["error"].lower()

    def test_missing_report_md(self, tmp_path):
        run_json = {"id": "run_x", "profile": "full", "requirement": "test"}
        (tmp_path / "run.json").write_text(json.dumps(run_json))
        # No REPORT.md — should still work with minimal body
        result = create_pr(str(tmp_path), dry_run=True)
        assert result.get("skipped") is not True or result.get("success") is not None


class TestDryRun:
    """AC6 related: dry-run mode produces command without executing."""

    def test_dry_run_returns_command(self, run_dir):
        result = create_pr(str(run_dir), dry_run=True)
        assert "command" in result
        assert "gh pr create" in result["command"]
        assert "--auto" in result["command"]

    def test_dry_run_no_subprocess_call(self, run_dir):
        with patch("subprocess.run") as mock_run:
            create_pr(str(run_dir), dry_run=True)
            # Only gh auth status check is allowed, not gh pr create
            for call in mock_run.call_args_list:
                cmd = call[0][0] if call[0] else call.kwargs.get("args", [])
                if isinstance(cmd, list):
                    assert "pr" not in cmd or "create" not in cmd
