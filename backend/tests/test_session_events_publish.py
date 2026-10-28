"""Tests that every router turn publishes to the session event hub.

The router's four turn entry points (run_conversation, continue_with_answer,
continue_with_cmd_permission, run_injected_turn) go through one wrapper,
SessionRouter._published_turn. These tests check what a subscribed tab relies on:

- turn_start, every event, turn_end, with strictly increasing eventSeq;
- run_conversation publishes from the turn's session_start (pre-turn replies such as
  SESSION_BUSY stay POST-only, without a turnId);
- the caller receives the same envelopes (so the POST stream can bind its turnId);
- a drained turn gets a server client_id, so its rows and its live bubble share a key;
- closing the wrapper early closes the inner generator (its finally runs) and still
  publishes turn_end (I4 / F7);
- a CLI-initiated turn publishes its events after persisting them.
"""
from __future__ import annotations

from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import session_events
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub(buffer_size=100, queue_size=100)
    monkeypatch.setattr(session_events, "_hub", h)
    return h


def _router() -> SessionRouter:
    return SessionRouter(prompt_builder=MagicMock())


def _published(h, sid):
    """The turn envelopes published for sid. Envelopes outside any turn (turnId "":
    session_state, session_activity — docs/design/chat-turn-liveness.md S1) share
    the session's eventSeq but are not part of the turn under test."""
    log = h._logs.get(sid)
    return [e for e in log.buf if e.get("turnId")] if log else []


async def _fake_impl(events, closed=None):
    try:
        for e in events:
            yield e
    finally:
        if closed is not None:
            closed.append(True)


async def test_run_conversation_publishes_from_session_start(hub, monkeypatch):
    r = _router()
    events = [
        {"type": "turn_phase", "phase": "preparing"},
        {"type": "session_start", "sessionId": "S1"},
        {"type": "text_delta", "text": "hi"},
        {"type": "result", "session_id": "S1"},
    ]
    seen_kwargs = {}

    def impl(**kw):
        seen_kwargs.update(kw)
        return _fake_impl(events)

    monkeypatch.setattr(r, "_run_conversation_impl", impl)
    got = [e async for e in r.run_conversation(agent_id="a", user_message="x",
                                               session_id="S1", client_id="local-c1")]
    pub = _published(hub, "S1")
    # a started turn ends with its turn_outcome block (core/turn_outcome) before turn_end
    assert [e["type"] for e in pub] == ["turn_start", "session_start", "text_delta", "result",
                                        "assistant", "turn_end"]
    assert pub[-2]["content"][0]["type"] == "turn_outcome"
    seqs = [e["eventSeq"] for e in pub]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1   # strictly increasing
    assert len({e["turnId"] for e in pub}) == 1
    assert all(e["turnClientId"] == "local-c1-asst" for e in pub)
    # the caller gets the pre-turn event unstamped and the turn events stamped
    assert "turnId" not in got[0]
    assert [e["type"] for e in got[1:]] == ["turn_start", "session_start", "text_delta", "result", "assistant"]
    assert all("eventSeq" in e for e in got[1:])
    assert seen_kwargs["client_id"] == "local-c1"


async def test_rejected_send_publishes_nothing(hub, monkeypatch):
    r = _router()
    busy = {"type": "error", "code": "SESSION_BUSY", "message": "busy"}
    monkeypatch.setattr(r, "_run_conversation_impl", lambda **kw: _fake_impl([busy]))
    got = [e async for e in r.run_conversation(agent_id="a", user_message="x", session_id="S1")]
    assert got == [busy]
    assert _published(hub, "S1") == []


async def test_new_session_id_learned_from_session_start(hub, monkeypatch):
    r = _router()
    events = [{"type": "session_start", "sessionId": "NEW"}, {"type": "result"}]
    monkeypatch.setattr(r, "_run_conversation_impl", lambda **kw: _fake_impl(events))
    [e async for e in r.run_conversation(agent_id="a", user_message="x", session_id=None)]
    assert [e["type"] for e in _published(hub, "NEW")] == ["turn_start", "session_start", "result",
                                                                "assistant", "turn_end"]


async def test_drained_turn_gets_a_server_client_id(hub, monkeypatch):
    r = _router()
    seen = {}

    def impl(**kw):
        seen.update(kw)
        return _fake_impl([{"type": "session_start", "sessionId": "S1"}])

    monkeypatch.setattr(r, "_run_conversation_impl", impl)
    [e async for e in r.run_conversation(agent_id="a", user_message="x", session_id="S1",
                                         _drained_pending=True)]
    assert seen["client_id"].startswith("drain-")
    assert _published(hub, "S1")[0]["turnClientId"] == f"{seen['client_id']}-asst"


async def test_early_close_closes_inner_and_publishes_turn_end(hub, monkeypatch):
    r = _router()
    closed: list[bool] = []
    events = [{"type": "session_start", "sessionId": "S1"},
              {"type": "text_delta", "text": "a"}, {"type": "text_delta", "text": "b"}]
    monkeypatch.setattr(r, "_run_conversation_impl", lambda **kw: _fake_impl(events, closed))
    async with aclosing(r.run_conversation(agent_id="a", user_message="x", session_id="S1")) as g:
        async for e in g:
            if e.get("type") == "session_start":
                break
    assert closed == [True]
    assert _published(hub, "S1")[-1]["type"] == "turn_end"


async def test_inner_exception_still_publishes_turn_end(hub, monkeypatch):
    r = _router()

    async def boom(**kw):
        yield {"type": "session_start", "sessionId": "S1"}
        raise RuntimeError("x")

    monkeypatch.setattr(r, "_run_conversation_impl", boom)
    with pytest.raises(RuntimeError):
        [e async for e in r.run_conversation(agent_id="a", user_message="x", session_id="S1")]
    assert _published(hub, "S1")[-1]["type"] == "turn_end"


@pytest.mark.parametrize("method,unit_method,args", [
    ("continue_with_answer", "continue_with_answer", ("S1", "ans")),
    ("continue_with_cmd_permission", "continue_with_permission", ("S1", "rid", True)),
])
async def test_continuations_publish_every_event(hub, monkeypatch, method, unit_method, args):
    r = _router()
    unit = SimpleNamespace(_turn_client_id="local-c9", _turn_changes=None)
    setattr(unit, unit_method, lambda *a, **k: _fake_impl(
        [{"type": "text_delta", "text": "x"}, {"type": "result"}]))
    monkeypatch.setattr(r, "get_unit", lambda sid: unit)
    monkeypatch.setattr(r, "_persist_stream_event", AsyncMock(return_value=[]))
    monkeypatch.setattr(r, "_turn_files_of", lambda u: None)
    got = [e async for e in getattr(r, method)(*args)]
    pub = _published(hub, "S1")
    assert [e["type"] for e in pub] == ["turn_start", "text_delta", "result", "assistant", "turn_end"]
    assert all(e["turnClientId"] == "local-c9-asst" for e in pub)
    assert [e["type"] for e in got] == ["turn_start", "text_delta", "result", "assistant"]


async def test_missing_unit_continuation_publishes_nothing(hub, monkeypatch):
    r = _router()
    monkeypatch.setattr(r, "get_unit", lambda sid: None)
    got = [e async for e in r.continue_with_answer("S1", "ans")]
    assert got[0]["code"] == "SESSION_NOT_FOUND"
    assert _published(hub, "S1") == []


async def test_injected_turn_publishes_after_persisting(hub, monkeypatch):
    r = _router()
    order: list[str] = []
    unit = MagicMock()
    unit.session_id = "S1"
    unit.state = SessionState.IDLE
    unit._turn_changes = None

    async def frames():
        for e in ({"type": "session_start", "sessionId": "S1"}, {"type": "text_delta", "text": "z"},
                  {"type": "result"}):
            order.append(f"read:{e['type']}")
            yield e

    unit._read_formatted_response = frames
    # the real failure policy over the fake CLI read (core/session_unit.read_with_recovery)
    unit.read_with_recovery = lambda: SessionUnit.read_with_recovery(unit)

    async def persist(sid, event, pending, **kw):
        order.append(f"persist:{event['type']}")
        assert "eventSeq" not in event  # the raw event is persisted, not the envelope
        return []

    monkeypatch.setattr(r, "_persist_stream_event", persist)
    monkeypatch.setattr(r, "_turn_files_of", lambda u: None)
    real_publish = hub.publish

    def publish(sid, ev, **kw):
        order.append(f"publish:{ev['type']}")
        return real_publish(sid, ev, **kw)

    monkeypatch.setattr(hub, "publish", publish)
    await r.run_injected_turn(unit, object())
    pub = _published(hub, "S1")
    assert [e["type"] for e in pub] == ["turn_start", "session_start", "text_delta", "result",
                                        "assistant", "turn_end"]
    key = pub[0]["turnClientId"]
    assert key.startswith("injected-") and key.endswith("-asst")
    assert order.index("persist:text_delta") < order.index("publish:text_delta")


def test_session_release_frees_the_event_log_buffer(hub):
    from core.lifecycle_manager import LifecycleManager
    hub.publish("S9", {"type": "text_delta"}, turn_id="t")
    LifecycleManager._release_session_state("S9")
    assert _published(hub, "S9") == []
    assert hub.current_seq("S9") == 1


async def test_injected_turn_publishes_its_deliverables(hub, monkeypatch):
    r = _router()
    unit = MagicMock()
    unit.session_id = "S1"
    unit.state = SessionState.IDLE
    unit._turn_changes = None

    async def frames():
        yield {"type": "session_start", "sessionId": "S1"}
        yield {"type": "result"}

    unit._read_formatted_response = frames
    # the real failure policy over the fake CLI read (core/session_unit.read_with_recovery)
    unit.read_with_recovery = lambda: SessionUnit.read_with_recovery(unit)

    async def persist(sid, event, pending, **kw):
        if event["type"] == "result":
            return [{"type": "assistant", "content": [{"type": "turn_deliverables", "files": ["a.md"]}]}]
        return []

    monkeypatch.setattr(r, "_persist_stream_event", persist)
    monkeypatch.setattr(r, "_turn_files_of", lambda u: None)
    await r.run_injected_turn(unit, object())
    types = [(e["type"], (e.get("content") or [{}])[0].get("type")) for e in _published(hub, "S1")]
    assert ("assistant", "turn_deliverables") in types
    assert types.index(("assistant", "turn_deliverables")) < types.index(("result", None))

