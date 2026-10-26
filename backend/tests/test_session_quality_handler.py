"""Online eval scoring (jobs/handlers/session_quality.py; design
2026-10-26-eval-as-product §3.5, final test 7): only the last 7 days, the
deterministic checks over every session, the judge on at most 10, a state file the
dashboard reads back with the same numbers, and sampler errors kept apart from an
empty week. All boundaries injected: no Bedrock, no real DB."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs.handlers import session_quality as sq  # noqa: E402

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(days=1)).astimezone().replace(tzinfo=None).isoformat()
OLD = (NOW - timedelta(days=9)).astimezone().replace(tzinfo=None).isoformat()


def _msg(role, content, at=RECENT):
    return {"role": role, "content": json.dumps(content) if isinstance(content, list) else content,
            "created_at": at}


def _text(t):
    return {"type": "text", "text": t}


def _tool(name="Read"):
    return {"type": "tool_use", "name": name, "input": {}}


def _good(title="Good chat"):
    return {"title": title, "messages": [_msg("user", "端口是多少？"),
                                          _msg("assistant", [_tool()]),
                                          _msg("assistant", [_text("端口是 7421。")])]}


def _english_answer_to_chinese():
    return {"title": "Wrong language", "messages": [
        _msg("user", "这个函数是做什么的？"),
        _msg("assistant", [_text("It parses the config file and returns a dict of settings.")])]}


def _chatty_tools():
    return {"title": "Chatty", "messages": [
        _msg("user", "fix the typo in the readme please"),
        _msg("assistant", [_text("Sure."), _tool(), _text("Now let me also check another file."), _tool()]),
        _msg("assistant", [_text("Done, fixed the typo.")])]}


def _scorer(goal=1.0, tool=1.0, calls=None):
    def score(prompt, response, tools):
        if calls is not None:
            calls.append(prompt)
        return {"goal_score": goal, "tool_score": tool, "dimension": "capability", "reason": "judge said so"}
    return score


def _run(tmp_path, sessions, *, corrections=(), scorer=None, sampler_error=None, state=None):
    cpath = tmp_path / "corrections.jsonl"
    cpath.write_text("".join(json.dumps(c) + "\n" for c in corrections))
    spath = tmp_path / ".context" / ".session-quality-state.json"
    if state is not None:
        spath.parent.mkdir(parents=True, exist_ok=True)
        spath.write_text(json.dumps(state))
    seen = {}

    def sampler(since):
        seen["since"] = since
        out = {"sessions": sessions}
        if sampler_error:
            out = {"sessions": {}, "sampler_error": sampler_error}
        return out
    result = sq.run_session_quality(sampler=sampler, scorer=scorer or _scorer(), corrections_path=cpath,
                                    state_path=spath, now=NOW)
    return result, spath, seen


def test_the_window_is_seven_days_for_sessions_turns_and_corrections(tmp_path):
    old_turn = {"title": "Old", "messages": [_msg("user", "这个函数是做什么的？", at=OLD),
                                             _msg("assistant", [_text("It parses config.")], at=OLD)]}
    corrections = [{"type": "user_correction", "session_id": "a", "ts": (NOW - timedelta(days=9)).timestamp()},
                   {"type": "tool_failure", "session_id": "a", "ts": NOW.timestamp()}]
    result, _, seen = _run(tmp_path, {"a": _good(), "old": old_turn}, corrections=corrections)
    assert seen["since"] == (NOW - timedelta(days=7)).astimezone().replace(tzinfo=None).isoformat()
    assert result["sessions_checked"] == 1                       # the old turn is outside the window
    assert result["checks"]["O3_CORRECTIONS"]["failed"] == 0     # old correction, and a tool failure


def test_deterministic_checks_cover_every_session_and_name_examples(tmp_path):
    sessions = {"a": _good(), "b": _english_answer_to_chinese(), "c": _chatty_tools()}
    corrections = [{"type": "user_correction", "session_id": "c", "ts": NOW.timestamp()}]
    result, _, _ = _run(tmp_path, sessions, corrections=corrections)
    o1, o2, o3 = (result["checks"][k] for k in ("O1_LANGUAGE", "O2_SILENT_TOOLS", "O3_CORRECTIONS"))
    assert (o1["checked"], o1["failed"]) == (3, 1) and o1["examples"][0]["title"] == "Wrong language"
    assert o1["examples"][0]["snippet"] == "这个函数是做什么的？"
    assert (o2["checked"], o2["failed"]) == (2, 1) and o2["examples"][0]["session_id"] == "c"
    assert (o3["checked"], o3["failed"], o3["rate"]) == (3, 1, 0.333)


def test_the_judge_sees_at_most_ten_sessions_corrected_first(tmp_path):
    sessions = {f"s{i:02d}": _good() for i in range(25)}
    for sid in ("s20", "s21"):
        sessions[sid]["messages"] = [m for _ in range(11) for m in _good()["messages"]] * 2
    corrections = [{"type": "user_correction", "session_id": "s24", "ts": NOW.timestamp()}]
    calls = []
    result, _, _ = _run(tmp_path, sessions, corrections=corrections, scorer=_scorer(goal=0.2, calls=calls))
    assert result["checks"]["O4_GOAL"]["checked"] == 3           # only corrected / long sessions qualify
    assert result["checks"]["O1_LANGUAGE"]["checked"] > 25       # deterministic: every turn of every session
    assert [d["session_id"] for d in result["low_details"]] == ["s24", "s20", "s21"]
    many = {f"x{i:02d}": _good() for i in range(15)}
    corr = [{"type": "user_correction", "session_id": s, "ts": NOW.timestamp()} for s in many]
    capped, _, _ = _run(tmp_path, many, corrections=corr)
    assert capped["checks"]["O4_GOAL"]["checked"] == 10


def test_the_state_file_is_written_and_the_dashboard_reads_the_same_numbers(tmp_path, monkeypatch):
    from core.eval_service import EvalService
    from tests._eval_golden_fixture import install_golden_fixture

    result, spath, _ = _run(tmp_path, {"a": _good(), "b": _english_answer_to_chinese()})
    on_disk = json.loads(spath.read_text())
    assert on_disk["checks"]["O1_LANGUAGE"] == result["checks"]["O1_LANGUAGE"]
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    shown = EvalService(workspace_root=tmp_path).get_session_quality()
    assert shown["sessions_checked"] == 2
    assert shown["checks"]["O1_LANGUAGE"]["rate"] == result["checks"]["O1_LANGUAGE"]["rate"]
    assert shown["checks"]["O1_LANGUAGE"]["examples"][0]["title"] == "Wrong language"


def test_a_sampler_error_and_an_empty_week_are_different(tmp_path):
    broken, _, _ = _run(tmp_path, {}, sampler_error="OperationalError: database is locked")
    empty, _, _ = _run(tmp_path, {})
    assert broken["sampler_error"] and broken["sessions_checked"] == 0
    assert empty["sampler_error"] is None and empty["sessions_checked"] == 0
    assert all(c["rate"] is None for c in empty["checks"].values())   # not shown as 0%


def test_the_trend_gets_one_entry_per_week_and_keeps_twelve(tmp_path):
    prior = {"trend": [{"week": f"2026-W{w:02d}", "rates": {"O1_LANGUAGE": 0.1}} for w in range(30, 44)]
             + [{"week": "2026-W44", "rates": {"O1_LANGUAGE": 0.9}}]}
    result, _, _ = _run(tmp_path, {"a": _good()}, state=prior)
    weeks = [t["week"] for t in result["trend"]]
    assert len(weeks) == 12 and weeks[-1] == "2026-W44" and weeks.count("2026-W44") == 1
    assert result["trend"][-1]["rates"]["O1_LANGUAGE"] == 0.0     # this run replaced this week's entry
    broken, _, _ = _run(tmp_path, {}, sampler_error="X: y", state=prior)
    assert broken["trend"] == prior["trend"]                       # a failed week adds no point


def test_the_default_sampler_asks_the_database_for_the_window_only(monkeypatch):
    seen = {}

    class _Messages:
        async def role_counts_by_session(self, role, since=None):
            seen["since"] = since
            return {"s1": 2}

        async def list_by_session(self, sid):
            return [_msg("user", "hi")]

    class _Sessions:
        async def get(self, sid):
            return {"title": "T1"}

    class _DB:
        messages, sessions = _Messages(), _Sessions()
    monkeypatch.setattr("database.get_database", lambda: _DB())
    out = sq._default_sampler("2026-10-19T12:00:00")
    assert seen["since"] == "2026-10-19T12:00:00" and out["sessions"]["s1"]["title"] == "T1"


def test_the_database_window_counts_only_recent_messages(tmp_path):
    from database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(tmp_path / "t.db"))
    asyncio.run(db.initialize())
    for sid, at in (("old", "2026-10-01T10:00:00"), ("new", "2026-10-25T10:00:00")):
        asyncio.run(db.sessions.put({"id": sid, "title": sid, "created_at": at, "updated_at": at}))
        asyncio.run(db.messages.put({"id": f"m-{sid}", "session_id": sid, "role": "user", "content": "x",
                                     "created_at": at, "updated_at": at}))
    assert asyncio.run(db.messages.role_counts_by_session("user", since="2026-10-19T00:00:00")) == {"new": 1}
    assert set(asyncio.run(db.messages.role_counts_by_session("user"))) == {"old", "new"}


def test_degraded_maps_to_valid_JobResult_literal():
    """The executor maps a sampler fault to JobResult status 'partial' ('degraded' is
    not a valid status)."""
    from jobs.models import JobResult

    jr = JobResult(job_id="session-quality", timestamp=datetime.now(timezone.utc), status="partial",
                   summary="session-quality: 0 session(s) checked — SAMPLER FAULT: x", duration_seconds=0.0)
    assert jr.status == "partial"
    with pytest.raises(Exception):
        JobResult(job_id="x", timestamp=datetime.now(timezone.utc), status="degraded", summary="",
                  duration_seconds=0.0)
