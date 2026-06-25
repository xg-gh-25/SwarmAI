"""Canonical correction-class key — the single normalization home.

Extracted from escalation_ladder.py (Gate-1: decoupling, NOT cycle-breaking —
escalation_ladder is a leaf module, so no runtime cycle existed; this keeps the
tracker dependency-free and gives ALL key writers one shared normalizer).

The bug this fixes: the EVOLUTION.md miner emits "CLASS A: Confidence → Skip
Process" and operational axes arrive lowercase ("operational"), while the tracker
stored/read RAW keys and escalate_class wrote the CANONICAL key — so the same
logical class split across two entries and the escalation ladder's gate rung
could never fire. Routing every key operation (record/get/register + proposal
generation) through this one function makes accumulation, escalation-read, and
rule/gate registration converge on ONE key.

Public symbols:
    canonical_class_key(name) -> str
    NON_COGNITIVE_CLASSES        — axis labels that are NOT judgment classes
    is_cognitive_class(name) -> bool
"""

from __future__ import annotations

# Axis labels that are NOT cognitive judgment classes. The cognitive classes are
# CLASS_A/B/C (a recurring JUDGMENT pattern). OPERATIONAL/UNCLASSIFIED are the
# operational/unlabeled axes — they record tool failures and unrouted noise, which
# even when frequent are not governance ("propose a rule") or loop-closure signal.
# Single home so the escalation ladder AND the closed-loop audit share ONE
# definition (no drift between "what can escalate" and "what the audit judges").
NON_COGNITIVE_CLASSES = frozenset({"OPERATIONAL", "UNCLASSIFIED"})


def is_cognitive_class(name: str) -> bool:
    """True if a class name is a cognitive judgment class (CLASS_A/B/C, etc.).

    Non-cognitive = OPERATIONAL / UNCLASSIFIED (normalized). An empty/None/unknown
    name defaults to True (cognitive) so legacy/miner forms keep their old
    escalatable behavior — the guard only EXCLUDES the known non-cognitive labels.
    """
    if not name:
        return True
    return canonical_class_key(name) not in NON_COGNITIVE_CLASSES


def canonical_class_key(name: str) -> str:
    """Normalize a correction-class name to a stable dedup key.

    "CLASS A: Confidence → Skip Process" -> "CLASS_A"
    "operational"                        -> "OPERATIONAL"
    "CLASS_A"                            -> "CLASS_A"  (idempotent)
    "" / None                            -> ""

    Canonical = leading token before ':', spaces->underscores, uppercased.
    Idempotent: canonical_class_key(canonical_class_key(x)) == canonical_class_key(x).
    """
    if not name:
        return ""
    head = name.split(":", 1)[0].strip()  # drop ": description"
    return "_".join(head.split()).upper()  # "CLASS A" -> "CLASS_A"
