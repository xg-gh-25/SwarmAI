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

Public symbol:
    canonical_class_key(name) -> str
"""

from __future__ import annotations


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
