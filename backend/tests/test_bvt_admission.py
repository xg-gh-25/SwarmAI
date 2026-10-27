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

from scripts.eval_runner import compute_bvt, _GATE_ELIGIBLE_EVALUATORS, LIVE_TIER  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402


def _stamped(**over):
    c = {
        "id": "GS_OK",
        "eval_method": "programmatic",
        "evaluators": ["file_contains"],
        "tier": "active",
        # A case is in the BVT only at release tier gate (core/eval_release.tier_of;
        # a case with no ``gating`` is watch: shown, never gating).
        "gating": "gate",
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


def test_archived_excluded():
    """delete-semantics consistency: a soft-deleted (tier='archived') case must
    NOT count in the BVT — every other golden-set reader (eval_service get_golden_set
    :386, active_cases :560) already excludes archived; compute_bvt was the one gate
    path that didn't, so a soft-deleted gate-eligible+stamped case leaked into the
    gate (run_5edf2cc0 follow-up; empirically counted=True before this fix)."""
    c = _stamped(id="GS_ARCH", tier="archived")
    bvt = compute_bvt([c], [_result("GS_ARCH")])
    assert bvt["total"] == 0


def test_stable_counted():
    """A 'stable' case (promote() sets tier='stable' for proven cases; get_golden_set
    returns them as active gate members) MUST count in the BVT. This pins the allowlist
    to include the proven tier — a regression where someone tightens the gate and
    forgets 'stable' would silently drop every promoted case out of the gate."""
    c = _stamped(id="GS_STABLE", tier="stable")
    bvt = compute_bvt([c], [_result("GS_STABLE")])
    assert bvt["total"] == 1 and bvt["passed"] == 1 and bvt["green"]


def test_unknown_tier_fail_closed():
    """Fail-closed allowlist guarantee: a tier the gate does NOT explicitly trust
    (e.g. a future 'canary'/'experimental' tier wired to a gate-eligible evaluator)
    must NOT silently enter the gate. The OLD denylist (tier not in draft,archived)
    would have admitted it; the allowlist (tier in active,stable) excludes anything
    unknown. New tiers must be added to _GATE_TIERS deliberately, never by default."""
    c = _stamped(id="GS_CANARY", tier="canary")
    bvt = compute_bvt([c], [_result("GS_CANARY")])
    assert bvt["total"] == 0


def test_live_tier_excluded_from_gate():
    """The 'live' tier marks a deterministic case that depends on
    runtime state (a built code_intel.db / live workspace) rather than being
    self-verifiable in a bare git checkout — e.g. GS_COD010. Such a case must stay
    in the corpus (get_golden_set returns it, so it runs post-deploy/manually) but
    must NOT enter the BVT release gate: a transient runtime-state miss (index
    rebuild window) would otherwise flap the gate red. LIVE_TIER is NOT in
    _GATE_TIERS, so compute_bvt excludes it — proven here with a FAILING live case
    that must not count toward (or redden) the gate."""
    live = _stamped(id="GS_LIVE", tier=LIVE_TIER)
    active = _stamped(id="GS_ACTIVE")
    # live case FAILS, active case passes
    bvt = compute_bvt([live, active], [_result("GS_LIVE", "failed"), _result("GS_ACTIVE")])
    assert bvt["total"] == 1, "only the active case is gated; live is excluded"
    assert bvt["passed"] == 1 and bvt["green"], "a failing live case must NOT redden the gate"


def test_missing_tier_defaults_to_counted():
    """Behavior-preservation: a case with NO tier field counted under the old
    denylist (None not in (draft,archived)). The allowlist must preserve this via
    the .get('tier','active') default (mirrors get_golden_set:254) — a tier-less
    legacy case stays in the gate, it does NOT fail-closed out."""
    c = _stamped(id="GS_NOTIER")
    del c["tier"]
    c["validated_by_4gate"] = compute_case_stamp(c)  # re-stamp after dropping tier
    bvt = compute_bvt([c], [_result("GS_NOTIER")])
    assert bvt["total"] == 1 and bvt["green"]


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


def test_watch_case_is_not_counted():
    """A case without gating (a new case starts at watch) is shown, never part of the BVT."""
    c = _stamped(id="GS_WATCH")
    c.pop("gating")
    c["validated_by_4gate"] = compute_case_stamp(c)
    bvt = compute_bvt([c], [_result("GS_WATCH")])
    assert bvt["total"] == 0 and not bvt["green"]
