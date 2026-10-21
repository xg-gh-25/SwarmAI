"""Layer-4 cross-boundary test: a CLI-initiated turn reaches a subscriber over HTTP.

Drives the real seam end to end, mocking only the far leaf (the CLI's frames):
SessionRouter.run_injected_turn → _published_turn → SessionEventHub → the real
GET /api/chat/events ASGI route (FastAPI + StreamingResponse) → SSE frames as the
desktop's consumeSSEStream parses them (`data: <json>`).

It also binds the cross-language contract the desktop router reads: the envelope
field names and the turn_start ``source`` values (sessionTurnRouter.ts TurnSource).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from core import session_events
from core.session_router import SessionRouter
from core.session_unit import SessionState
from routers import chat


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub(buffer_size=100, queue_size=100)
    monkeypatch.setattr(session_events, "_hub", h)
    monkeypatch.setattr(chat, "EVENTS_HEARTBEAT_INTERVAL", 10)
    return h


async def _sse_frames(app, path: str, want: int, act) -> list[dict]:
    """Call the ASGI app like a browser fetch, collect `want` data frames, then
    disconnect. `act` runs once the hello arrived (the subscriber is registered)."""
    frames: list[dict] = []
    started = asyncio.Event()
    hello = asyncio.Event()
    done = asyncio.Event()
    buf = ""

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(msg):
        nonlocal buf
        if msg["type"] == "http.response.start":
            assert msg["status"] == 200
            started.set()
        elif msg["type"] == "http.response.body":
            buf += msg.get("body", b"").decode()
            while "\n\n" in buf:
                chunk, buf = buf.split("\n\n", 1)
                if chunk.startswith("data: "):
                    frames.append(json.loads(chunk[len("data: "):]))
                    hello.set()
            if len(frames) >= want:
                done.set()

    path_only, _, qs = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path_only, "raw_path": path_only.encode(),
             "query_string": qs.encode(), "headers": [], "client": ("127.0.0.1", 1),
             "server": ("127.0.0.1", 18321)}
    task = asyncio.ensure_future(app(scope, receive, send))
    await asyncio.wait_for(started.wait(), 2.0)
    await asyncio.wait_for(hello.wait(), 2.0)
    await act()
    await asyncio.wait_for(done.wait(), 3.0)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return frames


async def test_cli_initiated_turn_reaches_the_events_subscriber_over_http(hub, monkeypatch):
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    r = SessionRouter(prompt_builder=MagicMock())
    monkeypatch.setattr(r, "_turn_files_of", lambda u: None)

    async def persist(*a, **k):
        return []

    monkeypatch.setattr(r, "_persist_stream_event", persist)
    unit = MagicMock()
    unit.session_id = "S1"
    unit.state = SessionState.IDLE
    unit._turn_changes = None

    async def frames():  # the far leaf: what the CLI sends for its own turn
        yield {"type": "session_start", "sessionId": "S1"}
        yield {"type": "assistant", "content": [{"type": "text", "text": "background job done"}]}
        yield {"type": "result", "session_id": "S1"}

    unit._read_formatted_response = frames

    async def act():
        await r.run_injected_turn(unit, object())

    got = await _sse_frames(app, "/api/chat/events", 6, act)
    assert got[0]["type"] == "events_hello"
    turn = got[1:]
    assert [f["type"] for f in turn] == ["turn_start", "session_start", "assistant", "result", "turn_end"]
    assert [f["eventSeq"] for f in turn] == [1, 2, 3, 4, 5]
    assert {f["sessionId"] for f in turn} == {"S1"} and len({f["turnId"] for f in turn}) == 1
    assert turn[0]["source"] == "injected" and turn[0]["turnClientId"].endswith("-asst")
    assert turn[2]["content"][0]["text"] == "background job done"


async def test_reconnect_with_cursor_replays_over_http(hub):
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    for i in range(1, 4):
        hub.publish("S1", {"type": "text_delta", "text": str(i)}, turn_id="t")
    cursors = json.dumps({"S1": 1})

    async def noop():
        return None

    got = await _sse_frames(app, f"/api/chat/events?cursors={cursors}&epoch={hub.epoch}", 3, noop)
    assert [f.get("eventSeq") for f in got[1:]] == [2, 3]


def test_envelope_and_source_contract_matches_the_desktop_router():
    env = session_events.SessionEventHub().publish("S", {"type": "x"}, turn_id="t", turn_client_id="c")
    ts = (Path(__file__).resolve().parents[2] / "desktop/src/hooks/sessionTurnRouter.ts").read_text()
    iface = ts[ts.index("export interface TurnEnvelope"):ts.index("}", ts.index("export interface TurnEnvelope"))]
    for key in ("sessionId", "turnId", "turnClientId", "eventSeq"):
        assert key in env, key
        assert re.search(rf"\b{key}\?:", iface), f"desktop TurnEnvelope lacks {key}"
    union = re.search(r"export type TurnSource =([^;]+);", ts).group(1)
    assert sorted(re.findall(r"'([a-z]+)'", union)) == sorted(session_events.TURN_SOURCES)
