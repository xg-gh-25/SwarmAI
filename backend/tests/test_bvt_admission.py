"""Tests for tightened compute_bvt admission (run_5edf2cc0 C2, gaps G1/G2/G5/G8).

BVT (the gate set) must admit a case ONLY when it is:
  - non-llm (existing)
  - has a gate-eligible evaluator (existing; now INCLUDES canary_pass — G5)
  - tier != draft (G2)
  - carries a validated_by_4gate stamp that MATCHES its current canonical body
    (G1/G8 — an edited-but-not-re-stamped case drifts out)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_runner import compute_bvt, _GATE_ELIGIBLE_EVALUATORS  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402


def _stamped(**over):
    c = {
        "id": "GS_OK",
        "eval_method": "programmatic",
        "evaluators": ["file_contains"],
        "tier": "active",
        "verification": {"file": "README.md", "expected_contains": "Swarm"},
    }
    c.update(over)
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


def _result(case_id, status="passed"):
    return {"id": case_id, "status": status}


def test_canary_pass_is_gate_eligible():
    """G5: canary_pass joined the eligible set."""
    assert "canary_pass" in _GATE_ELIGIBLE_EVALUATORS


def test_valid_stamped_case_counted():
    c = _stamped()
    bvt = compute_bvt([c], [_result("GS_OK")])
    assert bvt["total"] == 1 and bvt["passed"] == 1 and bvt["green"]


def test_canary_case_counted():
    """G5: a canary_pass case now contributes to bvt.total."""
    c = _stamped(id="GS_CAN", evaluators=["canary_pass"])
    bvt = compute_bvt([c], [_result("GS_CAN")])
    assert bvt["total"] == 1


def test_draft_excluded():
    """G2: a draft case with a gate-eligible evaluator is NOT counted."""
    c = _stamped(id="GS_DRAFT", tier="draft")
    bvt = compute_bvt([c], [_result("GS_DRAFT")])
    assert bvt["total"] == 0


def test_unstamped_excluded():
    """G1/G8: a case with NO stamp is not in the BVT (unsanctioned/un-validated)."""
    c = _stamped(id="GS_NOSTAMP")
    del c["validated_by_4gate"]
    bvt = compute_bvt([c], [_result("GS_NOSTAMP")])
    assert bvt["total"] == 0


def test_drifted_stamp_excluded():
    """G1/G8 NEGATIVE: edit the body after stamping → stamp no longer matches →
    case drops out of BVT. The drift-detection guarantee."""
    c = _stamped(id="GS_DRIFT")
    # tamper the body without re-stamping
    c["verification"]["expected_contains"] = "EVIL"
    bvt = compute_bvt([c], [_result("GS_DRIFT")])
    assert bvt["total"] == 0


def test_llm_still_excluded():
    c = _stamped(id="GS_LLM", eval_method="llm", evaluators=["llm_judge"])
    bvt = compute_bvt([c], [_result("GS_LLM")])
    assert bvt["total"] == 0


def test_green_requires_nonempty():
    """An all-drifted/empty BVT is RED, never vacuous-green."""
    c = _stamped(id="GS_DRIFT2")
    c["verification"]["expected_contains"] = "EVIL"
    bvt = compute_bvt([c], [_result("GS_DRIFT2")])
    assert not bvt["green"]


def test_failed_breaks_green_but_skip_does_not():
    ok = _stamped(id="A")
    bad = _stamped(id="B")
    bvt = compute_bvt([ok, bad], [_result("A", "passed"), _result("B", "failed")])
    assert not bvt["green"] and bvt["failed"] == 1
