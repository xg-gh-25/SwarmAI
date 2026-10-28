"""core/turn_outcome + SessionRouter._published_turn: every started turn leaves one
``turn_outcome`` block saying how it ended (design 2026-10-28-swarm-out-run-results §4.1).

Real path (conftest SQLite, real ``run_conversation``, the unit's ``send`` patched):
completed, error event, turn limit, a crash, a pause on a question and a turn with no
terminal event each write exactly one block with the right ``ended``. A turn that never
started writes nothing. The block stays out of resume history.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import asyncio

import core.session_manager  # noqa: F401 — binds db at import, before any patch
import routers.chat  # noqa: F401
from core.context_injector import _filter_tool_only_messages
from core.session_router import SessionRouter
from core.session_unit import SessionState
from core.turn_outcome import OutcomeRecorder, turn_costs
from core.turn_writer import TurnWriter


# ── the recorder ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("events,ended", [
    ([{"type": "result", "subtype": "success"}], "completed"),
    ([{"type": "error", "code": "MODEL_ERROR", "message": "throttled"}], "error"),
    ([{"type": "result", "subtype": "error_during_execution", "is_error": True}], "error"),
    ([{"type": "result", "subtype": "output_degenerated"}], "error"),
    ([{"type": "turn_limit_reached"}, {"type": "result", "subtype": "turn_limit_reached",
                                       "is_error": True}], "turn_limit"),
    ([{"type": "ask_user_question"}], "paused"),
    ([{"type": "cmd_permission_request"}], "paused"),
    ([{"type": "ask_user_question"}, {"type": "result", "subtype": "success"}], "completed"),
    ([{"type": "assistant", "content": []}], "none"),
])
def test_ended_follows_the_last_terminal_event(events, ended):
    rec = OutcomeRecorder()
    for e in events:
        rec.note(e)
    assert rec.block()["ended"] == ended


def test_a_crash_wins_and_keeps_only_the_type_name():
    rec = OutcomeRecorder()
    rec.note({"type": "result", "subtype": "success"})
    rec.crashed(RuntimeError("secret detail"))
    b = rec.block()
    assert b["ended"] == "crashed" and b["exception"] == "RuntimeError"
    assert "secret" not in str(b)


def test_block_fields_are_clipped_and_typed():
    rec = OutcomeRecorder()
    rec.note({"type": "error", "code": "C" * 200, "message": "m" * 1000})
    b = rec.block()
    assert len(b["code"]) == 64 and len(b["message"]) == 300
    rec = OutcomeRecorder()
    rec.note({"type": "result", "subtype": "success", "duration_ms": 1200, "num_turns": 3,
              "total_cost_usd": 0.5, "usage": {"input_tokens": 10, "output_tokens": "x"}}, pid=42)
    b = rec.block(repos=[])
    assert (b["duration_ms"], b["num_turns"], b["cli_cost_total"], b["cli_pid"]) == (1200, 3, 0.5, 42)
    assert b["usage"]["input_tokens"] == 10 and b["usage"]["output_tokens"] is None
    assert b["repos"] == []


def _o(total, pid, ended="completed"):
    return {"ended": ended, "cli_cost_total": total, "cli_pid": pid}


def test_costs_are_increases_of_one_process_and_restart_with_a_new_one():
    costs, short = turn_costs([_o(1.0, 7), _o(1.5, 7), _o(0.2, 8), _o(0.1, 8), _o(0.4, 9)])
    assert costs == [1.0, 0.5, 0.2, 0.1, 0.4] and short is False


def test_a_missing_total_is_covered_by_the_next_total_of_the_same_process():
    costs, short = turn_costs([_o(1.0, 7), _o(None, None), _o(2.0, 7)])
    assert sum(c for c in costs if c) == 2.0 and short is False


def test_a_missing_total_not_covered_is_flagged():
    costs, short = turn_costs([_o(1.0, 7), _o(None, None)])
    assert short is True
    _, short = turn_costs([_o(1.0, 7), _o(None, None, ended="paused")])
    assert short is False


# ── the real turn path ────────────────────────────────────────────────────────

def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _send(tail: list[dict], raise_at_end: bool = False):
    async def send(**kwargs: Any):
        sid = kwargs.get("app_session_id")
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "model": "test", "content": [{"type": "text", "text": "working"}]}
        for ev in tail:
            yield ev
        if raise_at_end:
            raise RuntimeError("the CLI died")
    return send


async def _turn(router, sid, send, client_id=None):
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for _ in router.run_conversation(agent_id="default", user_message="go",
                                               session_id=sid, client_id=client_id or f"local-{sid}"):
            pass


async def _outcomes(sid: str) -> list[dict]:
    from database import db
    rows = await db.messages.list_by_session(sid, include_pending=True)
    return [b for r in rows for b in r["content"] if b.get("type") == "turn_outcome"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,tail,crash,ended", [
    ("done", [{"type": "result", "subtype": "success", "duration_ms": 900,
               "total_cost_usd": 0.25, "num_turns": 2}], False, "completed"),
    ("err", [{"type": "error", "code": "MODEL_ERROR", "message": "throttled"}], False, "error"),
    ("limit", [{"type": "result", "subtype": "turn_limit_reached", "is_error": True}], False, "turn_limit"),
    ("crash", [], True, "crashed"),
    ("ask", [{"type": "ask_user_question", "toolUseId": "toolu_q",
              "questions": [{"question": "which?"}]}], False, "paused"),
    ("none", [], False, "none"),
])
async def test_every_started_turn_writes_one_outcome(name, tail, crash, ended):
    sid = f"s-to-{name}"
    try:
        await _turn(_router(), sid, _send(tail, crash))
    except RuntimeError:
        assert crash
    got = await _outcomes(sid)
    assert len(got) == 1, got
    assert got[0]["ended"] == ended
    if name == "done":
        assert (got[0]["duration_ms"], got[0]["cli_cost_total"], got[0]["num_turns"]) == (900, 0.25, 2)
    if name == "err":
        assert got[0]["code"] == "MODEL_ERROR"


@pytest.mark.asyncio
async def test_a_turn_that_never_started_writes_nothing():
    router = _router()
    sid = "s-to-never"
    writer = TurnWriter(router, sid, row_key="never-asst")

    async def agen():
        yield {"type": "error", "code": "SESSION_BUSY", "_post_only": True}

    async for _ in router._published_turn(agen(), session_id=sid, turn_client_id="never-asst",
                                          source="chat", start_on_session_start=True,
                                          writer=writer):
        pass
    assert await _outcomes(sid) == []
    from database import db
    assert await db.messages.list_by_session(sid, include_pending=True) == []


@pytest.mark.asyncio
async def test_an_outcome_write_failure_does_not_break_the_turn():
    sid = "s-to-fail"
    with patch.object(TurnWriter, "record_outcome", side_effect=OSError("disk")):
        await _turn(_router(), sid, _send([{"type": "result", "subtype": "success"}]))
    assert await _outcomes(sid) == []


def test_the_outcome_stays_out_of_resume_history():
    msgs = [{"role": "assistant", "content": [{"type": "turn_outcome", "ended": "completed"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    assert _filter_tool_only_messages(msgs) == [msgs[1]]


@pytest.mark.asyncio
async def test_a_declared_repository_is_reported_in_the_outcome(tmp_path, monkeypatch):
    from database import db
    from core import project_registry
    sid = "s-to-repos"
    await core.session_manager.session_manager.store_session(sid, "default", "t")
    await routers.chat._record_task_repos(sid, [{"url": "https://github.com/acme/widget", "ref": "main",
                                                 "dir": "Hive/repos/github.com/acme/widget"}])
    assert (await db.sessions.get(sid))["metadata"]["task_repos"][0]["ref"] == "main"
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path)
    await _turn(_router(), sid, _send([{"type": "result", "subtype": "success"}]))
    got = await _outcomes(sid)
    assert len(got) == 1 and got[0]["repos"][0]["dir"] == "Hive/repos/github.com/acme/widget"
    assert got[0]["repos"][0]["present"] is False


@pytest.mark.asyncio
async def test_repositories_are_read_from_the_sessions_start(tmp_path, monkeypatch):
    from database import db
    from core import project_registry, task_repos
    from datetime import datetime
    sid = "s-to-since"
    await core.session_manager.session_manager.store_session(sid, "default", "t")
    await routers.chat._record_task_repos(sid, [{"url": "https://github.com/acme/widget", "ref": "main",
                                                 "dir": "Hive/repos/github.com/acme/widget"}])
    started = datetime.fromisoformat((await db.sessions.get(sid))["created_at"]).timestamp()
    seen = []

    def facts(ws, repos, since=None):
        seen.append(since)
        return []
    monkeypatch.setattr(task_repos, "repo_facts", facts)
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path)
    await _turn(_router(), sid, _send([{"type": "result", "subtype": "success"}]))
    assert seen == [started]


@pytest.mark.asyncio
async def test_a_turn_without_declared_repositories_has_no_repos_field():
    sid = "s-to-norepos"
    await _turn(_router(), sid, _send([{"type": "result", "subtype": "success"}]))
    assert "repos" not in (await _outcomes(sid))[0]


@pytest.mark.asyncio
async def test_the_chat_stream_refuses_unaccepted_repositories_before_the_turn():
    class Req:
        async def json(self):
            return {"agent_id": "default", "message": "x", "task_repos": [{"url": "file:///etc"}]}
    with patch.object(routers.chat, "_get_router") as gr:
        with pytest.raises(Exception) as ei:
            await routers.chat.chat_stream(Req())
    assert "repositor" in str(ei.value.detail if hasattr(ei.value, "detail") else ei.value).lower() \
        or "Invalid task repositories" in str(getattr(ei.value, "message", ""))
    gr.return_value.run_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_recording_repositories_keeps_the_sessions_other_metadata():
    from database import db
    sid = "s-to-meta"
    await core.session_manager.session_manager.store_session(sid, "default", "t")
    await db.sessions.update(sid, {"metadata": {"keep": 1}})
    await routers.chat._record_task_repos(sid, [{"url": "https://github.com/acme/w", "ref": None,
                                                 "dir": "Hive/repos/github.com/acme/w"}])
    meta = (await db.sessions.get(sid))["metadata"]
    assert meta["keep"] == 1 and meta["task_repos"][0]["url"] == "https://github.com/acme/w"


def test_a_crashed_or_stopped_turn_without_a_total_is_flagged_unless_covered():
    _, short = turn_costs([_o(1.0, 7), _o(None, None, ended="crashed"), _o(0.2, 8)])
    assert short is True
    _, short = turn_costs([_o(1.0, 7), _o(None, None, ended="none")])
    assert short is True
    _, short = turn_costs([_o(1.0, 7), _o(None, None, ended="none"), _o(1.4, 7)])
    assert short is False                               # the same process's next total covers it
    # a new process after the gap: its later totals never bring the lost spend back
    _, short = turn_costs([_o(1.0, 7), _o(None, None, ended="crashed"), _o(0.3, 8), _o(0.5, 8)])
    assert short is True
    _, short = turn_costs([_o(None, None, ended="none"), _o(0.3, 8), _o(0.5, 8)])
    assert short is True
    costs, _ = turn_costs([_o(float("inf"), 7)])
    assert costs == [None]


@pytest.mark.asyncio
async def test_a_cancel_during_the_outcome_write_still_ends_the_turn_and_frees_the_session():
    router = _router()
    sid = "s-to-cancel"
    calls = []

    async def cancelled(self, writer, session_id, outcome):
        calls.append(session_id)
        raise asyncio.CancelledError()
    with patch.object(SessionRouter, "_write_turn_outcome", cancelled):
        with pytest.raises(asyncio.CancelledError):
            await _turn(router, sid, _send([{"type": "result", "subtype": "success"}]))
    assert len(calls) == 2          # the normal write, then the retry on the way out
    # the session is not left claimed: the next turn runs and records its outcome
    await asyncio.wait_for(_turn(router, sid, _send([{"type": "result", "subtype": "success"}]),
                                 client_id="local-second"), 20)
    assert [o["ended"] for o in await _outcomes(sid)] == ["completed"]


@pytest.mark.asyncio
async def test_the_writer_records_one_outcome_per_turn():
    router = _router()
    sid = "s-to-once"
    await core.session_manager.session_manager.store_session(sid, "default", "t")
    writer = TurnWriter(router, sid, row_key="once-asst")
    writer.begin(sid)
    assert await writer.record_outcome({"type": "turn_outcome", "ended": "completed"}) is True
    assert await writer.record_outcome({"type": "turn_outcome", "ended": "crashed"}) is False
    await writer.close()
    assert [o["ended"] for o in await _outcomes(sid)] == ["completed"]


@pytest.mark.asyncio
async def test_a_cancel_after_the_outcome_was_written_never_doubles_it():
    from database import db
    router = _router()
    sid = "s-to-late-cancel"
    await core.session_manager.session_manager.store_session(sid, "default", "t")
    writer = TurnWriter(router, sid, row_key="late-asst")
    writer.begin(sid)
    real = db.messages.append_to_turn_row

    async def written_then_cancelled(*a, **k):
        await real(*a, **k)
        await router._persist_assistant_blocks(sid, a[2], None, label="t", client_id="late-asst")
        raise asyncio.CancelledError()
    with patch.object(db.messages, "append_to_turn_row", side_effect=written_then_cancelled):
        with pytest.raises(asyncio.CancelledError):
            await writer.record_outcome({"type": "turn_outcome", "ended": "completed"})
    assert await writer.record_outcome({"type": "turn_outcome", "ended": "completed"}) is False
    await writer.close()
    assert len(await _outcomes(sid)) == 1
