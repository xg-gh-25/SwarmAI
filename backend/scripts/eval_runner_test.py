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
    eval_canary_pass,
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
        return (root / "Eval" / "golden_set.yaml").exists()
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
        gs_path = root / "Eval" / "golden_set.yaml"
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

    @pytest.mark.skipif(not _workspace_available(), reason="needs live Eval/golden_set.yaml")
    def test_workspace_found(self):
        root = _find_workspace_root()
        assert (root / "Eval" / "golden_set.yaml").exists()

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


class TestCanaryTeeth:
    """verify_teeth: after a canary's positive passes, execute the
    negative_command and require it to AFFIRMATIVELY emit its FAIL token
    (negative_expected_contains) AND omit the positive marker. Teeth are
    OPT-IN (only fire when negative_expected_contains is declared) — so cases
    using the opposite convention (eval_spine_probe prints _OK on a successful
    negative) are NOT runtime-executed. OFF by default at the param level too."""

    def _case(self, command, expected, negative=None, neg_expected=None):
        c = {
            "id": "GS_TEETH_TEST",
            "category": "compliance",
            "dimension": "compliance",
            "evaluators": ["canary_pass"],
            "affected_by": ["backend/scripts/eval_runner.py"],
            "verification": {"command": command, "expected_contains": expected},
        }
        if negative is not None:
            c["verification"]["negative_command"] = negative
        if neg_expected is not None:
            c["verification"]["negative_expected_contains"] = neg_expected
        return c

    def test_teeth_off_by_default_ignores_negative(self):
        """back-compat: verify_teeth defaults False — the negative is never run,
        even when fully declared. Behaves exactly as today."""
        repo = _find_swarmai_repo()
        case = self._case("echo M_OK", "M_OK", negative="echo M_OK", neg_expected="M_FAIL")
        result = eval_canary_pass(case, repo)  # default verify_teeth=False
        assert result["status"] == "passed"

    def test_vacuous_negative_still_prints_marker_is_RED(self):
        """AC1: positive passes, but the negative STILL prints the positive
        marker => vacuous probe => FAIL (even though FAIL token would match)."""
        repo = _find_swarmai_repo()
        # negative echoes BOTH the positive marker and the fail token
        case = self._case("echo M_OK", "M_OK", negative="echo 'M_OK M_FAIL'", neg_expected="M_FAIL")
        result = eval_canary_pass(case, repo, verify_teeth=True)
        assert result["status"] == "failed", result
        assert "vacuous" in result["notes"].lower()

    def test_real_negative_emits_fail_token_is_GREEN(self):
        """AC2: positive passes AND negative emits its FAIL token AND omits the
        positive marker (it discriminates) => passed."""
        repo = _find_swarmai_repo()
        case = self._case("echo M_OK", "M_OK", negative="echo M_FAIL", neg_expected="M_FAIL")
        result = eval_canary_pass(case, repo, verify_teeth=True)
        assert result["status"] == "passed", result

    def test_typo_or_noop_negative_is_RED(self):
        """AC5/HIGH-1: a negative that never ran the wire (typo => exit 127, or
        a no-op like `true`) emits NO fail token => NO TEETH => FAIL.
        'marker merely absent' must NOT pass."""
        repo = _find_swarmai_repo()
        for bad_neg in ("this_is_a_typo_cmd_xyz", "true"):
            case = self._case("echo M_OK", "M_OK", negative=bad_neg, neg_expected="M_FAIL")
            result = eval_canary_pass(case, repo, verify_teeth=True)
            assert result["status"] == "failed", (bad_neg, result)
            assert "no teeth" in result["notes"].lower(), (bad_neg, result)

    def test_not_opted_in_skips_teeth(self):
        """CRITICAL-1: a case with negative_command but NO
        negative_expected_contains is NOT runtime-executed (spine-probe
        convention is left alone). Even a vacuous negative passes."""
        repo = _find_swarmai_repo()
        case = self._case("echo M_OK", "M_OK", negative="echo M_OK")  # no neg_expected
        result = eval_canary_pass(case, repo, verify_teeth=True)
        assert result["status"] == "passed", result

    def test_fail_token_in_stderr_only_is_RED(self):
        """MEDIUM-1: match on stdout only. A fail token that appears solely in
        stderr (e.g. command echoed in an error) does NOT count as emitted."""
        repo = _find_swarmai_repo()
        # 'M_FAIL' only reaches stderr via the not-found command name echo
        case = self._case("echo M_OK", "M_OK", negative="M_FAIL_not_a_real_cmd", neg_expected="M_FAIL")
        result = eval_canary_pass(case, repo, verify_teeth=True)
        assert result["status"] == "failed", result
        assert "no teeth" in result["notes"].lower(), result

    def test_teeth_threaded_through_run_eval(self):
        """AC3: verify_teeth threads run_eval -> evaluate_case -> eval_canary_pass.
        A vacuous opted-in case fails ONLY when run_eval verify_teeth=True."""
        from backend.scripts.eval_runner import run_eval
        repo = _find_swarmai_repo()
        gs = {"cases": [self._case("echo M_OK", "M_OK",
                                    negative="echo 'M_OK M_FAIL'", neg_expected="M_FAIL")]}
        r_off = run_eval(gs, "manual", None, repo)
        assert r_off["cases"][0]["status"] == "passed"
        r_on = run_eval(gs, "manual", None, repo, verify_teeth=True)
        assert r_on["cases"][0]["status"] == "failed", r_on["cases"][0]


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
        hist_dir = root / "Eval" / "EvalHistory"
        json_files = list(hist_dir.glob("*.json"))
        assert len(json_files) > 0, "No eval history files found"

    @pytest.mark.skipif(not _workspace_available(), reason="Live workspace not available (CI)")
    def test_output_schema_valid(self):
        root = _find_workspace_root()
        hist_dir = root / "Eval" / "EvalHistory"
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

    def test_unmapped_short_name_WARNS_loudly(self, caplog):
        """The substitution above is correct — but it must not be SILENT.

        Teeth: deleting the logger.warning in _get_judge_model turns this RED.
        The sibling test above passes either way (it only checks the returned
        id), which is exactly how the defect survived: a live config pinned an
        unresolvable judge model and a DIFFERENT model ran the evaluation, with
        nothing in the logs to reveal it. The warning must name both the
        offending value and the substitute so the log is actionable.
        """
        import logging
        m, r = self._cfg(eval_judge_model="totally-unknown", bedrock_model_map={})
        with caplog.at_level(logging.WARNING), m, r:
            resolved = _get_judge_model()
        assert "totally-unknown" in caplog.text, (
            "the unresolvable judge model must be NAMED in a warning — a silent "
            "substitution is the defect this guards"
        )
        assert resolved in caplog.text, "the substituted id must be named too"

    def test_resolvable_judge_model_does_NOT_warn(self, caplog):
        """No warning noise on the normal path (a warn-always log is ignored)."""
        import logging
        m, r = self._cfg(eval_judge_model="claude-sonnet-4-6")
        with caplog.at_level(logging.WARNING), m, r:
            assert _get_judge_model() == "us.anthropic.claude-sonnet-4-6"
        assert "not resolvable" not in caplog.text

    def test_never_raises_on_a_bad_config(self):
        """One typo must not red the entire eval run — log, substitute, continue."""
        m = patch.object(Path, "exists", return_value=True)
        r = patch.object(Path, "read_text", side_effect=OSError("unreadable"))
        with m, r:
            assert _get_judge_model().startswith("us.anthropic.claude-")

    def test_resolves_via_the_registry_not_a_private_table(self):
        """Every registry model must resolve even with an EMPTY config map.

        Teeth: re-adding a private 3-entry _KNOWN_GOOD dict turns this RED for
        the flagship, which such a table would not contain.
        """
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
        from model_registry import MODEL_NAMES, MODEL_REGISTRY
        for short_name in MODEL_NAMES:
            m, r = self._cfg(eval_judge_model=short_name, bedrock_model_map={})
            with m, r:
                assert _get_judge_model() == MODEL_REGISTRY[short_name], (
                    f"{short_name} must resolve through the registry"
                )


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
        # Mock the spawn to return (trajectory, final_text). Trajectory contains
        # the expected Read. Asserts observed_trajectory is surfaced verbatim.
        captured = ['Read {"file_path": "/ws/.context/SELF.md"}']
        with patch("scripts.scenario_runner.run_scenario_full", return_value=(captured, "summary")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "passed"
        assert r["evaluator"] == "trajectory_capture"
        assert r["observed_trajectory"] == captured

    def test_fails_when_expected_read_absent_negative_control(self):
        # Agent used a DIFFERENT tool (didn't read the file) -> must FAIL,
        # not pass, not skip. This is the whole point: observe real usage.
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=(['Grep {"pattern": "foo"}'], "")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "failed"

    def test_empty_trajectory_fails_not_skips(self):
        # Spawn failed / agent did nothing -> empty trajectory ([], NOT None)
        # -> FAIL (the expected Read did not happen), never a silent skip.
        with patch("scripts.scenario_runner.run_scenario_full", return_value=([], "")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "failed", "empty trajectory must fail a Read assertion"

    def test_never_calls_llm_judge(self):
        # trajectory_capture is programmatic — must NOT fall through to the
        # circular LLM judge, even when the trajectory is empty.
        with patch("scripts.scenario_runner.run_scenario_full", return_value=([], "")), \
             patch("backend.scripts.eval_runner.eval_llm_judge") as judge:
            evaluate_case(self._CASE, Path("/tmp"))
        judge.assert_not_called()

    def test_gated_off_under_programmatic_only(self):
        # Canary path (programmatic_only=True) must NOT spawn an agent.
        with patch("scripts.scenario_runner.run_scenario_full") as spawn:
            r = evaluate_case(self._CASE, Path("/tmp"), programmatic_only=True)
        spawn.assert_not_called()
        assert r["status"] == "skipped"

    def test_missing_prompt_skips(self):
        bad = dict(self._CASE, scenario={})
        r = evaluate_case(bad, Path("/tmp"))
        assert r["status"] == "skipped"

    # ── Decision-class gate: read must DRIVE the conclusion (Gate-2 MED) ──

    # ── Decision-class gate: the read must DRIVE the decision, judged on the
    #    REAL final answer by the LLM judge (stance detection = judgment, not
    #    substring). We mock the judge to assert the WIRING, not the model. ──

    _DECISION_CASE = {
        "id": "GS_TRAJ_DEC",
        "scenario": {"prompt": "decide X; read IMPROVEMENT.md and let it drive you"},
        "expected_trajectory": ["Read IMPROVEMENT.md"],
        "decision_rubric": "PASS only if the final recommendation avoids big-bang and uses incremental.",
        "trajectory_match": "any_order",
        "evaluators": ["trajectory_capture"],
        "eval_method": "behavior",
        "allowed_tools": ["Read"],
    }

    def test_decision_judge_pass_when_direction_correct(self):
        # Trajectory passes AND the decision judge says the stance is correct.
        traj = ['Read {"file_path": "/ws/Projects/SwarmAI/IMPROVEMENT.md"}']
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=(traj, "NO to big-bang — use strangler-fig.")), \
             patch("backend.scripts.eval_runner._judge_decision_direction",
                   return_value={"status": "passed", "notes": "recommended incremental"}):
            r = evaluate_case(self._DECISION_CASE, Path("/tmp"))
        assert r["status"] == "passed"

    def test_decision_judge_fail_when_direction_wrong(self):
        # Read happened, but the judge finds the stance went the WRONG way ->
        # FAIL. This is the case substring matching could not reliably detect.
        traj = ['Read {"file_path": "/ws/Projects/SwarmAI/IMPROVEMENT.md"}']
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=(traj, "Names incremental, but: do the big-bang rewrite.")), \
             patch("backend.scripts.eval_runner._judge_decision_direction",
                   return_value={"status": "failed", "notes": "recommended big-bang"}):
            r = evaluate_case(self._DECISION_CASE, Path("/tmp"))
        assert r["status"] == "failed"
        assert "wrong way" in r["notes"].lower()

    def test_decision_judge_infra_failure_is_error(self):
        # Decision judge itself failing (throttle/auth) -> error, not failed:
        # a trajectory that passed must not be scored a behavior FAIL because the
        # judge was down (would lie the score red).
        traj = ['Read {"file_path": "/ws/Projects/SwarmAI/IMPROVEMENT.md"}']
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=(traj, "some answer")), \
             patch("backend.scripts.eval_runner._judge_decision_direction",
                   return_value={"status": "error", "notes": "judge throttled"}):
            r = evaluate_case(self._DECISION_CASE, Path("/tmp"))
        assert r["status"] == "error"

    def test_decision_no_read_fails_before_judge(self):
        # No Read at all -> fails on trajectory; decision judge never invoked.
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=([], "use the incremental approach")), \
             patch("backend.scripts.eval_runner._judge_decision_direction") as dj:
            r = evaluate_case(self._DECISION_CASE, Path("/tmp"))
        assert r["status"] == "failed"
        dj.assert_not_called()

    def test_decision_rubric_without_trajectory_is_error(self):
        # decision_rubric but no expected_trajectory -> would silently never run
        # -> loud misconfiguration error, not skip (Gate-2 V4).
        bad = {"id": "X", "scenario": {"prompt": "decide"},
               "decision_rubric": "PASS if incremental",
               "evaluators": ["trajectory_capture"], "eval_method": "behavior",
               "dimension": "utility"}
        r = evaluate_case(bad, Path("/tmp"))
        assert r["status"] == "error"

    def test_infra_failure_is_error_not_failed(self):
        # Spawn infra failure (CLI/timeout/throttle) -> error, NOT failed, so a
        # transient outage can't lie the health score red (Gate-2 HIGH).
        # Import via the SAME module path eval_trajectory_capture binds
        # (scripts.scenario_runner) so the raised class identity matches the
        # `except ScenarioInfraError` (avoids the dual-load identity trap).
        import scripts.scenario_runner as sr
        with patch("scripts.scenario_runner.run_scenario_full",
                   side_effect=sr.ScenarioInfraError("claude CLI not found")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "error"

    def test_grep_with_read_token_does_not_falsely_pass(self):
        # Gate-2 MED: a Grep whose pattern contains "read" must NOT satisfy a
        # "Read SELF.md" assertion — the agent never opened the file.
        fake = ['Grep {"pattern": "read", "path": "/ws/.context/SELF.md"}']
        with patch("scripts.scenario_runner.run_scenario_full", return_value=(fake, "")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "failed", "Grep != Read — must not false-pass"

    def test_real_read_passes_under_tool_strict(self):
        real = ['Read {"file_path": "/ws/.context/SELF.md"}']
        with patch("scripts.scenario_runner.run_scenario_full", return_value=(real, "ok")):
            r = evaluate_case(self._CASE, Path("/tmp"))
        assert r["status"] == "passed"

    def test_bash_tool_stripped_to_readonly(self):
        # Gate-2 MED: a behavior case requesting Bash must be down-scoped to
        # read-only before spawn (no arbitrary shell under bypassPermissions).
        case = dict(self._CASE, allowed_tools=["Read", "Bash", "Write"])
        captured = {}

        def _spy(prompt, allowed_tools=None, timeout=120):
            captured["tools"] = list(allowed_tools or [])
            return (['Read {"file_path": "SELF.md"}'], "ok")

        with patch("scripts.scenario_runner.run_scenario_full", _spy):
            evaluate_case(case, Path("/tmp"))
        assert "Bash" not in captured["tools"]
        assert "Write" not in captured["tools"]
        assert "Read" in captured["tools"]


class TestBehaviorCaseDefaultGating:
    """Behavior cases must NOT run on a default/manual sweep (Gate-2 HIGH:
    surprise 4-agent cost/flake bomb). Only with explicit behavior_trajectory
    tag or case_filter."""

    _BEHAVIOR = {"id": "GS_TRAJ_X", "eval_method": "behavior",
                 "evaluators": ["trajectory_capture"], "dimension": "utility",
                 "scenario": {"prompt": "read X"}, "expected_trajectory": ["Read X"]}
    _NORMAL = {"id": "GS_NORM", "eval_method": "programmatic",
               "evaluators": ["keyword_match"], "dimension": "recall",
               "expected_response_contains": ["x"]}

    def test_default_run_excludes_behavior_cases(self):
        from backend.scripts.eval_runner import run_eval
        gs = {"cases": [self._BEHAVIOR, self._NORMAL]}
        with patch("scripts.scenario_runner.run_scenario_full") as spawn:
            r = run_eval(gs, "manual", None, Path("/tmp"))
        spawn.assert_not_called()
        ran_ids = {c["id"] for c in r["cases"]}
        assert "GS_TRAJ_X" not in ran_ids
        assert "GS_NORM" in ran_ids

    def test_explicit_tag_includes_behavior_cases(self):
        from backend.scripts.eval_runner import run_eval
        b = dict(self._BEHAVIOR, tags=["behavior_trajectory"])
        gs = {"cases": [b]}
        with patch("scripts.scenario_runner.run_scenario_full", return_value=(['Read X'], "ok")) as spawn:
            r = run_eval(gs, "manual", None, Path("/tmp"), tags=["behavior_trajectory"])
        spawn.assert_called_once()
        assert "GS_TRAJ_X" in {c["id"] for c in r["cases"]}

    def test_explicit_case_filter_includes_behavior_case(self):
        from backend.scripts.eval_runner import run_eval
        gs = {"cases": [self._BEHAVIOR]}
        with patch("scripts.scenario_runner.run_scenario_full", return_value=(['Read X'], "ok")) as spawn:
            r = run_eval(gs, "manual", ["GS_TRAJ_X"], Path("/tmp"))
        spawn.assert_called_once()

    def test_include_behavior_true_runs_behavior(self):
        # run_0e29db9a follow-on: the biweekly/manual full sweep opts IN via
        # include_behavior=True (M3 safe frame — default stays False, tripwire
        # above). Mutation: removing `or include_behavior` from the gate flips
        # this RED.
        from backend.scripts.eval_runner import run_eval
        gs = {"cases": [self._BEHAVIOR, self._NORMAL]}
        with patch("scripts.scenario_runner.run_scenario_full",
                   return_value=(['Read X'], "ok")) as spawn:
            r = run_eval(gs, "scheduled", None, Path("/tmp"), include_behavior=True)
        spawn.assert_called_once()
        ran_ids = {c["id"] for c in r["cases"]}
        assert "GS_TRAJ_X" in ran_ids and "GS_NORM" in ran_ids

    def test_include_behavior_false_still_excludes(self):
        # Explicit default-False is identical to the implicit default — canary /
        # hook / _execute_run paths (which never pass include_behavior) stay safe.
        from backend.scripts.eval_runner import run_eval
        gs = {"cases": [self._BEHAVIOR, self._NORMAL]}
        with patch("scripts.scenario_runner.run_scenario_full") as spawn:
            r = run_eval(gs, "manual", None, Path("/tmp"), include_behavior=False)
        spawn.assert_not_called()
        assert "GS_TRAJ_X" not in {c["id"] for c in r["cases"]}

    def test_run_result_cases_carry_eval_method(self):
        # Gate-1 E fix: behavior-red segregation needs eval_method in the per-case
        # result dict (was absent → handler couldn't tell behavior from
        # deterministic failures).
        from backend.scripts.eval_runner import run_eval
        gs = {"cases": [self._NORMAL]}
        r = run_eval(gs, "manual", None, Path("/tmp"))
        assert r["cases"], "expected at least one case result"
        assert all("eval_method" in c for c in r["cases"]), \
            "run_result cases must carry eval_method for behavior-red segregation"


class TestDecisionJudgeUnit:
    """_judge_decision_direction: judges the agent's REAL answer for stance,
    replacing brittle substring/negation matching (stance = judgment). We mock
    the Bedrock client to assert parse/verdict wiring, not the model."""

    _CASE = {"id": "X", "decision_rubric": "PASS if incremental, FAIL if big-bang"}

    def _mock_response(self, verdict, notes="x", conf=0.9):
        # _judge_decision_direction calls jobs.bedrock.converse_with_retry, which
        # returns the response DICT directly (not a client). Mock must match that
        # shape, and the patch target must be the source symbol the call-time
        # `from jobs.bedrock import converse_with_retry` (eval_runner.py:1163) resolves.
        return {"output": {"message": {"content": [
            {"text": json.dumps({"verdict": verdict, "confidence": conf, "notes": notes})}
        ]}}}

    def test_passed_verdict(self):
        from backend.scripts import eval_runner as er
        with patch.object(er, "_get_judge_model", return_value="us.anthropic.x"), \
             patch("jobs.bedrock.converse_with_retry", return_value=self._mock_response("passed")):
            r = er._judge_decision_direction(self._CASE, "use strangler-fig, not big-bang")
        assert r["status"] == "passed"

    def test_failed_verdict(self):
        from backend.scripts import eval_runner as er
        with patch.object(er, "_get_judge_model", return_value="us.anthropic.x"), \
             patch("jobs.bedrock.converse_with_retry", return_value=self._mock_response("failed")):
            r = er._judge_decision_direction(self._CASE, "do the big-bang rewrite")
        assert r["status"] == "failed"

    def test_empty_answer_skips(self):
        from backend.scripts import eval_runner as er
        r = er._judge_decision_direction(self._CASE, "")
        assert r["status"] == "skipped"

    def test_no_rubric_skips(self):
        from backend.scripts import eval_runner as er
        r = er._judge_decision_direction({"id": "X"}, "some answer")
        assert r["status"] == "skipped"


class TestBehaviorSpawnTimeoutFallback:
    """The operative per-case spawn timeout fallback (eval_trajectory_capture)
    must give cold real-agent spawns headroom over their observed 82-95s
    cold-start latency — a case without scenario_timeout runs at 240, not 120.
    (run_e6921209: two DDD-read cases false-errored at the old 120s cap.)"""

    _CASE = {
        "id": "GS_TRAJ_TO", "eval_method": "behavior",
        "evaluators": ["trajectory_capture"], "dimension": "utility",
        "scenario": {"prompt": "read X"}, "expected_trajectory": ["Read X"],
        "allowed_tools": ["Read"],
    }

    def test_default_fallback_timeout_is_240(self):
        # A behavior case that does NOT set scenario_timeout must be spawned
        # with timeout=240 (the raised fallback), captured at the real call.
        captured = {}

        def fake_spawn(prompt, allowed_tools=None, timeout=None):
            captured["timeout"] = timeout
            return (['Read {"file_path": "/ws/X"}'], "done")

        with patch("scripts.scenario_runner.run_scenario_full", side_effect=fake_spawn):
            eval_runner_mod.eval_trajectory_capture(self._CASE)
        assert captured["timeout"] == 240

    def test_explicit_scenario_timeout_still_honored(self):
        # An explicit per-case scenario_timeout overrides the fallback.
        captured = {}

        def fake_spawn(prompt, allowed_tools=None, timeout=None):
            captured["timeout"] = timeout
            return (['Read {"file_path": "/ws/X"}'], "done")

        case = dict(self._CASE, scenario_timeout=90)
        with patch("scripts.scenario_runner.run_scenario_full", side_effect=fake_spawn):
            eval_runner_mod.eval_trajectory_capture(case)
        assert captured["timeout"] == 90


class TestJudgeConfidenceCoercion:
    """Both LLM judges format confidence with :.2f. A judge that returns
    confidence as a STRING ("0.92") or a non-numeric value must NOT crash into
    a swallowed 'error' (run_e6921209 / 2026-07-01: 'Unknown format code f for
    object of type str' at GS_TRAJ_DECISION_NEGATIVE_CONTROL)."""

    def test_coerce_conf_helper(self):
        from backend.scripts import eval_runner as er
        assert er._coerce_conf(0.92) == 0.92
        assert er._coerce_conf("0.92") == 0.92      # str numeric → float
        assert er._coerce_conf("high") == 0.0        # non-numeric → 0.0 fallback
        assert er._coerce_conf(None) == 0.0          # None → 0.0
        assert er._coerce_conf({"x": 1}) == 0.0      # wrong type → 0.0

    def test_decision_judge_str_confidence_no_crash(self):
        # The decision judge returns confidence as a string — must format
        # cleanly (status passed/failed), NOT swallow into 'error'.
        # converse_with_retry is `from jobs.bedrock import` inside the function;
        # patch it at its source module + return the real nested response shape.
        from backend.scripts import eval_runner as er
        case = {"id": "GS_D", "decision_rubric": "PASS if incremental"}
        verdict_json = '{"verdict":"passed","confidence":"0.92","notes":"recommended incremental"}'
        fake_response = {"output": {"message": {"content": [{"text": verdict_json}]}}}
        with patch("jobs.bedrock.converse_with_retry", return_value=fake_response):
            r = er._judge_decision_direction(case, "use incremental strangler-fig")
        assert r["status"] == "passed", f"got {r}"
        assert r["status"] != "error"
        assert "0.92" in r["notes"]

    def test_goal_quality_judge_str_confidence_no_crash(self):
        # Gate-2 LOW#1: SITE-1 (eval_llm_judge goal/quality judge) is the OTHER
        # :.2f-on-confidence site — symmetric coverage so a future regression
        # reverting site-1 to raw confidence is caught, not just the helper unit.
        from backend.scripts import eval_runner as er
        case = {"id": "GS_Q", "scenario": {"turns": [{"input": "do X"}]},
                "assertions": ["did X"], "title": "quality case"}
        verdict_json = '{"verdict":"passed","confidence":"0.88","notes":"looks compliant"}'
        fake_response = {"output": {"message": {"content": [{"text": verdict_json}]}}}
        with patch("jobs.bedrock.converse_with_retry", return_value=fake_response):
            r = er.eval_llm_judge(case, "goal_success")
        assert r["status"] != "error", f"got {r}"
        assert "0.88" in r["notes"]
