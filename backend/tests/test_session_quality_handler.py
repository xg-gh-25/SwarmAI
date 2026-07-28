"""Tests for the session-quality job handler — layer②③ weekly orchestration.

The handler wires: sample sessions → score (layer③) → low-score record +
harvest draft (layer②) → weekly report. All external boundaries are injected
seams (no Bedrock, no real DB, no disk) — same discipline as eval_scheduled.

The VALUE-BEARING logic tested here is `select_sessions` (the sampling 口径,
XG-fixed): a session qualifies if it HAS a correction OR is turn-anomalous
(turns > 20, or turns == 1 with a correction), capped at N=10 (fixed).
"""
from __future__ import annotations

from jobs.handlers.session_quality import select_sessions


def _sessions(**turns_by_id):
    """{session_id: turn_count} → the shape select_sessions consumes."""
    return dict(turns_by_id)


def test_select_with_correction_qualifies():
    sel = select_sessions(
        session_turns={"a": 3, "b": 5},
        correction_session_ids={"a"},
    )
    assert "a" in sel and "b" not in sel  # a has a correction; b is normal


def test_select_turn_anomaly_long_qualifies():
    sel = select_sessions(
        session_turns={"long": 25, "normal": 4},
        correction_session_ids=set(),
    )
    assert "long" in sel and "normal" not in sel  # >20 turns is anomalous


def test_select_single_turn_needs_correction():
    # turns==1 alone is NOT anomalous; only turns==1 WITH a correction qualifies
    sel = select_sessions(
        session_turns={"one_nocorr": 1, "one_corr": 1},
        correction_session_ids={"one_corr"},
    )
    assert "one_corr" in sel
    assert "one_nocorr" not in sel


def test_select_caps_at_ten():
    # 15 correction sessions → capped at 10 (fixed N, not configurable)
    turns = {f"s{i}": 5 for i in range(15)}
    corr = {f"s{i}" for i in range(15)}
    sel = select_sessions(session_turns=turns, correction_session_ids=corr)
    assert len(sel) == 10


def test_select_empty_returns_empty():
    assert select_sessions(session_turns={}, correction_session_ids=set()) == []


def test_select_dedups_correction_and_anomaly():
    # a session that BOTH has a correction AND is long → counted once
    sel = select_sessions(
        session_turns={"both": 30},
        correction_session_ids={"both"},
    )
    assert sel.count("both") == 1
