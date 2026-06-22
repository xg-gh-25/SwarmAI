"""Tests for Evolution Pipeline v3 Phase 2: escalation_ladder.

decide_escalation(class_state) is a PURE TOTAL function — every (count, active_gate)
combination returns a defined EscalationDecision. Phase 2 scope (re-scoped after Gate 1
BLOCK): only the REACHABLE rungs.

  count < 3                          -> kind="none"  (log-only)
  count >= 3 AND no existing fix     -> kind="rule"  (propose L1 rule + Intake brief)
  count >= 3 AND a structural fix
                already exists (active_gate set)  -> kind="none" (do NOT re-propose;
                                                     the gate/rule-failed escalation is
                                                     Phase 3, where the dashboard wires
                                                     the register caller)

DoD negative tests (Gate-1 driven):
  AC1: a class with NO existing fix never returns kind="gate" (gate rung is Phase 3).
  AC2: count<3 returns kind="none", no proposal.
  Truth-table: canonical CLASS_A seed (count=11, active_gate=GC12) -> kind="none"
               (a fix exists; re-proposing a rule would be the "4th rule" anti-pattern).
"""

from __future__ import annotations

import pytest

from core.evolution.escalation_ladder import EscalationDecision, decide_escalation


def _state(count=0, active_gate=None, post_gate_count=0, resolved=False):
    return {
        "count": count,
        "active_gate": active_gate,
        "post_gate_count": post_gate_count,
        "resolved": resolved,
    }


# --- AC2 (NEGATIVE): count < 3 -> none ---

@pytest.mark.parametrize("count", [0, 1, 2])
def test_ac2_below_threshold_is_none(count):
    d = decide_escalation(_state(count=count))
    assert d.kind == "none"
    assert d.proposal is None


# --- count >= 3 + no existing fix -> rule ---

@pytest.mark.parametrize("count", [3, 4, 11])
def test_threshold_no_fix_proposes_rule(count):
    d = decide_escalation(_state(count=count, active_gate=None), class_name="CLASS_B")
    assert d.kind == "rule"
    assert d.proposal is not None
    assert d.proposal["proposal_kind"] == "rule"
    assert d.proposal["source_class"] == "CLASS_B"
    assert d.proposal["occurrence_count"] == count


# --- AC1 (NEGATIVE): no existing fix never yields a gate ---

def test_ac1_no_fix_never_gate():
    for count in (3, 5, 50):
        d = decide_escalation(_state(count=count, active_gate=None))
        assert d.kind != "gate", "gate rung is Phase 3 — unreachable in Phase 2"


# --- Truth table: existing fix -> none (do NOT re-propose / no 4th rule) ---

def test_canonical_class_a_seed_with_gate_is_none():
    """The real CLASS_A seed (count=11, active_gate=GC12) must NOT re-propose.
    A structural fix already exists; re-proposing is the anti-pattern Gate 1 caught."""
    seed = _state(count=11, active_gate="GC12", post_gate_count=0)
    d = decide_escalation(seed, class_name="CLASS_A")
    assert d.kind == "none"
    assert d.proposal is None


def test_existing_gate_below_threshold_also_none():
    d = decide_escalation(_state(count=2, active_gate="GC12"))
    assert d.kind == "none"


# --- Totality: never raises, always returns a defined kind ---

@pytest.mark.parametrize("count", [0, 3, 100])
@pytest.mark.parametrize("gate", [None, "GC12"])
def test_total_function_never_raises(count, gate):
    d = decide_escalation(_state(count=count, active_gate=gate))
    assert isinstance(d, EscalationDecision)
    assert d.kind in {"none", "rule"}  # Phase 2 reachable kinds


def test_resolved_class_is_none():
    """A resolved class (gate held 30d) does not re-propose."""
    d = decide_escalation(_state(count=11, active_gate="GC12", resolved=True))
    assert d.kind == "none"


def test_missing_keys_degrade_to_none():
    """A malformed/sparse state dict degrades to none, never raises."""
    assert decide_escalation({}).kind == "none"
    assert decide_escalation({"count": 5}).kind == "rule"  # count present, no fix


def test_decision_dataclass_shape():
    d = EscalationDecision(kind="rule", proposal={"proposal_kind": "rule"})
    assert d.kind == "rule"
    assert d.proposal["proposal_kind"] == "rule"
