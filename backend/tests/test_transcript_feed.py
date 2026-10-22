"""core/transcript_feed + core/question_prompt: what other tabs learn, and when.

Real path (conftest SQLite, real SessionRouter, the hub patched to a fresh one):
- a user message is published as a ``transcript_row`` (outside any turn), before
  its turn starts; the turn's own rows are not (its events carry them);
- a permission decision recorded on the stored prompt is published with it;
- a Stop receipt is published;
- a compaction is published as its ``compact-<id>`` system row;
- a removed row is published as ``transcript_delete``;
- every envelope's rev matches ``db.transcript_rev`` at that point.
R2: an ``ask_user_question`` is stored with its turn; the answer is recorded onto
it before the continuation runs, and both reload; the block stays out of the
cold-resume history.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.session_manager  # noqa: F401
import routers.chat  # noqa: F401
from core import session_events
from core.session_router import SessionRouter
from core.session_unit import SessionState


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


def _gen(*events):
    def send(**kwargs: Any):
        async def g():
            sid = kwargs.get("app_session_id")
            yield {"type": "session_start", "sessionId": sid}
            for e in events:
                yield dict(e, sessionId=sid) if e.get("type") == "result" else dict(e)
        return g()
    return send


async def _turn(r, sid, cid, send):
    unit = r.get_or_create_unit(sid, "default")
    unit._client = object()
    if unit.state != SessionState.IDLE:
        unit._transition(SessionState.IDLE)
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for _ in r.run_conversation(agent_id="default", user_message="hi", session_id=sid, client_id=cid):
            pass
    return unit


def _feed(hub, sid):
    return [e for e in hub._logs[sid].buf]


@pytest.fixture
def hub():
    h = session_events.SessionEventHub()
    with patch.object(session_events, "_hub", h):
        yield h


@pytest.mark.asyncio
async def test_a_user_message_is_published_before_its_turn_and_turn_rows_are_not(hub):
    sid = "s-feed-1"
    await _turn(_router(), sid, "local-f1", _gen(
        {"type": "assistant", "content": [{"type": "text", "text": "ok"}], "model": "m"},
        {"type": "result"}))
    types = [(e["type"], (e.get("row") or {}).get("role")) for e in _feed(hub, sid)]
    rows = [t for t in types if t[0] == "transcript_row"]
    assert rows == [("transcript_row", "user")]
    assert types.index(("transcript_row", "user")) < types.index(("turn_start", None))
    user = next(e for e in _feed(hub, sid) if e["type"] == "transcript_row")
    assert user["turnId"] == "" and user["row"]["metadata"] == {"client_id": "local-f1"}


@pytest.mark.asyncio
async def test_decision_stop_compaction_and_removal_are_published_with_their_rev(hub):
    from database import db
    from core.compact_summary import build_record
    from core.permission_prompt import record_decision
    sid = "s-feed-2"
    r = _router()
    unit = await _turn(r, sid, "local-f2", _gen(
        {"type": "cmd_permission_request", "requestId": "perm_f", "toolName": "Bash",
         "toolInput": {"command": "make clean"}, "reason": "x", "options": ["approve", "deny"]}))
    await record_decision(sid, "perm_f", "approve")
    last = _feed(hub, sid)[-1]
    assert last["type"] == "transcript_row" and last["rev"] == await db.transcript_rev(sid)
    assert any(b.get("decision") == "approve" for b in last["row"]["content"])
    unit._transition(SessionState.STREAMING)
    receipt = await r._persist_stop_receipt(sid, unit)
    assert receipt and _feed(hub, sid)[-1]["row"]["content"][0]["type"] == "turn_stop"
    await db.insert_compaction(sid, {**build_record({"trigger": "auto"}, "sum"), "id": "cf"})
    comp = _feed(hub, sid)[-1]
    assert comp["row"]["id"] == "compact-cf" and comp["row"]["role"] == "system"
    user_id = next(e["row"]["id"] for e in _feed(hub, sid) if e.get("row", {}).get("role") == "user")
    await db.messages.delete(user_id)
    gone = _feed(hub, sid)[-1]
    assert gone == {**gone, "type": "transcript_delete", "rowId": user_id, "rev": await db.transcript_rev(sid)}


@pytest.mark.asyncio
async def test_a_question_and_its_answer_are_stored(hub):
    from core.context_injector import _TOOL_ONLY_TYPES
    sid = "s-q-1"
    r = _router()
    q = {"type": "ask_user_question", "toolUseId": "toolu_q", "questions": [
        {"question": "Which one?", "header": "Pick", "multiSelect": False,
         "options": [{"label": "A", "description": "a"}]}]}
    unit = await _turn(r, sid, "local-q1", _gen(q))
    unit._transition(SessionState.STREAMING)
    unit._transition(SessionState.WAITING_INPUT)

    async def cont(*_a, **_k):
        yield {"type": "assistant", "content": [{"type": "text", "text": "took A"}], "model": "m"}
        yield {"type": "result", "sessionId": sid}
    unit.continue_with_answer = cont
    async for _ in r.continue_with_answer(sid, json.dumps({"Which one?": "A"}), tool_use_id="toolu_q"):
        pass
    import routers.chat as chat_router
    import database
    req = MagicMock()
    req.headers = {}
    with patch.object(chat_router, "db", database.db), \
            patch.object(chat_router, "_get_router", lambda: r):
        resp = await chat_router.get_session_messages(sid, req, limit=None, before_id=None, turns=None)
    blocks = [b for m in json.loads(resp.body) for b in m["content"]]
    qb = [b for b in blocks if b.get("type") == "ask_user_question"]
    assert qb == [{**q, "answers": {"Which one?": "A"}}]
    assert int(resp.headers["X-Transcript-Rev"]) > 0
    assert "ask_user_question" in _TOOL_ONLY_TYPES
    answered = [e for e in _feed(hub, sid) if e["type"] == "transcript_row"
                and any(b.get("answers") for b in e["row"]["content"])]
    assert answered, "the answer reaches other tabs"


@pytest.mark.asyncio
async def test_since_rev_endpoint_returns_the_changes_after_a_rev(hub):
    from database import db
    import routers.chat as chat_router
    import database
    sid = "s-since-1"
    await _turn(_router(), sid, "local-s1", _gen(
        {"type": "assistant", "content": [{"type": "text", "text": "one"}], "model": "m"}, {"type": "result"}))
    r0 = await db.transcript_rev(sid)
    await _turn(_router(), sid, "local-s2", _gen(
        {"type": "assistant", "content": [{"type": "text", "text": "two"}], "model": "m"}, {"type": "result"}))
    with patch.object(chat_router, "db", database.db):
        out = await chat_router.get_transcript_since(sid, since_rev=r0)
    assert out["rev"] == await db.transcript_rev(sid) and out["reset"] is False
    assert [(x["row"]["role"], x["row"]["content"][0].get("text")) for x in out["rows"]] == [
        ("user", "hi"), ("assistant", "two")]


def test_history_merge_ends_a_bubble_at_a_compaction_between_turns():
    """R9: a CLI turn after a divider is its own bubble, below the divider, as the
    live chat shows it; within one turn the bubble continues across a divider."""
    from routers.chat import _merge_consecutive_assistant_messages as merge
    md = lambda c: {"client_id": c}
    rows = [
        {"id": "u", "role": "user", "content": [], "created_at": "1", "metadata": md("local-a")},
        {"id": "a1", "role": "assistant", "content": [{"type": "text", "text": "x"}], "created_at": "2",
         "metadata": md("local-a-asst")},
        {"id": "a2", "role": "assistant", "content": [{"type": "text", "text": "y"}], "created_at": "4",
         "metadata": md("local-a-asst")},
        {"id": "i1", "role": "assistant", "content": [{"type": "text", "text": "z"}], "created_at": "6",
         "metadata": md("injected-q-asst")},
    ]
    assert [m["id"] for m in merge(rows, ["3"])] == ["u", "a1"]            # inside one turn: one bubble
    assert [m["id"] for m in merge(rows, ["5"])] == ["u", "a1", "i1"]      # a1+a2, then i1 after the divider
    assert [m["id"] for m in merge(rows, [])] == ["u", "a1"]               # no divider: one bubble


@pytest.mark.asyncio
async def test_a_state_change_pushes_the_session_state(hub):
    """S3: a unit's state change is published as `session_state` (outside any
    turn), built by the same producer the streaming-state endpoint uses."""
    import asyncio
    from core.session_state import session_state_entry
    sid = "s-state-1"
    r = _router()
    unit = r.get_or_create_unit(sid, "default")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    for _ in range(100):                        # the push reads the DB off the loop
        await asyncio.sleep(0.01)
        log = hub._logs.get(sid)
        if log and sum(e["type"] == "session_state" for e in log.buf) >= 2:
            break
    states = [e for e in hub._logs[sid].buf if e["type"] == "session_state"]
    assert states and states[-1]["turnId"] == ""
    assert states[-1]["state"] == await session_state_entry(unit)
    assert states[-1]["state"]["streaming"] is True
