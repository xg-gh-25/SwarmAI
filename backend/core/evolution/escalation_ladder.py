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

# Mirror correction_tracker's promotion threshold. A class must recur >=3 times
# before any governance proposal is worth a human's attention.
_PROPOSE_THRESHOLD = 3


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
    count = int(state.get("count", 0) or 0)
    active_gate = state.get("active_gate")
    resolved = bool(state.get("resolved", False))

    # Resolved classes are dormant — never re-propose.
    if resolved:
        return EscalationDecision(kind="none")

    # Below the promotion threshold — log only.
    if count < _PROPOSE_THRESHOLD:
        return EscalationDecision(kind="none")

    # A structural fix already exists. Re-proposing a rule for a class that already
    # has one is the "4th rule" anti-pattern (SOUL Escalation Rule). The "fix failed
    # -> escalate to gate" rung is Phase 3 (needs the dashboard's register caller).
    if active_gate:
        return EscalationDecision(kind="none")

    # count >= threshold AND no structural fix yet -> propose a rule.
    proposal = {
        "target": "governance",
        "proposal_kind": "rule",
        "source_class": class_name,
        "occurrence_count": count,
        "proposed_rule": f"Recurring {class_name or 'correction'} pattern "
        f"({count}x) with no structural fix — propose an L1 rule.",
        "evidence": [e.get("text", "") for e in state.get("evidence", []) if isinstance(e, dict)][:5],
        "confidence": min(0.9, 0.5 + (count - _PROPOSE_THRESHOLD) * 0.05),
    }
    return EscalationDecision(kind="rule", proposal=proposal)
