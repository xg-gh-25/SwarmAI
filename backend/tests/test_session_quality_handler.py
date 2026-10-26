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



def test_a_judge_that_fails_on_every_session_is_reported_not_shown_as_no_data(tmp_path):
    def broken(prompt, response, tools):
        return {"status": "error", "notes": "judge call failed: ThrottlingException: slow down"}
    corr = [{"type": "user_correction", "session_id": "a", "ts": NOW.timestamp()}]
    result, _, _ = _run(tmp_path, {"a": _good()}, corrections=corr, scorer=broken)
    assert result["judge_error"].startswith("1/1 judged sessions failed")
    ok, _, _ = _run(tmp_path, {"a": _good()}, corrections=corr)
    assert ok["judge_error"] is None


def test_language_is_judged_on_the_answer_and_a_pasted_log_keeps_a_chinese_question_chinese(tmp_path):
    log = "Traceback (most recent call last): File app.py line 3 in main ValueError invalid literal for int"
    sessions = {
        "log": {"title": "log", "messages": [_msg("user", f"为什么报错？{log}"),
                                             _msg("assistant", [_text("因为输入不是数字。")])]},
        "narrated": {"title": "narr", "messages": [_msg("user", "改一下这个函数"),
                                                   _msg("assistant", [_text("Let me read the function first and check what it currently returns before I change anything at all."), _tool(),
                                                                      _text("已经改好了，现在返回整数。")])]},
    }
    result, _, _ = _run(tmp_path, sessions)
    assert result["checks"]["O1_LANGUAGE"] == {"checked": 2, "failed": 0, "rate": 0.0, "examples": []}



def test_the_apps_own_wrapper_lines_are_not_the_users_language(tmp_path):
    sessions = {
        "att": {"title": "t", "messages": [_msg("user", "[Attached terminal output]"),
                                           _msg("assistant", [_text("这是构建失败的原因。")])]},
        "review": {"title": "r", "messages": [
            _msg("user", "[File Review: `docs/a.md`]\n\nSelected text:\n```\nsome english text\n```\n这段改短一点"),
            _msg("assistant", [_text("改好了，现在只有两句。")])]},
    }
    result, _, _ = _run(tmp_path, sessions)
    assert result["checks"]["O1_LANGUAGE"] == {"checked": 1, "failed": 0, "rate": 0.0, "examples": []}


def test_the_judge_sees_one_question_with_its_own_answer(tmp_path):
    session = {"title": "Two asks", "messages": [
        _msg("user", "端口是多少？"), _msg("assistant", [_tool("Grep"), _text("端口是 7421。")]),
        _msg("user", "再把服务名改成 api"), _msg("assistant", [_tool("Edit"), _text("改好了。")])]}
    calls = []

    def scorer(prompt, response, tools):
        calls.append((prompt, response, tools))
        return {"goal_score": 1.0, "tool_score": 1.0, "reason": ""}
    corr = [{"type": "user_correction", "session_id": "a", "ts": NOW.timestamp()}]
    _run(tmp_path, {"a": session}, corrections=corr, scorer=scorer)
    assert calls == [("端口是多少？", "端口是 7421。", ["Grep"])]


# ── metrics, weekly backfill, judge prompt version (design §8 item 4) ────────────

def _at(days_ago, minute=0):
    return (NOW - timedelta(days=days_ago, minutes=-minute)).astimezone().replace(tzinfo=None).isoformat()


def _timed(days_ago, seconds, text="端口是多少？"):
    return {"title": "t", "messages": [
        _msg("user", text, at=_at(days_ago)),
        _msg("assistant", [_text("端口是 7421。")],
             at=(datetime.fromisoformat(_at(days_ago)) + timedelta(seconds=seconds)).isoformat())]}


def test_turn_costs_are_increases_of_the_running_total():
    rows = [("a", "2026-10-20T10:00:00", 1.0),   # first row of a: no baseline
            ("a", "2026-10-20T10:05:00", 1.5),   # +0.5
            ("b", "2026-10-20T10:06:00", 9.0),   # first row of b
            ("a", "2026-10-20T10:07:00", 0.3),   # new process: 0.3
            ("b", "2026-10-20T10:08:00", 9.25),  # +0.25
            ("a", "2026-10-28T10:00:00", 0.9)]   # +0.6, outside the window
    costs = sq._turn_costs(rows, "2026-10-19T00:00:00", "2026-10-26T00:00:00")
    assert sorted(costs) == [0.25, 0.3, 0.5]


def test_the_state_carries_turn_metrics_and_the_prompt_version(tmp_path):
    from core.session_scorer import JUDGE_PROMPT_SHA

    sessions = {"a": _timed(1, 60), "b": _timed(2, 120), "c": _timed(3, 7 * 3600)}  # c: tab left open
    rows = [("a", _at(1), 1.0), ("a", _at(1, 1), 2.0), ("a", _at(1, 2), 2.5)]
    spath = tmp_path / "state.json"
    state = sq.run_session_quality(sampler=lambda since: {"sessions": sessions}, scorer=_scorer(),
                                   corrections_path=tmp_path / "c.jsonl", state_path=spath, now=NOW,
                                   cost_reader=lambda since: rows)
    assert state["metrics"] == {"turns": 3, "turn_p50_s": 90, "cost_p50_usd": 0.75}
    assert state["judge_prompt_sha"] == JUDGE_PROMPT_SHA
    assert state["trend"][-1]["metrics"] == state["metrics"]
    assert json.loads(spath.read_text())["metrics"] == state["metrics"]


def _history_of(sessions, calls):
    def history(since, consume):
        calls.append(since)
        for sid, s in sessions.items():
            consume(sid, s)
    return history


def test_weeks_before_the_first_run_are_backfilled_once(tmp_path):
    past = {"w1": _english_answer_to_chinese(), "w2": _good()}
    for m in past["w1"]["messages"]:
        m["created_at"] = _at(10)          # 2026-W42
    for m in past["w2"]["messages"]:
        m["created_at"] = _at(4)           # 2026-W43, the last whole week
    cpath = tmp_path / "c.jsonl"
    start_epoch = (NOW - timedelta(days=8)).timestamp()      # the log covers from W42's last day
    cpath.write_text(json.dumps({"type": "tool_failure", "ts": start_epoch}) + "\n"
                     + json.dumps({"type": "user_correction", "session_id": "w2",
                                   "ts": (NOW - timedelta(days=4)).timestamp()}) + "\n")
    spath = tmp_path / "state.json"
    calls: list = []
    run = lambda: sq.run_session_quality(  # noqa: E731
        sampler=lambda since: {"sessions": {"now": _good()}}, scorer=_scorer(),
        corrections_path=cpath, state_path=spath, now=NOW, history=_history_of(past, calls),
        cost_reader=lambda since: [])
    state = run()
    trend = {t["week"]: t for t in state["trend"]}
    assert len(state["trend"]) == 12 and state["trend"][-1]["week"] == "2026-W44"
    assert [t["week"] for t in state["trend"]] == sorted(trend)
    two_back, last = trend["2026-W42"], trend["2026-W43"]
    assert two_back["backfilled"] and two_back["rates"]["O1_LANGUAGE"] == 1.0
    assert "O3_CORRECTIONS" not in two_back["rates"]          # the log did not cover that week
    assert last["rates"]["O3_CORRECTIONS"] == 1.0              # covered, and w2 was corrected
    assert not ({"O4_GOAL", "O5_TOOLS"} & set(last["rates"]))  # the judge is not backfilled
    assert trend["2026-W40"]["metrics"]["turns"] == 0          # an empty week is still recorded
    assert len(calls) == 1
    run()
    assert len(calls) == 1                                     # nothing left to backfill


def test_no_backfill_when_the_sampler_failed_or_none_is_injected(tmp_path):
    calls: list = []
    state = sq.run_session_quality(
        sampler=lambda since: {"sessions": {}, "sampler_error": "X: y"}, scorer=_scorer(),
        corrections_path=tmp_path / "c.jsonl", state_path=tmp_path / "s.json", now=NOW,
        history=_history_of({"a": _good()}, calls))
    assert state["trend"] == []
    result, _, _ = _run(tmp_path, {"a": _good()})                # injected sampler only
    assert [t["week"] for t in result["trend"]] == ["2026-W44"]


def test_the_database_returns_cost_rows_in_order(tmp_path):
    from database.sqlite import SQLiteDatabase

    db = SQLiteDatabase(str(tmp_path / "t.db"))
    asyncio.run(db.initialize())
    for sid, cost in (("a", 1.0), ("b", None), ("a", 1.5)):
        asyncio.run(db.record_token_usage(session_id=sid, cost_usd=cost))
    rows = asyncio.run(db.token_cost_rows("2000-01-01T00:00:00"))
    assert [(r[0], r[2]) for r in rows] == [("a", 1.0), ("a", 1.5)]
    assert asyncio.run(db.token_cost_rows("2999-01-01T00:00:00")) == []


def test_the_default_judge_is_asked_for_the_reason_in_the_users_language(tmp_path, monkeypatch):
    import core.session_scorer as ss

    prompts = []
    monkeypatch.setattr(ss, "default_judge_fn", lambda p: (prompts.append(p), json.dumps(
        {"goal_score": 0.2, "tool_score": 0.2, "dimension": "quality", "reason": "没交付"}))[1])
    corr = [{"type": "user_correction", "session_id": "a", "ts": (NOW - timedelta(days=1)).timestamp()}]
    cpath = tmp_path / "c.jsonl"
    cpath.write_text(json.dumps(corr[0]) + "\n")
    result = sq.run_session_quality(sampler=lambda since: {"sessions": {"a": _good()}},
                                    corrections_path=cpath, state_path=tmp_path / "s.json", now=NOW)
    assert "in Chinese" in prompts[0]
    assert result["low_details"][0]["reason"] == "没交付"


def test_backfilled_weeks_land_in_week_order_before_an_existing_run(tmp_path):
    prior = {"trend": [{"week": "2026-W43", "rates": {"O1_LANGUAGE": 0.5}}]}
    spath = tmp_path / "s.json"
    spath.write_text(json.dumps(prior))
    state = sq.run_session_quality(
        sampler=lambda since: {"sessions": {"a": _good()}}, scorer=_scorer(),
        corrections_path=tmp_path / "c.jsonl", state_path=spath, now=NOW,
        history=_history_of({}, []), cost_reader=lambda since: [])
    weeks = [t["week"] for t in state["trend"]]
    assert weeks == sorted(weeks) and weeks[-2:] == ["2026-W43", "2026-W44"]
    assert state["trend"][-2]["rates"] == {"O1_LANGUAGE": 0.5}   # a real run is never overwritten
