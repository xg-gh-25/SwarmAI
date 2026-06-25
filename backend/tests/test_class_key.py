"""Tests for canonical_class_key — the single class-name normalizer."""

from core.evolution.class_key import (
    canonical_class_key,
    is_cognitive_class,
    NON_COGNITIVE_CLASSES,
)


class TestIsCognitiveClass:
    """Axis classification — governance/loop-closure is cognitive-only."""

    def test_cognitive_classes_are_cognitive(self):
        for c in ("CLASS_A", "CLASS_B", "CLASS_C", "class a"):
            assert is_cognitive_class(c) is True, c

    def test_non_cognitive_axes_excluded(self):
        for c in ("OPERATIONAL", "operational", "UNCLASSIFIED", "unclassified"):
            assert is_cognitive_class(c) is False, c

    def test_empty_or_none_defaults_cognitive(self):
        # Legacy/miner forms with no class name keep the old escalatable default.
        assert is_cognitive_class("") is True
        assert is_cognitive_class(None) is True

    def test_axis_label_contract_is_pinned(self):
        # LOW (adversarial b4eb5124): default-True means a FUTURE non-cognitive
        # axis label silently re-pollutes governance/audit until added here. This
        # test pins the contract: every axis label the classifier can emit for a
        # NON-cognitive record (operational tool_failure, unrouted) MUST be in
        # NON_COGNITIVE_CLASSES. If a new operational/infra axis is introduced,
        # add its canonical label here AND to NON_COGNITIVE_CLASSES — this test
        # fails loudly rather than letting the bug recur silently.
        emittable_non_cognitive_axes = {"operational", "unclassified"}
        for raw in emittable_non_cognitive_axes:
            assert canonical_class_key(raw) in NON_COGNITIVE_CLASSES, (
                f"{raw!r} is an emittable non-cognitive axis but not guarded — "
                f"add canonical_class_key({raw!r}) to NON_COGNITIVE_CLASSES"
            )


def test_lowercase_axis_uppercased():
    assert canonical_class_key("operational") == "OPERATIONAL"


def test_class_a_description_dropped():
    assert canonical_class_key("CLASS A: Confidence → Skip Process") == "CLASS_A"


def test_already_canonical_unchanged():
    assert canonical_class_key("CLASS_A") == "CLASS_A"


def test_idempotent():
    once = canonical_class_key("CLASS A: foo")
    assert canonical_class_key(once) == once


def test_empty_and_none():
    assert canonical_class_key("") == ""
    assert canonical_class_key(None) == ""


def test_whitespace_collapses_to_empty():
    assert canonical_class_key("   ") == ""


def test_reexport_from_escalation_ladder_still_works():
    """governance_miner.py + test_escalation_ladder.py import it from there."""
    from core.evolution.escalation_ladder import canonical_class_key as cck
    assert cck("operational") == "OPERATIONAL"
