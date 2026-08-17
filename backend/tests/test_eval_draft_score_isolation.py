"""AC8 — draft cases must NEVER reach the headline score, on ANY evaluator path.

Regression guard for the score-pollution leak found by the Gate-0 skeptic
(run_1bfd3cf9): eval_trajectory_capture hard-skips tier=draft (eval_runner.py:1404),
but the LLM path (evaluate_case → eval_llm_judge) and compute_scores had NO tier
filter — so a GS_HARVEST_ (eval_method=llm, tier=draft) draft that reached run_eval
WOULD be judged and WOULD count toward the headline `overall`.

The fix moves the draft-skip to the evaluate_case ENTRY so it covers every path
(behavior + llm + programmatic) uniformly. These tests are mutation-proven: revert
the entry guard and test_llm_draft_is_skipped goes RED.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_runner import evaluate_case, compute_scores


_ROOT = Path.home() / ".swarm-ai" / "SwarmWS"


def _llm_draft_case():
    return {
        "id": "GS_HARVEST_testdraft",
        "tier": "draft",
        "eval_method": "llm",
        "evaluators": ["goal_success"],
        "scenario": {"turns": [{"input": "some prompt"}]},
        "assertions": ["agent does something reasonable"],
    }


def test_llm_draft_is_skipped_not_judged():
    """An llm+draft case must return skipped WITHOUT invoking the LLM judge.

    Mutation proof: remove the tier=draft guard at evaluate_case entry and this
    goes RED (the case would fall through to eval_llm_judge → passed/failed).
    """
    result = evaluate_case(_llm_draft_case(), _ROOT, programmatic_only=False)
    assert result["status"] == "skipped", (
        f"draft must be skipped at entry, got {result['status']} — "
        "the score-pollution leak is back"
    )


def test_draft_excluded_from_headline_score_end_to_end():
    """The two-layer defense driven through the REAL path (not a pre-set status):
    the draft's result is DERIVED from evaluate_case (it must itself emit skipped),
    then compute_scores must exclude it. This is what gives the test teeth — if the
    entry guard regresses, evaluate_case returns a scored status and this goes RED
    (draft_result.status != skipped AND scored_count becomes 2). A hardcoded
    status='skipped' would only prove the trivial 'compute_scores excludes skipped'.
    """
    draft_case = _llm_draft_case()
    # Active case is a pre-set passed result — it is NOT the thing under test; the
    # draft result below is the load-bearing one (derived from the real evaluator).
    active_case = {"id": "active1", "tier": "active", "dimension": "d"}
    draft_result = evaluate_case(draft_case, _ROOT, programmatic_only=False)
    assert draft_result["status"] == "skipped", (
        f"entry guard regressed: draft graded as {draft_result['status']}"
    )
    cases = [active_case, draft_case]
    results = [{"id": "active1", "status": "passed"}, draft_result]
    scores = compute_scores(cases, results)
    # Only the active case counts → draft neither helps nor hurts the headline.
    assert scores["scored_count"] == 1, (
        f"draft leaked into scored set: scored_count={scores['scored_count']}"
    )
    assert scores["overall"] == 100.0


def test_behavior_draft_still_skipped():
    """Regression: the pre-existing behavior-path draft skip must still hold
    (the fix generalizes it, must not remove it)."""
    case = {
        "id": "GS_behdraft",
        "tier": "draft",
        "eval_method": "behavior",
        "evaluators": ["trajectory_capture"],
        "scenario": {"prompt": "x"},
        "expected_trajectory": ["Read AGENT.md"],
    }
    result = evaluate_case(case, _ROOT, programmatic_only=False)
    assert result["status"] == "skipped"
