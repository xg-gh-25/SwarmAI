"""Evolution Pipeline v3 Phase 2 — escalation ladder (decision function).

Given a correction class's tracker state, decide whether to propose a governance
change and of what KIND. This encodes the CLASS-A lesson — "rules don't stop the
pattern, only gates do" — as an escalation:

    occurrence 1-2                         -> none  (log only)
    occurrence >=3, no structural fix yet  -> rule  (propose an L1 AGENT/STEERING rule)
    a structural fix already exists, and
        it failed (recurrence post-fix)    -> gate  (escalate the ENFORCEMENT mechanism)

**Phase 2 scope (re-scoped after Gate 1 BLOCK run_6cb825e4):** only the REACHABLE
rungs ship now — `none` and `rule`. The `gate` rung requires a "rule was accepted"
signal that nothing produces until the Phase-3 dashboard wires acceptance ->
`tracker.register_rule`. Building the gate rung now would be dead code (its caller
does not exist yet). So Phase 2:

  - count < 3                       -> none
  - count >= 3 AND active_gate None -> rule   (no structural fix exists -> propose one)
  - count >= 3 AND active_gate set  -> none   (a fix EXISTS; re-proposing a rule would
                                               be the "4th rule" anti-pattern. The
                                               rule-failed -> gate escalation is Phase 3.)
  - resolved class                  -> none

`decide_escalation` is a PURE TOTAL function: no I/O, every input returns a defined
EscalationDecision, never raises on a sparse/malformed state dict.

Safety: this module only PRODUCES proposal data. It NEVER writes SOUL/AGENT/STEERING
and never mutates the tracker.

Design: Knowledge/Designs/2026-06-22-evolution-pipeline-v3-governance-routing-design.md §6
"""

from __future__ import annotations

from dataclasses import dataclass

# canonical_class_key now lives in class_key.py (single normalization home, shared
# with correction_tracker). Re-exported here for back-compat: governance_router,
# governance_miner, and tests import it from this module.
from core.evolution.class_key import (  # noqa: F401  (canonical re-exported)
    canonical_class_key,
    is_cognitive_class,
)

# Mirror correction_tracker's promotion threshold. A class must recur >=3 times
# before any governance proposal is worth a human's attention.
_PROPOSE_THRESHOLD = 3
# Recurrences AFTER a rule is accepted that mean "the rule failed -> escalate to a
# gate". Mirrors correction_tracker._RED_THRESHOLD (kept in sync; both = 2).
_RED_THRESHOLD = 2

# Non-cognitive axis guard: governance (AGENT/STEERING rules) is a COGNITIVE
# concern (a recurring JUDGMENT pattern, CLASS_A/B/C). OPERATIONAL/UNCLASSIFIED
# never escalate, regardless of count — without this an OPERATIONAL count >= 3
# emits "Recurring OPERATIONAL Nx — propose an L1 rule" (fake-governance pollution).
# Shared definition lives in class_key (one home for escalation + closed-loop audit).


@dataclass
class EscalationDecision:
    """The ladder's verdict for one correction class.

    kind:
        "none" -> no proposal (below threshold, fix already exists, or resolved)
        "rule" -> propose an L1 rule (proposal dict populated)
    (Phase 3 will add "gate" and "alert".)
    proposal: a GovernanceProposal-shaped dict (proposal_kind tagged), or None.
    """

    kind: str
    proposal: dict | None = None


def decide_escalation(class_state: dict, class_name: str = "") -> EscalationDecision:
    """Decide the escalation rung for one correction class. Pure + total.

    Args:
        class_state: a tracker.get_class() dict (or sparse/empty). Reads
            ``count``, ``active_gate``, ``resolved`` defensively via .get().
        class_name: the class name (e.g. "CLASS_A") for the proposal payload.

    Returns:
        EscalationDecision. kind is always one of the Phase-2 reachable values
        ("none" | "rule"); never raises.
    """
    state = class_state if isinstance(class_state, dict) else {}
    # Total function: a malformed count must degrade to 0, never raise (the stated
    # invariant — adversarial MED). The caller's try/except masks a raise, but the
    # function itself must honor totality.
    try:
        count = int(state.get("count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    active_gate = state.get("active_gate")
    active_rule = state.get("active_rule")
    try:
        post_rule_count = int(state.get("post_rule_count", 0) or 0)
    except (TypeError, ValueError):
        post_rule_count = 0
    resolved = bool(state.get("resolved", False))
    evidence = [e.get("text", "") for e in state.get("evidence", []) if isinstance(e, dict)][:5]

    # Resolved classes are dormant — never re-propose.
    if resolved:
        return EscalationDecision(kind="none")

    # Axis guard: non-cognitive classes (OPERATIONAL/UNCLASSIFIED) never escalate
    # to a governance proposal regardless of count. is_cognitive_class normalizes
    # ("operational" == "OPERATIONAL") and defaults empty/unknown to cognitive.
    if class_name and not is_cognitive_class(class_name):
        return EscalationDecision(kind="none")

    # Below the promotion threshold — log only.
    if count < _PROPOSE_THRESHOLD:
        return EscalationDecision(kind="none")

    # A code gate already exists — terminal. The gate IS the strongest enforcement;
    # there is no rung above it. No further proposal.
    if active_gate:
        return EscalationDecision(kind="none")

    # GATE RUNG (Phase 3, now reachable): a rule was accepted (active_rule set) and the
    # class RECURRED past the threshold anyway (post_rule_count >= RED). The rule failed
    # to stop the pattern — escalate the ENFORCEMENT MECHANISM to a code gate, NOT a 4th
    # rule. This is the CLASS-A lesson: "rules don't stop it, only gates do."
    if active_rule and post_rule_count >= _RED_THRESHOLD:
        proposal = {
            "target": "governance",
            "proposal_kind": "gate",
            "source_class": class_name,
            "occurrence_count": count,
            "proposed_rule": f"{class_name or 'correction'}: rule {active_rule} failed "
            f"({post_rule_count}x recurrence post-rule) — escalate to a CODE GATE.",
            "evidence": evidence,
            "confidence": min(0.95, 0.7 + (post_rule_count - _RED_THRESHOLD) * 0.05),
        }
        return EscalationDecision(kind="gate", proposal=proposal)

    # A rule is active but hasn't failed enough yet (post_rule_count < RED) -> wait.
    if active_rule:
        return EscalationDecision(kind="none")

    # count >= threshold AND no structural fix yet -> propose a rule.
    proposal = {
        "target": "governance",
        "proposal_kind": "rule",
        "source_class": class_name,
        "occurrence_count": count,
        "proposed_rule": f"Recurring {class_name or 'correction'} pattern "
        f"({count}x) with no structural fix — propose an L1 rule.",
        "evidence": evidence,
        "confidence": min(0.9, 0.5 + (count - _PROPOSE_THRESHOLD) * 0.05),
    }
    return EscalationDecision(kind="rule", proposal=proposal)
