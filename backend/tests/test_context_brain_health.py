"""Tests for the per-file Health tag in context_brain (DoD5, run_d0ba3f69).

Health tag precedence (Gate-1 correction — SINGLE tag, severity-first):
    oversized (>90% budget) > growing (>60% budget) > idle (>=14d) > fresh (<7d)
A file that is BOTH idle AND oversized shows **oversized** (the actionable risk).
The 7-14d mtime gap gets no time tag → defaults to 'fresh' only if <7d, else the
size tags decide; a middle-aged small file is 'fresh' by fallthrough (neutral).
"""
from __future__ import annotations

from core.context_brain import _health_tag


BUDGET = 91_000


def test_oversized_beats_everything():
    # >90% of budget → oversized, even if also idle
    assert _health_tag(tokens=int(BUDGET * 0.95), budget=BUDGET, mtime_days=999) == "oversized"


def test_growing_when_over_60_not_90():
    assert _health_tag(tokens=int(BUDGET * 0.70), budget=BUDGET, mtime_days=1) == "growing"


def test_growing_beats_idle():
    # 70% budget AND old → growing wins over idle (size severity > time)
    assert _health_tag(tokens=int(BUDGET * 0.70), budget=BUDGET, mtime_days=999) == "growing"


def test_idle_when_old_and_small():
    assert _health_tag(tokens=int(BUDGET * 0.10), budget=BUDGET, mtime_days=20) == "idle"


def test_fresh_when_recent_and_small():
    assert _health_tag(tokens=int(BUDGET * 0.10), budget=BUDGET, mtime_days=2) == "fresh"


def test_middle_age_small_is_fresh_fallthrough():
    # 7-14d gap, small file → no idle (needs >=14d), no size tag → fresh fallback
    assert _health_tag(tokens=int(BUDGET * 0.10), budget=BUDGET, mtime_days=10) == "fresh"


def test_zero_budget_safe():
    # div-by-zero guard: no budget → size tags can't fire, time decides
    assert _health_tag(tokens=5000, budget=0, mtime_days=2) == "fresh"
    assert _health_tag(tokens=5000, budget=0, mtime_days=30) == "idle"
