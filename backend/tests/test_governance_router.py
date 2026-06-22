"""Tests for Evolution Pipeline v3 Phase 1: governance_router.

The router takes a JudgmentClassification and decides:
  - operational / counter_state="counted"  -> tracker.record() ONCE (auto-count)
  - cognitive / counter_state="pending_confirm" -> park in pending queue,
    tracker.record() NEVER called, emit a SOUL Intake Gate brief.

DoD negative tests:
  - AC3: CLASS_A classification NEVER auto-increments the counter.
  - AC4: operational classification calls record() exactly once (auto path live).
  - AC6: cognitive routing emits an Intake brief (classify/parent/conflict/budget).

Safety (design §9): router NEVER writes SOUL/AGENT/STEERING. The pending queue
is the only persistence; promotion happens later through the human gate.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.evolution.judgment_classifier import JudgmentClassification
from core.evolution.governance_router import route_classification


def _cognitive(class_name="CLASS_A", principle="P1"):
    return JudgmentClassification(
        correction_ref="1781696331.24:e95e5923",
        axis="cognitive",
        class_name=class_name,
        parent_principle=principle,
        skill_spread=[],
        blast_radius=0,
        evidence=["shipped 3 unverified subsystems"],
        tier="llm",
        confidence=0.8,
        counter_state="pending_confirm",
    )


def _operational():
    return JudgmentClassification(
        correction_ref="1781055459.41:cbcf9db7",
        axis="operational",
        class_name=None,
        parent_principle=None,
        skill_spread=["Bash"],
        blast_radius=1,
        evidence=["Exit code 2: no such file"],
        tier="mechanical",
        confidence=0.5,
        counter_state="counted",
    )


@pytest.fixture
def pending_path(tmp_path):
    return tmp_path / "governance_pending.json"


# --- AC3 (NEGATIVE): CLASS_A NEVER auto-increments the counter ---

def test_ac3_class_a_never_calls_record(pending_path):
    """AC3: a CLASS_A (cognitive) classification must NOT call tracker.record()."""
    tracker = MagicMock()
    route_classification(_cognitive("CLASS_A"), tracker, pending_path=pending_path)
    tracker.record.assert_not_called()


def test_ac3_all_cognitive_classes_park(pending_path):
    """Every cognitive CLASS parks in pending_confirm, none auto-count."""
    tracker = MagicMock()
    for cls in ("CLASS_A", "CLASS_B", "CLASS_C"):
        route_classification(_cognitive(cls), tracker, pending_path=pending_path)
    tracker.record.assert_not_called()
    queue = json.loads(pending_path.read_text())
    assert len(queue) == 3
    assert {item["class_name"] for item in queue} == {"CLASS_A", "CLASS_B", "CLASS_C"}


# --- AC4: operational calls record() exactly once (auto-count path LIVE) ---

def test_ac4_operational_records_once(pending_path):
    """AC4: operational classification auto-counts via record() exactly once."""
    tracker = MagicMock()
    route_classification(_operational(), tracker, pending_path=pending_path)
    tracker.record.assert_called_once()


def test_ac4_operational_does_not_park(pending_path):
    """Operational records do NOT go into the pending-confirm queue."""
    tracker = MagicMock()
    route_classification(_operational(), tracker, pending_path=pending_path)
    # queue file either absent or empty
    if pending_path.exists():
        assert json.loads(pending_path.read_text()) == []


# --- AC6: cognitive routing emits a SOUL Intake Gate brief ---

def test_ac6_cognitive_emits_intake_brief(pending_path):
    """AC6: cognitive routing returns an Intake brief with the 4 SOUL gate keys."""
    tracker = MagicMock()
    brief = route_classification(_cognitive("CLASS_A", "P1"), tracker, pending_path=pending_path)
    assert brief is not None
    for key in ("classify", "parent", "conflict", "budget"):
        assert key in brief, f"intake brief missing '{key}'"
    assert brief["parent"] == "P1"


def test_ac6_operational_no_brief(pending_path):
    """Operational routing returns None (no governance brief needed)."""
    tracker = MagicMock()
    brief = route_classification(_operational(), tracker, pending_path=pending_path)
    assert brief is None


# --- Pending queue is flock-safe append (re-read under lock) ---

def test_pending_queue_appends_not_overwrites(pending_path):
    """A second cognitive routing appends, preserving the first."""
    tracker = MagicMock()
    route_classification(_cognitive("CLASS_A"), tracker, pending_path=pending_path)
    route_classification(_cognitive("CLASS_B"), tracker, pending_path=pending_path)
    queue = json.loads(pending_path.read_text())
    assert len(queue) == 2


def test_none_classification_is_noop(pending_path):
    """Routing a None classification (degraded) is a safe no-op."""
    tracker = MagicMock()
    brief = route_classification(None, tracker, pending_path=pending_path)
    assert brief is None
    tracker.record.assert_not_called()
