"""Tests for eval_runner.py.

Uses a committed fixture (testdata/golden_set_fixture.yaml) for structural
validation tests. Tests that need live workspace data are marked and skip
gracefully if workspace is not available (CI-safe).
"""
import json
import sys
from pathlib import Path

import pytest

# Add parent to path for import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from unittest.mock import patch

from backend.scripts.eval_runner import (
    load_golden_set,
    compute_scores,
    eval_keyword_match,
    eval_trajectory,
    eval_llm_judge,
    evaluate_case,
    filter_cases_by_tags,
    _find_workspace_root,
    _find_swarmai_repo,
    _get_judge_model,
)
import backend.scripts.eval_runner as eval_runner_mod

# Committed fixture path (always available, even in CI)
FIXTURE_PATH = Path(__file__).resolve().parent / "testdata" / "golden_set_fixture.yaml"


def _workspace_available() -> bool:
    """Check if live workspace golden_set exists (not available in CI)."""
    try:
        root = _find_workspace_root()
        return (root / "Projects" / "SwarmAI" / "golden_set.yaml").exists()
    except FileNotFoundError:
        return False


class TestLoadGoldenSet:
    """Test golden_set.yaml loading and validation (uses fixture for CI-safety)."""

    def test_loads_successfully(self):
        data = load_golden_set(FIXTURE_PATH)
        assert data["version"] == 2
        assert len(data["cases"]) >= 3

    def test_all_cases_have_required_fields(self):
        data = load_golden_set(FIXTURE_PATH)
        for case in data["cases"]:
            assert "id" in case, f"Missing id: {case.get('title')}"
            assert "evaluators" in case, f"{case['id']} missing evaluators"
            assert "affected_by" in case, f"{case['id']} missing affected_by"
            assert "category" in case, f"{case['id']} missing category"
            assert "dimension" in case, f"{case['id']} missing dimension"
            assert "title" in case, f"{case['id']} missing title"

    def test_unique_ids(self):
        data = load_golden_set(FIXTURE_PATH)
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_valid_categories(self):
        data = load_golden_set(FIXTURE_PATH)
        valid_categories = set(data["categories"])
        for case in data["cases"]:
            assert case["category"] in valid_categories, f"{case['id']} has invalid category '{case['category']}'"

    @pytest.mark.skipif(not _workspace_available(), reason="Live workspace not available (CI)")
    def test_live_workspace_loads(self):
        """Smoke test: live workspace golden_set parses without error."""
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        assert data["version"] == 2
        assert len(data["cases"]) >= 10


class TestComputeScores:
    """Test score computation logic."""

    def test_all_passed(self):
        cases = [{"dimension": "capability"}, {"dimension": "capability"}]
        results = [{"status": "passed"}, {"status": "passed"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 100.0
        assert scores["dimensions"]["capability"] == 100.0

    def test_mixed_results(self):
        cases = [{"dimension": "capability"}, {"dimension": "factual_accuracy"}]
        results = [{"status": "passed"}, {"status": "failed"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 50.0
        assert scores["dimensions"]["capability"] == 100.0
        assert scores["dimensions"]["factual_accuracy"] == 0.0

    def test_skipped_excluded(self):
        cases = [{"dimension": "capability"}, {"dimension": "compliance"}]
        results = [{"status": "passed"}, {"status": "skipped"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 100.0  # Only scored cases count
        assert scores["scored_count"] == 1
        assert scores["skipped_count"] == 1

    def test_all_skipped(self):
        cases = [{"dimension": "capability"}]
        results = [{"status": "skipped"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 0.0
        assert scores["scored_count"] == 0


class TestPathResolution:
    """Test workspace/repo discovery."""

    def test_workspace_found(self):
        root = _find_workspace_root()
        assert (root / "Projects" / "SwarmAI").is_dir()

    def test_swarmai_repo_found(self):
        repo = _find_swarmai_repo()
        assert (repo / "backend" / "core").is_dir()


class TestKeywordMatch:
    """Test keyword_match evaluator."""

    def test_all_keywords_present(self):
        case = {
            "id": "TEST001",
            "expected_response_contains": ["pipeline", "trivial"],
            "evaluators": ["keyword_match"],
        }
        # Simulate a response that contains both keywords
        result = eval_keyword_match(case, simulated_response="Use pipeline with trivial profile")
        assert result["status"] == "passed"

    def test_keyword_missing(self):
        case = {
            "id": "TEST002",
            "expected_response_contains": ["pipeline", "DEFER"],
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="Use pipeline for this change")
        assert result["status"] == "failed"
        assert "DEFER" in result["notes"]

    def test_case_insensitive(self):
        case = {
            "id": "TEST003",
            "expected_response_contains": ["Pipeline"],
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="run pipeline now")
        assert result["status"] == "passed"

    def test_no_keywords_defined(self):
        case = {
            "id": "TEST004",
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="anything")
        assert result["status"] == "skipped"


class TestTrajectoryEvaluator:
    """Test trajectory_* evaluators."""

    def test_in_order_pass(self):
        case = {
            "id": "TEST010",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Read target file",
            "Discuss approach",
            "Invoke pipeline",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_in_order_fail_wrong_order(self):
        case = {
            "id": "TEST011",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Invoke pipeline",
            "Read target file",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_in_order_fail_missing_step(self):
        case = {
            "id": "TEST012",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = ["Read target file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_any_order_pass(self):
        case = {
            "id": "TEST013",
            "expected_trajectory": ["Invoke pipeline", "Read target file"],
            "trajectory_match": "any_order",
            "evaluators": ["trajectory_any_order"],
        }
        actual_trajectory = [
            "Read target file",
            "Invoke pipeline",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_exact_pass(self):
        case = {
            "id": "TEST014",
            "expected_trajectory": ["Read file", "Edit file"],
            "trajectory_match": "exact",
            "evaluators": ["trajectory_exact"],
        }
        actual_trajectory = ["Read file", "Edit file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_exact_fail_extra_step(self):
        case = {
            "id": "TEST015",
            "expected_trajectory": ["Read file", "Edit file"],
            "trajectory_match": "exact",
            "evaluators": ["trajectory_exact"],
        }
        actual_trajectory = ["Read file", "Think", "Edit file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_no_trajectory_skips(self):
        case = {
            "id": "TEST016",
            "evaluators": ["trajectory_in_order"],
        }
        result = eval_trajectory(case, actual_trajectory=[])
        assert result["status"] == "skipped"

    def test_substring_matching(self):
        """Trajectory steps should match as substrings (case-insensitive)."""
        case = {
            "id": "TEST017",
            "expected_trajectory": ["Read initialization_manager", "Verify get_session_context exists"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Read file: backend/core/initialization_manager.py",
            "Grep: verify get_session_context exists in the module",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"


class TestFilterCasesByTags:
    """Test tag-based filtering of cases."""

    def test_filter_smoke(self):
        cases = [
            {"id": "A", "tags": ["smoke", "regression"]},
            {"id": "B", "tags": ["full"]},
            {"id": "C", "tags": ["smoke"]},
            {"id": "D"},  # no tags
        ]
        filtered = filter_cases_by_tags(cases, ["smoke"])
        assert len(filtered) == 2
        assert {c["id"] for c in filtered} == {"A", "C"}

    def test_filter_multiple_tags(self):
        cases = [
            {"id": "A", "tags": ["smoke"]},
            {"id": "B", "tags": ["regression"]},
            {"id": "C", "tags": ["full"]},
        ]
        filtered = filter_cases_by_tags(cases, ["smoke", "regression"])
        assert len(filtered) == 2

    def test_no_filter_returns_all(self):
        cases = [{"id": "A", "tags": ["smoke"]}, {"id": "B"}]
        filtered = filter_cases_by_tags(cases, None)
        assert len(filtered) == 2

    def test_empty_tags_returns_all(self):
        cases = [{"id": "A", "tags": ["smoke"]}, {"id": "B"}]
        filtered = filter_cases_by_tags(cases, [])
        assert len(filtered) == 2


class TestProgrammaticFirstCascade:
    """Test that programmatic evaluators are tried before LLM judge."""

    def test_keyword_match_resolves_without_llm(self):
        """Case with expected_response_contains + keyword_match evaluator should not need LLM."""
        case = {
            "id": "GS_TEST",
            "category": "compliance",
            "dimension": "compliance",
            "evaluators": ["keyword_match", "goal_success"],
            "expected_response_contains": ["pipeline"],
            "affected_by": ["STEERING.R1"],
        }
        repo_root = _find_swarmai_repo()
        result = evaluate_case(case, repo_root, simulated_response="Run the pipeline here")
        assert result["evaluator"] == "keyword_match"
        assert result["status"] == "passed"

    def test_fallthrough_to_llm_when_no_programmatic(self):
        """Case with only LLM evaluators falls through."""
        case = {
            "id": "GS_LLM_ONLY",
            "category": "decision",
            "dimension": "judgment_quality",
            "evaluators": ["goal_success"],
            "assertions": ["Agent decides correctly"],
            "affected_by": ["MEMORY.KD34"],
        }
        repo_root = _find_swarmai_repo()
        result = evaluate_case(case, repo_root)
        assert result["evaluator"] == "goal_success"
        assert result["status"] == "skipped"  # LLM not implemented yet


class TestGoldenSetNewFields:
    """Test that golden_set.yaml supports new fields (uses committed fixture)."""

    def test_tags_field_accepted(self):
        """Cases with tags field should load without error."""
        data = load_golden_set(FIXTURE_PATH)
        tagged_cases = [c for c in data["cases"] if c.get("tags")]
        assert len(tagged_cases) >= 3, f"Expected >=3 tagged cases in fixture, got {len(tagged_cases)}"

    def test_promoted_from_field_accepted(self):
        """Cases with promoted_from field should load without error."""
        data = load_golden_set(FIXTURE_PATH)
        promoted = [c for c in data["cases"] if c.get("promoted_from")]
        assert len(promoted) >= 1, "Expected at least 1 case with promoted_from in fixture"

    def test_fixture_has_all_evaluator_types(self):
        """Fixture covers all programmatic evaluator types."""
        data = load_golden_set(FIXTURE_PATH)
        evaluators_used = set()
        for case in data["cases"]:
            for ev in case.get("evaluators", []):
                evaluators_used.add(ev)
        # Fixture should exercise keyword_match, canary_pass, file_contains, trajectory_in_order, goal_success
        assert "keyword_match" in evaluators_used
        assert "canary_pass" in evaluators_used
        assert "file_contains" in evaluators_used
        assert "trajectory_in_order" in evaluators_used
        assert "goal_success" in evaluators_used


class TestEvalHistoryOutput:
    """Test that eval run produces valid output (workspace-dependent, skips in CI)."""

    @pytest.mark.skipif(not _workspace_available(), reason="Live workspace not available (CI)")
    def test_output_file_exists(self):
        root = _find_workspace_root()
        hist_dir = root / "Projects" / "SwarmAI" / "EvalHistory"
        json_files = list(hist_dir.glob("*.json"))
        assert len(json_files) > 0, "No eval history files found"

    @pytest.mark.skipif(not _workspace_available(), reason="Live workspace not available (CI)")
    def test_output_schema_valid(self):
        root = _find_workspace_root()
        hist_dir = root / "Projects" / "SwarmAI" / "EvalHistory"
        json_files = sorted(hist_dir.glob("*.json"))
        if not json_files:
            pytest.skip("No eval history files to validate")
        latest = json_files[-1]
        data = json.loads(latest.read_text())

        assert "run_id" in data
        assert "triggered_by" in data
        assert "overall_score" in data
        assert "dimensions" in data
        assert "cases" in data
        assert "total_cases" in data
        assert isinstance(data["overall_score"], (int, float))
        assert isinstance(data["cases"], list)


class TestJudgeModelResolution:
    """_get_judge_model() must resolve short names to full Bedrock IDs.

    Regression: it used to return the raw config short name, so converse()
    threw "model identifier is invalid" → every judge call silently skipped
    → 88 LLM cases never ran → a fake 100/100 (run_123a6530).
    """

    def _cfg(self, **kw):
        """Patch config + map for _get_judge_model (reads config.json directly)."""
        base = {"eval_judge_model": "claude-opus-4-6",
                "bedrock_model_map": {
                    "claude-opus-4-8": "us.anthropic.claude-opus-4-8",
                    "claude-opus-4-6": "us.anthropic.claude-opus-4-6-v1",
                    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6"}}
        base.update(kw)
        m = patch.object(Path, "exists", return_value=True)
        r = patch.object(Path, "read_text", return_value=json.dumps(base))
        return m, r

    def test_short_name_resolves_to_full_bedrock_id(self):
        m, r = self._cfg(eval_judge_model="claude-opus-4-6")
        with m, r:
            assert _get_judge_model() == "us.anthropic.claude-opus-4-6-v1"

    def test_full_id_passes_through(self):
        m, r = self._cfg(eval_judge_model="us.anthropic.claude-opus-4-6-v1")
        with m, r:
            assert _get_judge_model() == "us.anthropic.claude-opus-4-6-v1"

    def test_null_map_falls_back_to_known_good_not_invalid_guess(self):
        # Degraded config: map is null. Must NOT synthesize the invalid
        # "us.anthropic.claude-opus-4-6" (missing -v1) — fall back to known-good.
        m, r = self._cfg(eval_judge_model="claude-opus-4-6", bedrock_model_map=None)
        with m, r:
            assert _get_judge_model() == "us.anthropic.claude-opus-4-6-v1"

    def test_unmapped_short_name_uses_safe_default(self):
        m, r = self._cfg(eval_judge_model="totally-unknown", bedrock_model_map={})
        with m, r:
            # Never an unverifiable us.anthropic.{guess}; a known-good full ID.
            assert _get_judge_model().startswith("us.anthropic.claude-")
            assert "totally-unknown" not in _get_judge_model()


class TestJudgeFailureIsErrorNotSkip:
    """Judge-infra failures must be status='error' (red), not 'skipped' (green).

    The skip→drop path is exactly how a broken judge produced a clean 100/100.
    Legit malformed-case skips (no turns/assertions) must STAY skipped.
    """

    _CASE = {"id": "X", "scenario": {"turns": [{"input": "hi"}]},
             "assertions": ["must do Y"], "evaluators": ["goal_success"], "title": "t"}

    def test_invalid_model_is_error(self):
        with patch.object(eval_runner_mod, "_get_judge_model",
                          return_value="us.anthropic.bogus-model-does-not-exist"):
            r = eval_llm_judge(self._CASE, "goal_success")
        assert r["status"] == "error", f"judge infra fail must be error, got {r['status']}"

    def test_malformed_case_still_skips(self):
        bad = {"id": "X", "scenario": {"turns": []}, "assertions": [],
               "evaluators": ["goal_success"], "title": "t"}
        r = eval_llm_judge(bad, "goal_success")
        assert r["status"] == "skipped", "malformed case is a legit skip, not error"

    def test_error_excluded_from_score_no_inflation(self):
        # 1 mechanical pass + 1 judge error must NOT yield 100% on the error.
        cases = [{"id": "A", "dimension": "d"}, {"id": "B", "dimension": "d"}]
        results = [{"id": "A", "status": "passed"}, {"id": "B", "status": "error"}]
        s = compute_scores(cases, results)
        assert s["scored_count"] == 1  # only the pass counts
        assert s["overall"] == 100.0   # of the 1 SCORED case
        # The red-light lives in cases_error surfacing, not the % — verified
        # in run_eval/get_health/briefing wiring (separate integration path).

    def test_all_error_does_not_count_as_passed(self):
        cases = [{"id": "A", "dimension": "d"}]
        results = [{"id": "A", "status": "error"}]
        s = compute_scores(cases, results)
        assert s["scored_count"] == 0


class TestTrajectoryCaptureDispatch:
    """trajectory_capture: real-behavior eval method (run_75b656c1).

    The fix for the circular judge: a case with eval_method=behavior +
    evaluators=[trajectory_capture] spawns a REAL agent (via scenario_runner),
    captures its tool-call trajectory, and delegates to the existing
    eval_trajectory matcher. The verdict is programmatic (trajectory match,
    NOT the circular LLM judge). Critical safety: it MUST NOT spawn under
    programmatic_only=True (the every-session canary path).

    NOTE: eval_trajectory_capture imports `from scripts.scenario_runner import
    run_scenario` at call time, so the patch target MUST be
    `scripts.scenario_runner.run_scenario` (not `backend.scripts....`).
    """

    _CASE = {
        "id": "GS_TRAJ_T",
        "scenario": {"prompt": "read SELF.md and summarize"},
        "expected_trajectory": ["Read SELF.md"],
        "trajectory_match": "any_order",
        "evaluators": ["trajectory_capture"],
        "eval_method": "behavior",
        "allowed_tools": ["Read"],
        "dimension": "utility",
    }

    def test_dispatch_passes_when_expected_tool_observed(self):
        # Mock the spawn to return a trajectory containing the expected Read.
        # Also asserts the observed_trajectory annotation is surfaced verbatim.
        captured = ['Read {"file_path": "/ws/.context/SELF.md"}']
        with patch("scripts.scenario_runner.run_scenario", return_value=captured):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "passed"
        assert r["evaluator"] == "trajectory_capture"
        assert r["observed_trajectory"] == captured

    def test_fails_when_expected_read_absent_negative_control(self):
        # Agent used a DIFFERENT tool (didn't read the file) -> must FAIL,
        # not pass, not skip. This is the whole point: observe real usage.
        with patch("scripts.scenario_runner.run_scenario",
                   return_value=['Grep {"pattern": "foo"}']):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "failed"

    def test_empty_trajectory_fails_not_skips(self):
        # Spawn failed / agent did nothing -> empty trajectory ([], NOT None)
        # -> FAIL (the expected Read did not happen), never a silent skip.
        with patch("scripts.scenario_runner.run_scenario", return_value=[]):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "failed", "empty trajectory must fail a Read assertion"

    def test_never_calls_llm_judge(self):
        # trajectory_capture is programmatic — must NOT fall through to the
        # circular LLM judge, even when the trajectory is empty.
        with patch("scripts.scenario_runner.run_scenario", return_value=[]), \
             patch("backend.scripts.eval_runner.eval_llm_judge") as judge:
            evaluate_case(self._CASE, Path("/tmp"))
        judge.assert_not_called()

    def test_gated_off_under_programmatic_only(self):
        # Canary path (programmatic_only=True) must NOT spawn an agent.
        with patch("scripts.scenario_runner.run_scenario") as spawn:
            r = evaluate_case(self._CASE, Path("/tmp"), programmatic_only=True)
        spawn.assert_not_called()
        assert r["status"] == "skipped"

    def test_missing_prompt_skips(self):
        bad = dict(self._CASE, scenario={})
        r = evaluate_case(bad, Path("/tmp"))
        assert r["status"] == "skipped"
