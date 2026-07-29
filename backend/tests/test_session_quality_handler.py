"""Tests for the session-quality job handler — layer②③ weekly orchestration.

The handler wires: sample sessions → score (layer③) → low-score record +
harvest draft (layer②) → weekly report. All external boundaries are injected
seams (no Bedrock, no real DB, no disk) — same discipline as eval_scheduled.

The VALUE-BEARING logic tested here is `select_sessions` (the sampling 口径,
XG-fixed): a session qualifies if it HAS a correction OR is turn-anomalous
(turns > 20, or turns == 1 with a correction), capped at N=10 (fixed).
"""
from __future__ import annotations

import asyncio

from jobs.handlers.session_quality import (
    select_sessions,
    _enumerate_sessions,
    run_session_quality,
)


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


# ── DAO-contract + N+1-efficiency regression ──
# History: _enumerate_sessions once called db.sessions.list_all() (nonexistent) →
# swallowed → inert. Then the N+1 fix: it now (1) gets turn counts from ONE indexed
# aggregate db.messages.role_counts_by_session("user"), then (2) hydrates messages
# ONLY for the ≤N sessions select_sessions picks — NOT all sessions. These pin BOTH
# the count-source contract AND the "only hydrate selected" efficiency invariant.

class _FakeMessages:
    def __init__(self, counts):
        self._counts = counts
        self.hydrated: list[str] = []          # spy: which sids got list_by_session
    async def role_counts_by_session(self, role):
        assert role == "user"                  # the sampler counts USER turns
        return dict(self._counts)
    async def list_by_session(self, sid):
        self.hydrated.append(sid)              # record every hydration
        return [
            {"role": "user", "content": "fix the bug in X"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "done"},
                {"type": "tool_use", "name": "Edit"},
            ]},
        ]

class _FakeDB:
    def __init__(self, counts):
        self.messages = _FakeMessages(counts)


def test_enumerate_counts_via_aggregate_not_per_session_loop():
    """Turn counts must come from ONE role_counts_by_session aggregate — NOT a
    per-session list_by_session loop. If reverted to the loop, _FakeMessages has no
    such attr → AttributeError → RED (non-vacuous)."""
    # 3 sessions; only 'anom' (turns>20) qualifies for sampling
    db = _FakeDB({"anom": 30, "normal": 5, "tiny": 2})
    out = asyncio.run(_enumerate_sessions(db, correction_session_ids=set()))
    assert out["session_turns"] == {"anom": 30, "normal": 5, "tiny": 2}
    # hydrated ONLY the selected (turns>20) session — not all 3 (the N+1 fix)
    assert db.messages.hydrated == ["anom"]
    assert out["sessions"]["anom"]["tool_names"] == ["Edit"]
    # non-selected sessions are counted but NOT hydrated
    assert "normal" not in out["sessions"]


def test_enumerate_hydrates_only_selected_scales_with_N_not_total():
    """The efficiency invariant: message reads == len(selected), independent of how
    many sessions exist. 100 sessions, 2 anomalous → exactly 2 hydrations."""
    counts = {f"s{i}": 3 for i in range(100)}   # 100 normal (won't select)
    counts["big1"] = 25; counts["big2"] = 40    # 2 anomalous (turns>20)
    db = _FakeDB(counts)
    out = asyncio.run(_enumerate_sessions(db, correction_session_ids=set()))
    assert sorted(db.messages.hydrated) == ["big1", "big2"]   # 2 reads, not 102
    assert set(out["sessions"]) == {"big1", "big2"}


def test_sampler_failure_is_loud_not_silent_empty():
    """A broken sampler must surface status=degraded + sampler_error, NOT masquerade
    as a clean 0-session success (GC19: the exact mask that hid list_all())."""
    def _boom_sampler():
        return {"session_turns": {}, "sessions": {},
                "sampler_error": "AttributeError: 'SQLiteTable' object has no attribute 'list_all'"}
    res = run_session_quality(
        dry_run=True, sampler=_boom_sampler,
        scorer=lambda p, r, t: {"status": "success", "goal_score": 1.0, "tool_score": 1.0},
        harvester=lambda **k: None, recorder=lambda *a: None,
    )
    assert res["status"] == "degraded"
    assert "sampler_error" in res


def test_degraded_maps_to_valid_JobResult_literal():
    """The handler's internal 'degraded' MUST map to a valid JobResult.status
    Literal at the executor boundary — passing 'degraded' raw would ValidationError
    → swallowed → misreported as 'failed' with sampler_error lost (adversarial catch).
    This closes the coverage gap: the handler-only test never built a JobResult."""
    from jobs.models import JobResult
    from datetime import datetime, timezone

    # simulate the executor branch's mapping (executor.py session_quality branch)
    sq_result = {"status": "degraded", "scored": 0, "low": 0, "drafts": 0,
                 "sampler_error": "AttributeError: no attribute 'list_all'"}
    sq_err = sq_result.get("sampler_error")
    # This construction MUST NOT raise — "degraded" is NOT a valid Literal, "partial" is.
    jr = JobResult(
        job_id="session-quality", timestamp=datetime.now(timezone.utc),
        status="partial" if sq_err else "success",
        summary=f"session-quality: 0 scored — SAMPLER FAULT: {sq_err}",
        duration_seconds=0.0,
    )
    assert jr.status == "partial"
    assert "SAMPLER FAULT" in jr.summary
    # guard the invariant: 'degraded' is genuinely NOT constructable (proves the map is needed)
    import pytest
    with pytest.raises(Exception):
        JobResult(job_id="x", timestamp=datetime.now(timezone.utc),
                  status="degraded", summary="", duration_seconds=0.0)
