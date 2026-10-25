"""D6: a steer the CLI runs as its own round is read as part of the SAME turn.

Design docs/design/2026-10-24-chat-steer.md D6 (T7, T12b, T13, T21, T24, T27,
T28 carry half, the TurnAbandoned path). Real StreamingOrchestrator.
_read_formatted_response over a real CliStreamReader fed SDK-parsed frames; the
parent unit is the leak-guard harness stub.

The reader is its own task and runs AHEAD of the turn: every test feeds all
frames before the turn reads, so the steer's round has already been opened (often
finished) by the time the turn reads its own Result -- the case a check of "a
steer claim still waiting" misses.

Each test fails with its D6 branch reverted.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.cli_stream_reader import CliStreamReader
from core.session_unit import SessionState
from tests.test_cli_stream_reader import FakeClient, _settle
from tests.test_cli_stream_reader_steer import (
    S1, U1, _echo, _init, _notification, _result, _state, _text, _tool_result, _tool_use,
)
from tests.test_tool_call_leak_guard import _make_orchestrator

ERR = "API Error: 503 Bedrock is unable to process your request. This is a server-side issue, usually temporary"


def _res(subtype="success", text="done", ms=1000, out=5, is_error=None):
    r = _result(subtype, text)
    r["duration_ms"] = ms
    r["usage"] = {"input_tokens": 10, "output_tokens": out}
    if is_error is not None:
        r["is_error"] = is_error
    return r


def _synthetic(text):
    return {"type": "assistant", "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": text}]}}


@pytest.fixture(autouse=True)
def _no_db():
    import database
    with patch.object(database.db, "record_token_usage", AsyncMock()), \
            patch("core.streaming_orchestrator._api_error_wait", AsyncMock()) as wait, \
            patch("core.streaming_orchestrator.write_user_message", AsyncMock()) as write:
        yield {"wait": wait, "write": write}


class _Env:
    def __init__(self):
        self.client = FakeClient()
        self.ends: list = []
        self.reader = CliStreamReader(
            self.client, "sess-test",
            on_steer_end=lambda u, o, seen: self.ends.append((u, o, seen)))
        self.reader.start()
        self.orch = _make_orchestrator()
        p = self.orch._parent
        p._client = object()
        p._stream_reader = self.reader
        p._turn_stream = self.reader.claim(U1, "human")
        self.resets = 0
        real = self.orch.reset_turn_carry

        def _count():
            self.resets += 1
            real()
        self.orch.reset_turn_carry = _count

    async def run(self, frames):
        self.client.feed(frames)
        await _settle(self.client)  # the reader runs ahead of the turn
        events, raised = [], None
        try:
            async for ev in self.orch._read_formatted_response():
                events.append(ev)
        except Exception as exc:  # noqa: BLE001
            raised = exc
        return events, raised


def _texts(events):
    return [b.get("text") for e in events if e.get("type") == "assistant"
            for b in e.get("content", []) if b.get("type") == "text"]


def _types(events, *keep):
    return [e["type"] for e in events if e.get("type") in keep]


# ── T7 / T21: the steer's round after the reply, same turn ───────────────────

async def test_t7_the_steers_round_is_read_as_the_same_turn_with_one_result():
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _res(ms=1000, out=5),
        _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(ms=300, out=2),
        _state("idle")])
    assert raised is None
    assert _texts(events) == ["A", "B"]
    assert _types(events, "result", "user_steered", "assistant") == ["assistant", "user_steered", "assistant", "result"]
    (steered,) = [e for e in events if e["type"] == "user_steered"]
    assert steered["outcome"] == "opened" and steered["clientId"] == "c-s1"
    (res,) = [e for e in events if e["type"] == "result"]
    assert res["duration_ms"] == 1300                       # both rounds
    assert res["usage"]["output_tokens"] == 7 and res["usage"]["input_tokens"] == 20
    assert env.orch._parent.state == SessionState.IDLE
    assert env.resets == 1                                  # the carry survives the first round (T28)
    assert env.orch._parent._steer_accepting is False
    assert env.ends == [(S1, "opened", True)]


async def test_a_turn_without_a_steer_ends_as_before():
    env = _Env()
    events, raised = await env.run([_state("running"), _init(), _echo(U1), _text("A"),
                                    _res(ms=1000, out=5), _state("idle")])
    assert raised is None and _texts(events) == ["A"]
    (res,) = [e for e in events if e["type"] == "result"]
    assert res["duration_ms"] == 1000 and res["usage"]["output_tokens"] == 5
    assert env.resets == 1 and env.orch._parent._steer_accepting is False


async def test_a_folded_steer_shows_where_it_was_read_and_the_turn_goes_on():
    env = _Env()
    env.client.feed([_state("running"), _init(), _echo(U1), _tool_use("t1")])
    await _settle(env.client)
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([_tool_result("t1"), _echo(S1), _text("skipping"), _res(), _state("idle")])
    assert raised is None
    assert _types(events, "user_steered", "result") == ["user_steered", "result"]
    assert [e["outcome"] for e in events if e["type"] == "user_steered"] == ["folded"]
    assert env.resets == 1


# ── the steer wins over every other continuation (T12b, T13, T27) ────────────

async def test_t12b_an_api_error_round_with_a_steer_waiting_reads_the_steer_not_a_retry(_no_db):
    env = _Env()
    env.orch._parent._content_emitted = True
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _tool_use("t1"), _tool_result("t1"),
        _synthetic(ERR), _res(text=ERR, is_error=True),
        _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None
    assert _no_db["wait"].await_count == 0 and _no_db["write"].await_count == 0
    assert not any(e.get("type") in ("error", "reconnecting") for e in events)
    assert _texts(events) == ["B"] and _types(events, "result") == ["result"]


async def test_t13_after_a_tool_hang_interrupt_the_steer_is_still_read():
    env = _Env()
    env.orch._parent._tool_hang_interrupted = True        # the watchdog, not a user Stop
    env.orch._parent._interrupted = True
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _tool_use("t1"),
        _res("error_during_execution", text="", is_error=True),
        _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None and _texts(events) == ["B"]
    assert [e["outcome"] for e in events if e["type"] == "user_steered"] == ["opened"]


async def test_t27_error_max_turns_with_a_steer_waiting_reads_the_steer():
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _tool_use("t1"), _tool_result("t1"),
        _res("error_max_turns", text="", is_error=True),
        _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None
    assert not any(e.get("type") == "turn_limit_reached" for e in events)
    assert env.orch._parent._client is not None
    assert _texts(events) == ["B"] and _types(events, "result") == ["result"]


async def test_a_user_stop_ends_the_turn_without_reading_the_steer():
    env = _Env()
    env.orch._parent._user_stopped_current_turn = True
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _res(), _state("idle")])
    assert _texts(events) == ["A"] and _types(events, "result") == ["result"]
    assert env.reader.next_steer() is not None             # left for the Stop's path (D8)


# ── the round the reader abandoned (idle before its Result) ──────────────────

async def test_a_round_the_cli_left_without_a_result_moves_on_to_the_steer():
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _state("idle"),
        _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None
    assert _texts(events) == ["A", "B"] and _types(events, "result") == ["result"]


# ── T22: a notification turn first (D6 waits on through it) ──────────────────

async def test_t22_a_notification_turn_before_the_steers_round_is_left_to_the_sink():
    env = _Env()
    injected = []
    env.reader._on_injected = injected.append
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _res(), _state("idle"),
        _notification(), _state("running"), _init(), _text("N"), _res(), _state("idle"),
        _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None and _texts(events) == ["A", "B"]
    notif, steer_round = injected
    assert not notif.transferred and not notif.started     # the sink runs it after the turn
    assert steer_round.transferred


# ── T24: the CLI never reads the steer ───────────────────────────────────────

async def test_t24_a_steer_never_read_recycles_and_finishes_the_held_result():
    env = _Env()
    p = env.orch._parent
    p._compute_init_timeout = lambda: 0.3
    p.TOOL_FREE_HARD_CEILING_S, p.TOOL_FREE_MAX_EXTENSIONS, p._tool_free_extensions = 600, 3, 0
    p.pid = None                                            # no process to CPU-probe
    recycled = []

    async def _crash_to_cold_async(clear_identity=False):
        recycled.append(clear_identity)
        # the CLI reads the steer at last, while it is being killed: too late
        env.client.feed([_state("running"), _init(), _echo(S1)])
        await _settle(env.client)
        env.reader.close()
        p._client = None
        p.state = SessionState.COLD
    p._crash_to_cold_async = _crash_to_cold_async
    p._steer_event = lambda kind, u, o, seen: env.ends.append((u, o, seen))
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _res(ms=1000, out=5), _state("idle")])
    assert raised is None
    assert recycled == [False]
    assert _texts(events) == ["A"] and _types(events, "result") == ["result"]
    (res,) = [e for e in events if e["type"] == "result"]
    assert res["duration_ms"] == 1000                       # not counted twice
    assert p.state == SessionState.COLD                     # not turned back to IDLE
    assert env.ends == [(S1, "reader_closed", False)]       # withdrawn, queued again, before the kill
    assert env.reader._claims == {}


# ── T12a: a leak-shaped end with a steer waiting reads the steer ─────────────

async def test_t12a_a_leaked_tool_call_round_with_a_steer_waiting_reads_the_steer(_no_db):
    from tests._tool_markup import tc
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    leak = tc('let me run it\n\n‹invoke name="Bash">\n‹parameter name="command">ls‹/parameter>')
    r1 = _res()
    r1["stop_reason"] = "end_turn"
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text(leak), r1,
        _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None
    assert _no_db["write"].await_count == 0                 # no leak continuation written
    assert _texts(events)[-1] == "B" and _types(events, "result") == ["result"]
    assert env.reader._merged is None


# ── T15: between the rounds the turn is still running ────────────────────────

async def test_t15_the_unit_streams_from_the_first_result_to_the_steers_round():
    env = _Env()
    p = env.orch._parent
    p.state = SessionState.STREAMING
    seen = []
    real = p._transition

    def _spy(new_state):
        seen.append(new_state)
        real(new_state)
    p._transition = _spy
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([
        _state("running"), _init(), _echo(U1), _text("A"), _res(),
        _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(), _state("idle")])
    assert raised is None and _texts(events) == ["A", "B"]   # the steer's round was read
    assert seen == [SessionState.IDLE]                      # once, at the turn's end



# ── review fixes ─────────────────────────────────────────────────────────────

async def test_the_accepting_flag_is_cleared_however_the_read_ends():
    from core.cli_stream_reader import TurnAbandoned
    env = _Env()
    events, raised = await env.run([_state("running"), _init(), _echo(U1), _text("A"), _state("idle")])
    assert isinstance(raised, TurnAbandoned)
    assert env.orch._parent._steer_accepting is False


async def test_a_steer_withdrawn_after_it_was_handed_out_finishes_the_held_result():
    """The write failed after the turn took the steer's stream (drop_claim ends it):
    the held round's Result still ends the turn, once, with no raise."""
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    env.client.feed([_state("running"), _init(), _echo(U1), _text("A"), _res(ms=1000, out=5)])
    events: list = []

    async def drive():
        async for ev in env.orch._read_formatted_response():
            events.append(ev)
    task = asyncio.ensure_future(drive())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if getattr(env.orch._parent._turn_stream, "uuid", None) == S1:
            break
    assert env.orch._parent._turn_stream.uuid == S1
    env.reader.drop_claim(S1)
    await asyncio.wait_for(task, 2)
    assert _texts(events) == ["A"] and _types(events, "result") == ["result"]
    (res,) = [e for e in events if e["type"] == "result"]
    assert res["duration_ms"] == 1000


async def test_an_abandoned_round_whose_steer_is_never_read_ends_as_abandoned_not_retried():
    from core.cli_stream_reader import TurnAbandoned
    env = _Env()
    p = env.orch._parent
    p._compute_init_timeout = lambda: 0.3
    p.TOOL_FREE_HARD_CEILING_S, p.TOOL_FREE_MAX_EXTENSIONS, p._tool_free_extensions = 600, 3, 0
    p.pid = None
    recycled = []

    async def _crash_to_cold_async(clear_identity=False):
        recycled.append(clear_identity)
        env.reader.close()
        p._client = None
        p.state = SessionState.COLD
    p._crash_to_cold_async = _crash_to_cold_async
    p._steer_event = lambda kind, u, o, seen: env.ends.append((u, o, seen))
    env.reader.claim(S1, "steer", client_id="c-s1")
    events, raised = await env.run([_state("running"), _init(), _echo(U1), _text("A"), _state("idle")])
    assert isinstance(raised, TurnAbandoned)               # not the retriable timeout
    assert recycled == [False] and env.ends == [(S1, "reader_closed", False)]


async def test_a_continuation_result_carries_the_rounds_absorbed_before_it():
    """The rounds live on the unit: a later read of the same turn (an answer to a
    question) ends with one result carrying every round."""
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    await env.run([_state("running"), _init(), _echo(U1), _text("A"), _res(ms=1000, out=5),
                   _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _res(ms=300, out=2),
                   _state("idle")])
    # the same turn reads on (a continuation): its round's result includes the earlier ones
    env.orch._parent._turn_steer_rounds = {"duration_ms": 1000, "usage": {"output_tokens": 5}}
    u2 = "00000000-0000-4000-8000-0000000000c9"
    env.orch._parent._turn_stream = env.reader.claim(u2, "human")
    events, raised = await env.run([_state("running"), _init(), _echo(u2), _text("C"),
                                    _res(ms=200, out=1), _state("idle")])
    (res,) = [e for e in events if e["type"] == "result"]
    assert res["duration_ms"] == 1200 and res["usage"]["output_tokens"] == 6


async def test_a_steer_stream_that_ends_empty_finishes_the_held_result():
    """Folded elsewhere after it was handed out: the stream ends with no frame."""
    env = _Env()
    env.reader.claim(S1, "steer", client_id="c-s1")
    env.client.feed([_state("running"), _init(), _echo(U1), _text("A"), _res(ms=1000, out=5)])
    events: list = []

    async def drive():
        async for ev in env.orch._read_formatted_response():
            events.append(ev)
    task = asyncio.ensure_future(drive())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if getattr(env.orch._parent._turn_stream, "uuid", None) == S1:
            break
    ts = env.orch._parent._turn_stream
    assert ts.uuid == S1
    ts._end()
    await asyncio.wait_for(task, 2)
    assert _texts(events) == ["A"] and _types(events, "result") == ["result"]
