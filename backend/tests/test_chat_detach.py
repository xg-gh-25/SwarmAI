"""Tests for the chat SSE layer once a turn belongs to its session (routers/chat.py).

- A client disconnect DETACHES the turn: the turn's generator keeps running to its
  own end, nothing interrupts or re-drives it, and the detached consumer stops
  queueing (I2, F9).
- The disconnect interrupt and core/auto_continue are gone; /chat/stop still
  interrupts a detached turn through the unit.
- GET /api/chat/events streams the session hub: hello, replay after a cursor,
  live events, heartbeats; malformed cursors subscribe from now; a client
  disconnect ends the subscription (it is not detached).
"""
from __future__ import annotations

import asyncio
import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import session_events
from routers import chat


class _Req:
    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone


def _data(chunk: str):
    assert chunk.startswith("data: "), chunk
    body = chunk[len("data: "):].strip()
    return body if body == "[DONE]" else json.loads(body)


async def test_disconnect_detaches_and_the_turn_runs_to_its_end(monkeypatch):
    finished = asyncio.Event()
    produced = []
    gate = asyncio.Event()

    async def turn():
        try:
            yield {"type": "text_delta", "text": "a"}
            await gate.wait()
            for i in range(50):
                produced.append(i)
                yield {"type": "text_delta", "text": str(i)}
                await asyncio.sleep(0)
            yield {"type": "result"}
        finally:
            finished.set()

    req = _Req()
    stream = chat.sse_with_heartbeat(turn(), heartbeat_interval=0.05, request=req)
    first = await stream.__anext__()
    assert _data(first)["text"] == "a"
    req.gone = True
    with pytest.raises(StopAsyncIteration):
        while True:
            await asyncio.wait_for(stream.__anext__(), 1.0)
    assert not finished.is_set()          # the turn was not cancelled
    assert len(chat._DETACHED_TURNS) == 1  # held by a strong reference
    gate.set()
    await asyncio.wait_for(finished.wait(), 2.0)
    assert produced == list(range(50))
    await asyncio.sleep(0)
    assert len(chat._DETACHED_TURNS) == 0


async def test_detached_consumer_stops_queueing(monkeypatch):
    puts_after_detach = []

    class CountingQueue(asyncio.Queue):
        detached = False

        async def put(self, item):
            if CountingQueue.detached:
                puts_after_detach.append(item)
            await super().put(item)

    monkeypatch.setattr(chat.asyncio, "Queue", CountingQueue)
    gate = asyncio.Event()
    done = asyncio.Event()

    async def turn():
        yield {"type": "text_delta", "text": "a"}
        await gate.wait()
        for i in range(20):
            yield {"type": "text_delta", "text": str(i)}
        done.set()

    req = _Req()
    stream = chat.sse_with_heartbeat(turn(), heartbeat_interval=0.05, request=req)
    await stream.__anext__()
    req.gone = True
    with pytest.raises(StopAsyncIteration):
        while True:
            await asyncio.wait_for(stream.__anext__(), 1.0)
    CountingQueue.detached = True
    gate.set()
    await asyncio.wait_for(done.wait(), 2.0)
    await asyncio.sleep(0.01)
    assert [i for i in puts_after_detach if i[0] == "message"] == []


async def test_stop_event_also_detaches_instead_of_cancelling():
    stop = asyncio.Event()
    finished = asyncio.Event()

    async def turn():
        try:
            yield {"type": "text_delta", "text": "a"}
            await asyncio.sleep(0.2)
            yield {"type": "result"}
        finally:
            finished.set()

    stream = chat.sse_with_heartbeat(turn(), heartbeat_interval=0.05, request=_Req(), stop_event=stop)
    await stream.__anext__()
    stop.set()
    with pytest.raises(StopAsyncIteration):
        while True:
            await asyncio.wait_for(stream.__anext__(), 1.0)
    await asyncio.wait_for(finished.wait(), 2.0)


def test_disconnect_interrupt_and_auto_continue_are_gone():
    assert not hasattr(chat, "_recover_streaming_on_disconnect")
    assert not hasattr(chat, "_cleanup_subprocess_after_disconnect")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.auto_continue")


async def test_stop_interrupts_a_detached_turn_through_the_unit(monkeypatch):
    router = MagicMock()
    router.get_unit.return_value = MagicMock()
    router.interrupt_session = AsyncMock(return_value={"success": True, "message": "Interrupted"})
    monkeypatch.setattr(chat, "_get_router", lambda: router)
    out = await chat.stop_session("S1")
    router.interrupt_session.assert_awaited_once_with("S1")
    assert out["status"] == "stopped"


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub(buffer_size=50, queue_size=50)
    monkeypatch.setattr(session_events, "_hub", h)
    return h


async def _events_body(req, **params):
    resp = await chat.session_events_stream(req, **params)
    assert resp.media_type == "text/event-stream"
    return resp.body_iterator


async def test_events_endpoint_hello_replay_live(hub, monkeypatch):
    monkeypatch.setattr(chat, "EVENTS_HEARTBEAT_INTERVAL", 10)
    hub.publish("A", {"type": "text_delta", "text": "1"}, turn_id="t")
    hub.publish("A", {"type": "text_delta", "text": "2"}, turn_id="t")
    req = _Req()
    body = await _events_body(req, cursors=json.dumps({"A": 1}), epoch=hub.epoch)
    hello = _data(await body.__anext__())
    assert hello["type"] == "events_hello" and hello["epoch"] == hub.epoch
    replay = _data(await body.__anext__())
    assert replay["eventSeq"] == 2
    hub.publish("A", {"type": "text_delta", "text": "3"}, turn_id="t")
    live = _data(await asyncio.wait_for(body.__anext__(), 1.0))
    assert live["eventSeq"] == 3
    await body.aclose()
    assert hub.subscriber_count() == 0


async def test_events_endpoint_heartbeat_and_disconnect_ends_it(hub, monkeypatch):
    monkeypatch.setattr(chat, "EVENTS_HEARTBEAT_INTERVAL", 0.05)
    req = _Req()
    body = await _events_body(req, cursors=None, epoch=None)
    await body.__anext__()  # hello
    beat = _data(await asyncio.wait_for(body.__anext__(), 1.0))
    assert beat["type"] == "heartbeat"
    req.gone = True
    with pytest.raises(StopAsyncIteration):
        while True:
            await asyncio.wait_for(body.__anext__(), 1.0)
    await asyncio.sleep(0)
    assert hub.subscriber_count() == 0


async def test_events_endpoint_malformed_cursor_subscribes_from_now(hub, monkeypatch):
    monkeypatch.setattr(chat, "EVENTS_HEARTBEAT_INTERVAL", 10)
    hub.publish("A", {"type": "text_delta", "text": "old"}, turn_id="t")
    body = await _events_body(_Req(), cursors="{not json", epoch=hub.epoch)
    await body.__anext__()  # hello
    hub.publish("A", {"type": "text_delta", "text": "new"}, turn_id="t")
    ev = _data(await asyncio.wait_for(body.__anext__(), 1.0))
    assert ev["text"] == "new"
    await body.aclose()


async def test_response_closed_mid_turn_detaches_too():
    # Starlette closes the body iterator when the client goes away mid-write;
    # that close must detach the turn, not cancel it
    finished = asyncio.Event()

    async def turn():
        try:
            yield {"type": "text_delta", "text": "a"}
            await asyncio.sleep(0.1)
            yield {"type": "result"}
        finally:
            finished.set()

    stream = chat.sse_with_heartbeat(turn(), heartbeat_interval=5, request=_Req())
    await stream.__anext__()
    await stream.aclose()
    assert not finished.is_set()
    await asyncio.wait_for(finished.wait(), 2.0)

