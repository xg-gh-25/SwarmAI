"""Canonical correction-class key — the single normalization home.

The EVOLUTION.md miner emits "CLASS A: Confidence → Skip Process" and operational
axes arrive lowercase ("operational"); every reader that compares class keys goes
through this one function so the same logical class is never split in two.

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
# Single home so the miner and the proposal readers share ONE definition.
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
