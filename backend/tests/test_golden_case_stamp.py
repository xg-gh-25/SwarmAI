"""Tests for the validated_by_4gate content-bound stamp + gate_teeth.

Covers the eval-gate hardening (run_5edf2cc0, gaps G1/G3/G8):
- compute_case_stamp is CANONICAL — invariant to dict key ordering and to the
  _origin tag eval_service injects on load (Gate-1 BLOCK-A: a naive sha256(dict)
  mismatches after a yaml round-trip → BVT empties → gate RED forever).
- gate_teeth requires a negative_command on gate-eligible programmatic cases,
  but ONLY on non-grandfathered ones (Gate-1 BLOCK-D: enforcing it on the legacy
  corpus, which has zero negatives, would fail-all → can't stamp → BVT empties).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.golden_case_validator import (  # noqa: E402
    compute_case_stamp,
    gate_teeth,
    _STAMP_EXCLUDED_FIELDS,
)


def _case(**over):
    c = {
        "id": "GS_TEST001",
        "category": "test",
        "dimension": "capability",
        "eval_method": "programmatic",
        "affected_by": [],
        "evaluators": ["file_contains"],
        "verification": {"file": "README.md", "expected_contains": "Swarm"},
    }
    c.update(over)
    return c


# ── G1/G8: canonical stamp stability ──────────────────────────────────

def test_stamp_is_deterministic():
    assert compute_case_stamp(_case()) == compute_case_stamp(_case())


def test_stamp_invariant_to_key_order():
    """A yaml round-trip / dict rebuild can reorder keys. The stamp MUST NOT
    change — else every stamp mismatches after the first CRUD re-serialize."""
    c1 = _case()
    # rebuild with reversed insertion order
    c2 = {k: c1[k] for k in reversed(list(c1.keys()))}
    assert compute_case_stamp(c1) == compute_case_stamp(c2)


def test_stamp_invariant_to_origin_injection():
    """eval_service injects _origin on load and strips it on write. The stamp
    must ignore it (Gate-1 BLOCK-A)."""
    base = _case()
    with_origin = _case(_origin="public")
    assert compute_case_stamp(base) == compute_case_stamp(with_origin)


def test_stamp_invariant_to_user_owned_fields():
    """tags/notes/promoted_from are merge-mutable (eval_service _USER_OWNED_FIELDS)
    — they must not be part of the body identity."""
    base = _case()
    mutated = _case(tags=["x"], notes="hello", promoted_from="draft")
    assert compute_case_stamp(base) == compute_case_stamp(mutated)


def test_stamp_excludes_itself():
    """The stamp field must not feed its own hash (circularity)."""
    base = _case()
    stamped = _case(validated_by_4gate="deadbeef")
    assert compute_case_stamp(base) == compute_case_stamp(stamped)


def test_stamp_changes_when_body_changes():
    """The NEGATIVE/teeth of the stamp: edit a meaningful body field → different
    stamp → case drops from BVT. This is the drift-detection guarantee."""
    base = _case()
    tampered = _case(verification={"file": "README.md", "expected_contains": "EVIL"})
    assert compute_case_stamp(base) != compute_case_stamp(tampered)


def test_excluded_fields_are_the_expected_set():
    assert _STAMP_EXCLUDED_FIELDS == frozenset(
        {"_origin", "validated_by_4gate", "tags", "notes", "promoted_from"}
    )


# ── G3: gate_teeth (new-cases-only) ───────────────────────────────────

def test_teeth_fails_new_gate_eligible_without_negative():
    """A NEW gate-eligible programmatic case with no negative_command FAILS."""
    ok, errs = gate_teeth(_case(), grandfathered=False)
    assert not ok
    assert any("negative" in e.lower() for e in errs)


def test_teeth_passes_with_negative_command():
    c = _case(verification={"file": "README.md", "expected_contains": "Swarm",
                            "negative_command": "false"})
    ok, errs = gate_teeth(c, grandfathered=False)
    assert ok, errs


def test_teeth_grandfathers_legacy():
    """Legacy cases (grandfathered=True) pass even without a negative — Gate-1
    BLOCK-D: enforcing teeth on the legacy corpus would empty the BVT."""
    ok, errs = gate_teeth(_case(), grandfathered=True)
    assert ok, errs


def test_teeth_ignores_non_gate_eligible():
    """An llm case is never gate-eligible → teeth does not apply."""
    c = _case(eval_method="llm", evaluators=["llm_judge"])
    ok, errs = gate_teeth(c, grandfathered=False)
    assert ok, errs
