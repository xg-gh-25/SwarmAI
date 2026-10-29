"""An answer or a permission decision reaches only the session it belongs to
(routers/chat.py), and streaming-state lists a relayed Hive session.

Questions: is a decision for a request of another session refused before anything is
delivered (R6.1), and an answer to a question on another session's board (R6.2), while
the same calls for the right session still go through; does streaming-state list a
session while its relay is registered (R5.1)?
"""
import asyncio
import json

import pytest

from core.permission_manager import permission_manager as pm
from core import session_events, swarm_out_relay
from routers import chat as chat_router


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

    async def is_disconnected(self):
        return False


class FakeRouter:
    def __init__(self):
        self.answers = []
        self.decisions = []

    def get_unit(self, sid):
        return None

    def list_units(self):
        return []

    async def continue_with_answer(self, session_id, answer, tool_use_id):
        self.answers.append((session_id, tool_use_id))
        yield {"type": "result"}

    async def continue_with_cmd_permission(self, session_id, request_id, allowed, **kw):
        self.decisions.append((session_id, request_id, allowed))
        yield {"type": "result"}


@pytest.fixture
def router(monkeypatch):
    r = FakeRouter()
    monkeypatch.setattr(chat_router, "_get_router", lambda: r)

    async def yes(agent_id):
        return True
    monkeypatch.setattr(chat_router, "agent_exists", yes)
    return r


async def _drain(resp):
    out = []
    async for chunk in resp.body_iterator:
        out.append(chunk if isinstance(chunk, str) else chunk.decode())
    return "".join(out)


def test_an_answer_for_another_sessions_question_is_refused(router):
    pm._prompts.setdefault("sess-A", {})["tool-1"] = {"kind": "ask_user_question", "tool_use_id": "tool-1"}
    try:
        body = {"agent_id": "default", "session_id": "sess-B", "tool_use_id": "tool-1", "answers": {"q": "a"}}
        text = asyncio.run(_drain(asyncio.run(chat_router.answer_question(FakeRequest(body)))))
        assert "ANSWER_WRONG_SESSION" in text and router.answers == []
        body["session_id"] = "sess-A"
        asyncio.run(_drain(asyncio.run(chat_router.answer_question(FakeRequest(body)))))
        assert router.answers == [("sess-A", "tool-1")]
    finally:
        pm._prompts.pop("sess-A", None)


def test_a_decision_for_another_sessions_request_is_refused(router, monkeypatch):
    monkeypatch.setattr(pm, "get_pending_request", lambda rid: {"id": rid, "session_id": "sess-A"})

    class Session:
        agent_id = "default"

    async def get_session(sid):
        return Session()
    monkeypatch.setattr(chat_router.session_manager, "get_session", get_session)
    body = {"session_id": "sess-B", "request_id": "req-1", "decision": "approve"}
    with pytest.raises(Exception) as e:
        asyncio.run(chat_router.cmd_permission_continue(FakeRequest(body)))
    assert "another session" in json.dumps(getattr(e.value, "detail", str(e.value)))
    assert router.decisions == []
    body["session_id"] = "sess-A"
    asyncio.run(_drain(asyncio.run(chat_router.cmd_permission_continue(FakeRequest(body)))))
    assert router.decisions == [("sess-A", "req-1", True)]


def test_streaming_state_lists_a_relayed_session(router, monkeypatch):
    monkeypatch.setattr(session_events, "_hub", session_events.SessionEventHub())
    monkeypatch.setattr(swarm_out_relay, "_manager", None)
    m = swarm_out_relay.manager()
    m._relays["hive-s"] = swarm_out_relay._Relay("hive-s", {}, {"streaming": True, "state": "streaming"})
    out = asyncio.run(chat_router.get_streaming_state_endpoint())["sessions"]
    assert out["hive-s"]["streaming"] is True and "relay_connected" in out["hive-s"]
