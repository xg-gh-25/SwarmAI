"""AC1/AC2/AC3 — the teeth gate (gate_by_knockout).

The命门 of option-D: an auto-generated case may enter the golden set ONLY if it
has TEETH — proven by a knockout self-test. The gate feeds the case's own
`negative_example` (an answer that SHOULD be judged FAIL) to the pinned judge:

  - judge FAILs the negative  → the case discriminates → gate PASS (admit)
  - judge PASSes the negative  → tautology (any answer passes) → gate FAIL (discard)
  - judge ERRORs               → verdict unusable → gate FAIL (fail-closed, never
                                 admit an unverified case)

Anti-self-deception: the judge is the pinned judge (different stance from the
generator), NOT the generator itself. These tests mock the judge boundary to
prove the gate's ROUTING logic; a live-Bedrock knockout is the BUILD smoke.

Mutation proof (AC3): invert the admit/discard branch and test_teeth_admits /
test_tautology_discarded swap verdicts → RED.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import eval_service as es


def _behavior_case_with_negative():
    """A behavior/decision case + a negative_example that a TEETHED rubric fails."""
    return {
        "id": "GS_gen_behavior",
        "eval_method": "behavior",
        "decision_rubric": "PASS only if the final recommendation AVOIDS a big-bang rewrite.",
        "negative_example": "Yes, do the big-bang rewrite now — rip it all out and start fresh.",
        "evaluators": ["trajectory_capture"],
    }


def _llm_case_with_negative():
    return {
        "id": "GS_gen_llm",
        "eval_method": "llm",
        "assertions": ["The answer states the daemon port is 18321."],
        "negative_example": "The daemon runs on port 8080.",
        "evaluators": ["goal_success"],
        "scenario": {"turns": [{"input": "what port?"}]},
    }


def test_teeth_admits_when_judge_fails_negative(monkeypatch):
    """AC2: negative correctly FAILED by judge → case has teeth → gate PASS."""
    monkeypatch.setattr(es, "_knockout_judge",
                        lambda case, neg: {"status": "failed", "notes": "correctly rejected"})
    ok, reason = es.gate_by_knockout(_behavior_case_with_negative())
    assert ok is True, f"a teethed case must be admitted, got fail: {reason}"


def test_tautology_discarded_when_judge_passes_negative(monkeypatch):
    """AC1: negative also PASSES → tautology → gate FAIL (discard)."""
    monkeypatch.setattr(es, "_knockout_judge",
                        lambda case, neg: {"status": "passed", "notes": "negative also passed"})
    ok, reason = es.gate_by_knockout(_llm_case_with_negative())
    assert ok is False, "a tautology (negative passes) must be discarded"
    assert "tautolog" in reason.lower() or "negative" in reason.lower()


def test_judge_error_is_failclosed(monkeypatch):
    """Judge infra error → gate FAIL (never admit an unverified case)."""
    monkeypatch.setattr(es, "_knockout_judge",
                        lambda case, neg: {"status": "error", "notes": "bedrock down"})
    ok, reason = es.gate_by_knockout(_llm_case_with_negative())
    assert ok is False, "judge error must fail-closed, not admit"


def test_missing_negative_is_failclosed():
    """A generated case with NO negative_example cannot be knockout-tested →
    fail-closed (the generator must produce one; absence is not a free pass)."""
    case = {"id": "GS_x", "eval_method": "llm", "assertions": ["x"]}
    ok, reason = es.gate_by_knockout(case)
    assert ok is False
    assert "negative" in reason.lower()
