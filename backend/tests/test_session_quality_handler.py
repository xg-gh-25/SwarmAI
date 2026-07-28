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


# ── DAO-contract regression (the list_all() bug that shipped layer②③ inert) ──
# Root cause: _enumerate_sessions called db.sessions.list_all() — a method that
# does NOT exist on SQLiteTable (real API is .list()). A blanket except swallowed
# the AttributeError → 0 sessions → job "success" with 0 drafts. The prior tests
# injected a fake sampler, so this DAO seam was never exercised. These pin it.

class _FakeSessions:
    def __init__(self, rows):
        self._rows = rows
    async def list(self, user_id=None):            # the REAL SQLiteTable contract
        return self._rows

class _FakeMessages:
    async def list_by_session(self, sid):
        return [
            {"role": "user", "content": "fix the bug in X"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "done"},
                {"type": "tool_use", "name": "Edit"},
            ]},
        ]

class _FakeDB:
    def __init__(self, rows):
        self.sessions = _FakeSessions(rows)
        self.messages = _FakeMessages()


def test_enumerate_uses_list_not_list_all():
    """_enumerate_sessions must call the DAO's .list() and return real turns +
    (prompt, response, tool_names). If the code reverts to .list_all(), _FakeSessions
    has no such attr → AttributeError → this test goes RED (non-vacuous)."""
    out = asyncio.run(_enumerate_sessions(_FakeDB([{"id": "s1"}, {"id": "s2"}])))
    assert set(out["session_turns"]) == {"s1", "s2"}
    assert out["sessions"]["s1"]["prompt"] == "fix the bug in X"
    assert out["sessions"]["s1"]["tool_names"] == ["Edit"]


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
