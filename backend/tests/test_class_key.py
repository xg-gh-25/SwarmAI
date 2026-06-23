"""Tests for canonical_class_key — the single class-name normalizer."""

from core.evolution.class_key import canonical_class_key


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
