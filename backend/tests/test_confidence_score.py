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

    def test_completion_audit_all_green_bonus(self):
        """completion_audit.all_green → +2 bonus."""
        d = _make_run_dir(
            run_json={
                "id": "run_test_audit",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [
                    {"name": "plan", "status": "complete"},
                ],
                "taste_decisions": [],
                "completion_audit": {
                    "all_green": True,
                    "gaps": 0,
                    "unfixable_gaps": 0,
                },
            },
            evaluation={"acceptance_criteria": ["c1", "c2"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        # Should include the +2 audit bonus
        audit_rules = [b for b in result["breakdown"] if b["rule"] == "completion_audit_all_green"]
        assert len(audit_rules) == 1
        assert audit_rules[0]["points"] == 2

    def test_completion_audit_gaps_not_fixed_penalty(self):
        """completion_audit.gaps > 0 and not fixed → -3 penalty."""
        d = _make_run_dir(
            run_json={
                "id": "run_test_gaps",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [
                    {"name": "plan", "status": "complete"},
                ],
                "taste_decisions": [],
                "completion_audit": {
                    "all_green": False,
                    "gaps": 2,
                    "unfixable_gaps": 0,
                },
            },
            evaluation={"acceptance_criteria": ["c1", "c2"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        gap_penalties = [p for p in result["penalties"] if p["rule"] == "completion_audit_gaps"]
        assert len(gap_penalties) == 1
        assert gap_penalties[0]["points"] == -3

    def test_completion_audit_unfixable_gaps_penalty(self):
        """completion_audit.unfixable_gaps > 0 → -1 penalty."""
        d = _make_run_dir(
            run_json={
                "id": "run_test_unfixable",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [
                    {"name": "plan", "status": "complete"},
                ],
                "taste_decisions": [],
                "completion_audit": {
                    "all_green": False,
                    "gaps": 0,
                    "unfixable_gaps": 1,
                },
            },
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        unfixable_penalties = [p for p in result["penalties"] if p["rule"] == "completion_audit_unfixable"]
        assert len(unfixable_penalties) == 1
        assert unfixable_penalties[0]["points"] == -1

    def test_completion_audit_in_deliver_stage_fallback(self):
        """completion_audit nested inside deliver stage record → still detected."""
        d = _make_run_dir(
            run_json={
                "id": "run_test_nested",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [
                    {"name": "plan", "status": "complete"},
                    {
                        "stage": "deliver",
                        "status": "complete",
                        "completion_audit": {
                            "all_green": True,
                            "gaps": 0,
                            "unfixable_gaps": 0,
                        },
                    },
                ],
                "taste_decisions": [],
                # NOTE: no top-level completion_audit — tests the fallback path
            },
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        audit_rules = [b for b in result["breakdown"] if b["rule"] == "completion_audit_all_green"]
        assert len(audit_rules) == 1, f"Expected audit bonus from stage fallback, got: {result['breakdown']}"
        assert audit_rules[0]["points"] == 2

    def test_completion_audit_contradictory_state(self):
        """all_green=True with unfixable_gaps=1 → bonus wins, no unfixable penalty."""
        d = _make_run_dir(
            run_json={
                "id": "run_test_contradict",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [
                    {"name": "plan", "status": "complete"},
                ],
                "taste_decisions": [],
                "completion_audit": {
                    "all_green": True,
                    "gaps": 0,
                    "unfixable_gaps": 1,  # contradicts all_green
                },
            },
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        # all_green=True → bonus applies
        audit_rules = [b for b in result["breakdown"] if b["rule"] == "completion_audit_all_green"]
        assert len(audit_rules) == 1
        # unfixable penalty should NOT apply when all_green=True
        unfixable_penalties = [p for p in result["penalties"] if p["rule"] == "completion_audit_unfixable"]
        assert len(unfixable_penalties) == 0

    def test_no_completion_audit_no_effect(self):
        """Missing completion_audit field → no bonus, no penalty."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {
                    "green_pass": True,
                    "regressions": 0,
                    "smoke_tests": 1,
                    "user_path_traces": 1,
                },
            },
            review={
                "findings": [],
                "integration_trace": {"checked": 3},
                "runtime_patterns": {"checked": 5},
            },
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        audit_rules = [b for b in result["breakdown"] if "audit" in b["rule"]]
        audit_penalties = [p for p in result["penalties"] if "audit" in p["rule"]]
        assert len(audit_rules) == 0
        assert len(audit_penalties) == 0


class TestACVerification:
    """Test AC verification scoring — verified vs claimed vs failed."""

    def test_ac_verified_gives_plus_3(self):
        """ac_verification.status == 'verified' → +3 (replaces old +3 for claimed)."""
        d = _make_run_dir(
            run_json={
                "id": "run_ac_verified",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [{"name": "plan", "status": "complete"}],
                "taste_decisions": [],
                "ac_verification": {
                    "status": "verified",
                    "matrix": [
                        {"ac": "Feature works E2E", "test": "test_e2e", "verifies_ac": True, "e2e_connected": True}
                    ],
                },
            },
            evaluation={"acceptance_criteria": ["Feature works E2E"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {"green_pass": True, "regressions": 0, "smoke_tests": 1, "user_path_traces": 1},
            },
            review={"findings": [], "integration_trace": {"checked": 3}, "runtime_patterns": {"checked": 5}},
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        ac_rules = [b for b in result["breakdown"] if b["rule"] == "ac_verified"]
        assert len(ac_rules) == 1
        assert ac_rules[0]["points"] == 3

    def test_ac_claimed_gives_plus_1(self):
        """ac_verification.status == 'claimed' (or absent) + tests exist → +1."""
        d = _make_run_dir(
            run_json={
                "id": "run_ac_claimed",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [{"name": "plan", "status": "complete"}],
                "taste_decisions": [],
                "ac_verification": {"status": "claimed"},
            },
            evaluation={"acceptance_criteria": ["Feature works"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {"green_pass": True, "regressions": 0, "smoke_tests": 1, "user_path_traces": 1},
            },
            review={"findings": [], "integration_trace": {"checked": 3}, "runtime_patterns": {"checked": 5}},
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        ac_rules = [b for b in result["breakdown"] if b["rule"] == "ac_claimed"]
        assert len(ac_rules) == 1
        assert ac_rules[0]["points"] == 1

    def test_ac_failed_gives_minus_3(self):
        """ac_verification.status == 'failed' → -3 penalty."""
        d = _make_run_dir(
            run_json={
                "id": "run_ac_failed",
                "project": "TestProject",
                "profile": "full",
                "status": "running",
                "stages": [{"name": "plan", "status": "complete"}],
                "taste_decisions": [],
                "ac_verification": {
                    "status": "failed",
                    "matrix": [
                        {"ac": "Feature works", "test": "test_foo", "verifies_ac": False, "e2e_connected": False}
                    ],
                },
            },
            evaluation={"acceptance_criteria": ["Feature works"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {"green_pass": True, "regressions": 0, "smoke_tests": 1, "user_path_traces": 1},
            },
            review={"findings": [], "integration_trace": {"checked": 3}, "runtime_patterns": {"checked": 5}},
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        ac_penalties = [p for p in result["penalties"] if p["rule"] == "ac_verification_failed"]
        assert len(ac_penalties) == 1
        assert ac_penalties[0]["points"] == -3

    def test_no_ac_verification_backward_compat(self):
        """No ac_verification field at all → falls back to old +3 (tests exist logic)."""
        d = _make_run_dir(
            evaluation={"acceptance_criteria": ["c1", "c2"]},
            changeset={
                "files_changed": ["a.py"],
                "tdd": {"green_pass": True, "regressions": 0, "smoke_tests": 1, "user_path_traces": 1},
            },
            review={"findings": [], "integration_trace": {"checked": 3}, "runtime_patterns": {"checked": 5}},
            test_report={"passed": 5, "failed": 0, "wtf_score": 0},
        )
        result = _run_score(d)
        # Should use old logic: +3 for acceptance_criteria_tested
        ac_rules = [b for b in result["breakdown"] if b["rule"] == "acceptance_criteria_tested"]
        assert len(ac_rules) == 1
        assert ac_rules[0]["points"] == 3


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
