"""Tests for pipeline confidence scoring script.

Verifies the deterministic confidence scoring formula matches the prose rules
from the original INSTRUCTIONS.md. Each test case represents a specific
scoring rule from the design doc.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__),
    "..",
    "skills",
    "s_autonomous-pipeline",
    "scripts",
    "confidence_score.py",
)


def _run_score(run_dir: str) -> dict:
    """Run confidence_score.py and return parsed JSON output."""
    result = subprocess.run(
        [sys.executable, SCRIPT, "--run-dir", run_dir],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return json.loads(result.stdout)


def _make_run_dir(
    run_json: dict | None = None,
    evaluation: dict | None = None,
    changeset: dict | None = None,
    review: dict | None = None,
    test_report: dict | None = None,
) -> str:
    """Create a temporary run directory with artifact files."""
    d = tempfile.mkdtemp(prefix="pipeline_test_")

    # Default minimal run.json
    if run_json is None:
        run_json = {
            "id": "run_test1234",
            "project": "TestProject",
            "profile": "full",
            "status": "running",
            "stages": [
                {"name": "evaluate", "status": "complete"},
                {"name": "think", "status": "complete"},
                {"name": "plan", "status": "complete"},
                {"name": "build", "status": "complete"},
                {"name": "review", "status": "complete"},
                {"name": "test", "status": "complete"},
            ],
            "taste_decisions": [],
        }
    with open(os.path.join(d, "run.json"), "w") as f:
        json.dump(run_json, f)

    if evaluation is not None:
        with open(os.path.join(d, "evaluation.json"), "w") as f:
            json.dump(evaluation, f)
    if changeset is not None:
        with open(os.path.join(d, "changeset.json"), "w") as f:
            json.dump(changeset, f)
    if review is not None:
        with open(os.path.join(d, "review.json"), "w") as f:
            json.dump(review, f)
    if test_report is not None:
        with open(os.path.join(d, "test_report.json"), "w") as f:
            json.dump(test_report, f)

    return d


class TestConfidenceScoring:
    """Test each scoring rule individually and in combination."""

    def test_perfect_score(self):
        """All positive criteria met, no penalties → score 10."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1", "c2", "c3"]},
            changeset={
                "files_changed": ["a.py", "b.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 3,
                    "user_path_traces": 2,
                    "probes": 0,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 5, "connected": 5, "warnings": []},
                "runtime_patterns": {"checked": 8, "passed": 8, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 10, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        assert result["score"] == 10
        assert result["flag_for_review"] is False

    def test_missing_smoke_tests_penalty(self):
        """smoke_tests == 0 and files_changed > 1 → -2."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py", "b.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 0,
                    "user_path_traces": 2,
                    "probes": 0,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3, "connected": 3, "warnings": []},
                "runtime_patterns": {"checked": 5, "passed": 5, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        assert result["score"] == 8  # 10 - 2

    def test_wtf_gate_triggered_penalty(self):
        """WTF score >= 5 → -2."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                    "probes": 0,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3, "connected": 3, "warnings": []},
                "runtime_patterns": {"checked": 5, "passed": 5, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 5},
        )
        result = _run_score(d)
        assert result["score"] == 8  # 10 - 2

    def test_flag_for_review_below_7(self):
        """Score below 7 → flag_for_review is True."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py", "b.py", "c.py"],
                "tdd": {
                    "green_pass": False,
                    "regressions": 2,
                    "smoke_tests": 0,
                    "user_path_traces": 0,
                    "probes": 0,
                },
            },
            review={
                "findings": [{"severity": "critical"}],
                "integration_trace": {"checked": 0, "connected": 0, "warnings": []},
                "runtime_patterns": {"checked": 0, "passed": 0, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 3, "failed": 2, "wtf_score": 0},
        )
        result = _run_score(d)
        assert result["score"] < 7
        assert result["flag_for_review"] is True

    def test_score_clamped_minimum_1(self):
        """Score never goes below 1 even with many penalties."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1", "c2", "c3"]},
            changeset={
                "files_changed": ["a.py", "b.py", "c.tsx", "d.ts"],
                "tdd": {
                    "green_pass": False,
                    "regressions": 5,
                    "smoke_tests": 0,
                    "user_path_traces": 0,
                    "probes": 0,
                },
            },
            review={
                "findings": [{"severity": "critical"}, {"severity": "high"}],
                "integration_trace": {"checked": 0, "connected": 0, "warnings": []},
                "runtime_patterns": {"checked": 0, "passed": 0, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 1, "failed": 5, "wtf_score": 7},
        )
        result = _run_score(d)
        assert result["score"] >= 1

    def test_output_has_breakdown(self):
        """Output includes breakdown list with rule names and points."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                    "probes": 0,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3, "connected": 3, "warnings": []},
                "runtime_patterns": {"checked": 5, "passed": 5, "findings": []},
                "ux_review": {"triggered": False},
                "wire_test": {"boundaries": 0, "verified": 0, "findings": []},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        assert "breakdown" in result
        assert "penalties" in result
        assert isinstance(result["breakdown"], list)

    def test_missing_artifacts_graceful(self):
        """Script handles missing artifact files gracefully."""
        d = _make_run_dir()
        # No evaluation, changeset, review, or test_report
        result = _run_score(d)
        assert "score" in result
        assert result["score"] >= 1


class TestWtfGate:
    """Test WTF gate scoring — separate script."""

    WTF_SCRIPT = os.path.join(
        os.path.dirname(__file__),
        "..",
        "skills",
        "s_autonomous-pipeline",
        "scripts",
        "wtf_gate.py",
    )

    def _run_wtf(self, **kwargs) -> dict:
        args = [sys.executable, self.WTF_SCRIPT]
        for k, v in kwargs.items():
            args.extend([f"--{k.replace('_', '-')}", str(v)])
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        return json.loads(result.stdout)

    def test_clean_pass(self):
        """No risk factors → score 0, no halt."""
        result = self._run_wtf(files_touched=1, fix_count=2)
        assert result["score"] == 0
        assert result["halt"] is False

    def test_many_files_penalty(self):
        """Fix touches > 3 files → +2."""
        result = self._run_wtf(files_touched=5, fix_count=2)
        assert result["score"] >= 2

    def test_unrelated_module_penalty(self):
        """Fix modifies unrelated module → +3."""
        result = self._run_wtf(
            files_touched=1, fix_count=1, unrelated_module=True
        )
        assert result["score"] >= 3

    def test_halt_at_threshold(self):
        """Score >= 5 → halt is True."""
        result = self._run_wtf(
            files_touched=5, fix_count=12, unrelated_module=True
        )
        assert result["halt"] is True

    def test_api_contract_changed(self):
        """API contract change → +2."""
        result = self._run_wtf(
            files_touched=1, fix_count=1, api_contract_changed=True
        )
        assert result["score"] >= 2

    def test_previous_fix_broke(self):
        """Previous fix broke something → +3."""
        result = self._run_wtf(
            files_touched=1, fix_count=1, previous_fix_broke=True
        )
        assert result["score"] >= 3

    def test_output_format(self):
        """Output has score, breakdown, halt, threshold."""
        result = self._run_wtf(files_touched=2, fix_count=3)
        assert "score" in result
        assert "breakdown" in result
        assert "halt" in result
        assert "threshold" in result
        assert result["threshold"] == 5
