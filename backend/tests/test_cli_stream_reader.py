"""Tests for core.cli_stream_reader: one consumer of the CLI's SDK stream per subprocess.

The fixtures under tests/fixtures/cli_stream/ are frames recorded from the real Claude
CLI through the SDK (bare client, replay-user-messages on, session-state events on),
reduced to the fields the reader reads. ``_marker`` lines record the moment the
recording client wrote its second user message; tests register the claim at that
point. Frames are parsed with the SDK's own parser, so the reader sees exactly the
Message objects a live session produces.

Properties covered (design: Knowledge/Designs/2026-10-20-continuous-cli-stream-reader-design.md):
- the reader consumes the stream with no turn in progress, so a later control
  request is never blocked behind unconsumed frames (I1);
- a claimed turn yields only its own frames and ends at its own ResultMessage (I2);
- a turn nobody claimed goes to the injected callback;
- a claim that a task notification raced is transferred at its echo;
- quiescence follows the CLI's session state (I3); the reader's end or error
  reaches the open turn and every later claim (I4).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage, UserMessage
from claude_agent_sdk._internal.message_parser import parse_message

from core.cli_stream_reader import (
    CliStreamEnded, CliStreamReader, MessageNotConsumed, TurnAbandoned, is_turn_abandoned,
)

FIX = Path(__file__).parent / "fixtures" / "cli_stream"
U1 = "00000000-0000-4000-8000-000000000001"
U2 = "00000000-0000-4000-8000-000000000002"
U3 = "00000000-0000-4000-8000-000000000003"


def _frames(name: str) -> list[dict]:
    return [json.loads(ln) for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln.strip()]


class FakeClient:
    """receive_messages() over a queue the test feeds (None = end of stream)."""

    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.consumed = 0

    async def receive_messages(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            self.consumed += 1
            msg = parse_message(item)
            if msg is not None:
                yield msg

    def feed(self, frames: list[dict]) -> None:
        for f in frames:
            if "_marker" not in f:
                self.q.put_nowait(f)


async def _settle(client: FakeClient) -> None:
    """Wait until the reader has taken every fed frame (bounded by wall time, not
    a tick count: a loaded test machine schedules the reader late)."""
    deadline = asyncio.get_running_loop().time() + 2.0
    while not client.q.empty() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    for _ in range(5):
        await asyncio.sleep(0)


def _split(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Frames before and after the first ``_marker`` line."""
    for i, f in enumerate(frames):
        if "_marker" in f:
            return frames[:i], frames[i + 1:]
    return frames, []


def _until_result(frames: list[dict]) -> list[dict]:
    """Frames up to and including the first ResultMessage."""
    i = next(i for i, f in enumerate(frames) if f.get("type") == "result")
    return frames[:i + 1]


def _split_at_first_idle(frames: list[dict]) -> tuple[list[dict], list[dict]]:
    """Frames up to the CLI's first idle report, and the rest: the moment the send gate
    opens, before the background task's notification (the real race)."""
    i = next(i for i, f in enumerate(frames)
             if f.get("subtype") == "session_state_changed" and f.get("state") == "idle")
    rest = [f for f in frames[i + 1:] if "_marker" not in f]
    return frames[:i + 1], rest


async def _drain(ts) -> list:
    out = []
    async for m in ts:
        out.append(m)
    return out


def _texts(msgs) -> list[str]:
    out = []
    for m in msgs:
        for b in getattr(m, "content", None) or []:
            t = getattr(b, "text", None)
            if t:
                out.append(t)
    return out


def _reader(client, injected=None, idle=None):
    return CliStreamReader(
        client, "sess-1",
        on_injected=(injected.append if injected is not None else None),
        on_idle=(lambda: idle.append(1)) if idle is not None else None,
    )


# ── I1: consumed with no turn in progress ─────────────────────────────────


async def test_stream_is_consumed_with_no_turn_in_progress():
    """All 59 frames of a session with a CLI-initiated turn are read although no
    claim exists for the second turn: nothing parks in the SDK buffer (the stall)."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    first = r.claim(U1, "human")
    frames = _frames("idle_notification")
    c.feed(frames)
    await _settle(c)
    assert c.consumed == len(frames)
    got = await asyncio.wait_for(_drain(first), 1)
    assert isinstance(got[-1], ResultMessage) and got[-1].result == "STARTED"
    assert len(injected) == 1
    inj = await asyncio.wait_for(_drain(injected[0]), 1)
    assert isinstance(inj[-1], ResultMessage) and inj[-1].result == "NOTIFIED"
    assert inj[-1].origin["kind"] == "task-notification"
    r.close()


async def test_more_than_100_frames_with_no_consumer_are_all_read():
    """The SDK buffer holds 100 frames; the reader must keep reading past it while
    the injected stream has no consumer yet (its frames wait in the stream)."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    c.feed([{"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
             "mcp_servers": [], "model": "m", "permissionMode": "default"}])
    c.feed([{"type": "system", "subtype": "thinking_tokens", "session_id": "S"}] * 150)
    await _settle(c)
    assert c.consumed == 151
    assert len(injected) == 1
    r.close()


# ── I2: a claimed turn gets only its own frames ───────────────────────────


async def test_claimed_turn_ends_at_its_own_result_not_an_injected_one():
    """A CLI-initiated text-only turn finishing before the user's next turn must not
    end that next turn (the stale-result shift of receive_response)."""
    c = FakeClient()
    injected: list = []
    idle: list = []
    r = _reader(c, injected, idle)
    r.start()
    first = r.claim(U1, "human")
    c.feed(_frames("idle_notification"))
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert r.quiescent
    second = r.claim(U2, "human")
    assert not r.quiescent  # a claim is waiting: the CLI is about to run it
    c.feed([
        {"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"},
        {"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
         "mcp_servers": [], "model": "m", "permissionMode": "default"},
        {"type": "user", "uuid": U2, "session_id": "S", "parent_tool_use_id": None,
         "message": {"role": "user", "content": "two"}},
        {"type": "assistant", "session_id": "S", "parent_tool_use_id": None,
         "message": {"role": "assistant", "model": "m", "content": [{"type": "text", "text": "TWO"}]}},
        {"type": "result", "subtype": "success", "session_id": "S", "is_error": False, "num_turns": 1,
         "result": "TWO", "duration_ms": 1, "duration_api_ms": 1, "total_cost_usd": 0},
        {"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"},
    ])
    got = await asyncio.wait_for(_drain(second), 1)
    assert _texts(got) == ["TWO"]
    assert not any(isinstance(m, UserMessage) and m.uuid == U2 for m in got)  # echo swallowed
    assert any(isinstance(m, SystemMessage) and m.subtype == "init" for m in got)
    assert r.quiescent and idle
    r.close()


async def test_compact_claim_owns_its_turn_although_the_echo_comes_last():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    before, after = _split(_frames("compact"))
    first = r.claim(U1, "human")
    c.feed(before)
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    comp = r.claim(U3, "compact")
    c.feed(after)
    got = await asyncio.wait_for(_drain(comp), 1)
    assert any(isinstance(m, SystemMessage) and m.subtype == "compact_boundary" for m in got)
    assert isinstance(got[-1], ResultMessage)
    r.close()


async def test_notification_inside_a_user_turn_stays_in_that_turn():
    """agent_in_turn: the background agent finished while the user turn was still
    running; the CLI folded the notification into that turn (one result)."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    first = r.claim(U1, "human")
    c.feed(_frames("agent_in_turn"))
    got = await asyncio.wait_for(_drain(first), 1)
    assert "STARTED\n\nAGENTDONE" in _texts(got)
    assert injected == []
    assert r.background_tasks == frozenset()
    r.close()


# ── the notification race: transfer at the echo ──────────────────────────


async def test_raced_claim_is_transferred_at_its_echo():
    """race_fold: the user message was written after the task notification; the CLI
    folded it into the injected turn. The turn opens provisional (injected), and the
    echo hands it — buffered frames included — to the claimant."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    before, after = _split_at_first_idle(_frames("race_fold"))
    first = r.claim(U1, "human")
    c.feed(before)
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert r.quiescent  # the send gate is open: the notification has not arrived yet
    second = r.claim(U2, "human")
    c.feed(after)
    got = await asyncio.wait_for(_drain(second), 1)
    assert isinstance(got[-1], ResultMessage)
    assert "NOTIFIED\n\nHUMAN2" in _texts(got)
    assert any(isinstance(m, SystemMessage) and m.subtype == "init" for m in got)
    assert len(injected) == 1 and injected[0].transferred
    assert await asyncio.wait_for(_drain(injected[0]), 1) == []
    r.close()


async def test_echo_after_the_sink_started_is_shared_not_stolen():
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    before, after = _split_at_first_idle(_frames("race_fold"))
    first = r.claim(U1, "human")
    c.feed(before)
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert r.quiescent  # the send gate is open: the notification has not arrived yet
    second = r.claim(U2, "human")
    # feed up to (not including) the echo, start the sink, then the rest
    cut = next(i for i, f in enumerate(after) if f.get("uuid") == U2)
    c.feed(after[:cut])
    await _settle(c)
    assert len(injected) == 1 and r.start_sink(injected[0])
    c.feed(after[cut:])
    sink, mine = await asyncio.wait_for(asyncio.gather(_drain(injected[0]), _drain(second)), 1)
    assert isinstance(sink[-1], ResultMessage) and isinstance(mine[-1], ResultMessage)
    assert sink[-1] is mine[-1]
    assert "NOTIFIED\n\nHUMAN2" in _texts(mine)
    r.close()


async def test_start_sink_refuses_a_transferred_stream():
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    before, after = _split_at_first_idle(_frames("race_fold"))
    first = r.claim(U1, "human")
    c.feed(before)
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert r.quiescent  # the send gate is open: the notification has not arrived yet
    second = r.claim(U2, "human")
    c.feed(after)
    await asyncio.wait_for(_drain(second), 1)
    assert r.start_sink(injected[0]) is False
    r.close()


# ── I3: quiescence ────────────────────────────────────────────────────────


async def test_task_notification_makes_the_cli_not_quiescent_until_idle():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    first = r.claim(U1, "human")
    frames = _frames("idle_notification")
    # the terminal task_updated is the first sign the task finished (it precedes the
    # task_notification); a non-terminal update must not end quiescence
    cut = next(i for i, f in enumerate(frames) if f.get("subtype") == "task_updated")
    assert frames[cut]["patch"]["status"] == "completed"
    c.feed(frames[:cut])
    c.feed([{**frames[cut], "patch": {"status": "running"}}])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert r.quiescent
    assert r.background_tasks  # the background sleep is still running
    c.feed(frames[cut:cut + 1])
    await _settle(c)
    assert not r.quiescent
    assert r.background_tasks == frozenset()
    c.feed(frames[cut + 1:])
    await _settle(c)
    assert r.quiescent
    r.close()


async def test_without_session_state_frames_a_result_counts_as_idle():
    """An older CLI that never emits session_state_changed: the result is all there is."""
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    first = r.claim(U1, "human")
    c.feed([f for f in _until_result(_frames("compact")) if f.get("subtype") != "session_state_changed"])
    await asyncio.wait_for(_drain(first), 1)
    await _settle(c)
    assert r.quiescent
    r.close()


# ── I4: no claim or open turn outlives the stream ─────────────────────────


async def test_stream_error_reaches_the_open_turn_and_later_claims():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    first = r.claim(U1, "human")
    c.feed(_frames("compact")[:7])
    await _settle(c)
    c.q.put_nowait(RuntimeError("cli died"))
    with pytest.raises(RuntimeError, match="cli died"):
        await asyncio.wait_for(_drain(first), 1)
    with pytest.raises(RuntimeError, match="cli died"):
        r.claim(U2, "human")


async def test_end_of_stream_ends_a_waiting_claim_as_an_ended_stream():
    """EOF = the subprocess is gone: the claim ends like receive_response did (no
    result, no exception), so the orchestrator's end-of-stream / retry path runs; the
    reader is dead, quiescent, reports it once and refuses later claims."""
    c = FakeClient()
    errors: list = []
    r = CliStreamReader(c, "sess-1", on_error=lambda: errors.append(1))
    r.start()
    waiting = r.claim(U1, "human")
    c.q.put_nowait(None)
    assert await asyncio.wait_for(_drain(waiting), 1) == []
    assert r.dead and r.quiescent and errors == [1]
    assert not is_turn_abandoned(r.error)
    with pytest.raises(CliStreamEnded):
        r.claim(U2, "human")


async def test_a_dead_stream_drops_its_background_tasks():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    started = next(f for f in _frames("idle_notification") if f.get("subtype") == "task_started")
    c.feed([started])
    await _settle(c)
    assert r.background_tasks
    c.q.put_nowait(None)
    await _settle(c)
    await asyncio.sleep(0.01)
    assert r.dead and r.background_tasks == frozenset()


async def test_close_is_not_a_dead_stream():
    c = FakeClient()
    errors: list = []
    r = CliStreamReader(c, "sess-1", on_error=lambda: errors.append(1))
    r.start()
    await _settle(c)
    r.close()
    await asyncio.sleep(0.01)
    assert not r.dead and errors == []
    assert is_turn_abandoned(r.error)


async def test_a_claim_marked_at_the_gate_does_not_take_a_later_notification_turn():
    """A task finished between the gate and the write: the CLI's own turn goes to the
    sink, and the claim keeps waiting for its own (or its echo)."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    mark = r.notif_count  # what the gate saw
    notif = next(f for f in _frames("idle_notification") if f.get("subtype") == "task_notification")
    c.feed([notif])
    await _settle(c)
    claim = r.claim(U2, "human", notif_mark=mark)
    c.feed([{"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
             "mcp_servers": [], "model": "m", "permissionMode": "default"}])
    await _settle(c)
    assert len(injected) == 1 and not claim.closed


async def test_idle_after_a_cli_turn_keeps_a_claim_the_cli_takes_next():
    """The CLI ran its own turn first and went idle before reading our message: the
    claim must survive that idle and own the turn the CLI runs for it next."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    notif = next(f for f in _frames("idle_notification") if f.get("subtype") == "task_notification")
    c.feed([notif])
    await _settle(c)
    claim = r.claim(U2, "human", notif_mark=0)  # gate saw no notification
    init = {"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
            "mcp_servers": [], "model": "m", "permissionMode": "default"}
    result = next(f for f in _frames("compact") if f.get("type") == "result")
    c.feed([
        {"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"},
        init, result,  # the CLI's own turn
        {"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"},
    ])
    await _settle(c)
    assert len(injected) == 1 and not claim.closed
    echo = {"type": "user", "uuid": U2, "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "user", "content": "two"}}
    c.feed([{"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"},
            init, echo, {**result, "result": "MINE"}])
    got = await asyncio.wait_for(_drain(claim), 1)
    # the claim survived the idle; its echo hands it the turn the CLI ran for it
    assert isinstance(got[-1], ResultMessage) and got[-1].result == "MINE"
    r.close()


async def test_a_claim_the_cli_skipped_raises_message_not_consumed():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    waiting = r.claim(U1, "human")
    c.feed([{"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"},
            {"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"}])
    with pytest.raises(MessageNotConsumed):
        await asyncio.wait_for(_drain(waiting), 1)
    r.close()


async def test_a_turn_records_when_its_first_frame_arrived():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    claim = r.claim(U1, "human")
    before, _ = _split(_frames("compact"))
    c.feed(before)
    await asyncio.wait_for(_drain(claim), 1)
    assert claim.opened_at is not None
    r.close()


async def test_idle_while_a_turn_is_open_abandons_it():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    first = r.claim(U1, "human")
    c.feed(_frames("compact")[:7])  # init + echo, no result
    c.feed([{"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"}])
    with pytest.raises(TurnAbandoned):
        await asyncio.wait_for(_drain(first), 1)
    r.close()


async def test_idle_after_running_abandons_a_claim_the_cli_never_took():
    """The CLI ran (running), went idle and never consumed the written message:
    the claim must end with TurnAbandoned, not wait forever."""
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    waiting = r.claim(U1, "human")
    c.feed([
        {"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"},
        {"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"},
    ])
    with pytest.raises(TurnAbandoned):
        await asyncio.wait_for(_drain(waiting), 1)
    assert r.quiescent
    r.close()


async def test_an_idle_already_in_flight_does_not_abandon_a_fresh_claim():
    """A leak continuation is written right after a result: the idle that follows
    was emitted before the CLI read it, so the claim must survive and own the
    next turn."""
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    claim = r.claim(U2, "leak")
    c.feed([{"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"}])
    await _settle(c)
    assert not claim.closed
    _, after = _split(_frames("compact"))
    turn = [f for f in after if f.get("uuid") != U3]
    c.feed(turn)
    got = await asyncio.wait_for(_drain(claim), 1)
    assert isinstance(got[-1], ResultMessage)
    r.close()


async def test_a_stray_result_opens_no_turn_and_ends_no_claim():
    """An interrupt sent to an idle CLI can produce a result with no turn: it must
    not become the first frame of the next user turn (it would end it at once)."""
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    stray = next(f for f in _frames("compact") if f.get("type") == "result")
    claim = r.claim(U1, "human")
    c.feed([stray])
    await _settle(c)
    assert not claim.closed and injected == []
    before, _ = _split(_frames("compact"))
    c.feed(before)
    got = await asyncio.wait_for(_drain(claim), 1)
    assert any(isinstance(m, SystemMessage) and m.subtype == "init" for m in got)
    assert got[-1].result == "ONE"
    r.close()


async def test_close_fails_open_streams_and_stops_reading():
    c = FakeClient()
    injected: list = []
    r = _reader(c, injected)
    r.start()
    waiting = r.claim(U1, "human")
    r.close()
    with pytest.raises(TurnAbandoned):
        await asyncio.wait_for(_drain(waiting), 1)
    with pytest.raises(TurnAbandoned):
        r.claim(U2, "human")


async def test_claimant_turn_without_its_echo_ends_at_result_and_logs(caplog):
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    first = r.claim(U1, "human")
    c.feed([f for f in _until_result(_frames("compact")) if f.get("uuid") != U1])
    got = await asyncio.wait_for(_drain(first), 1)
    assert isinstance(got[-1], ResultMessage)
    assert "turn_owner_mismatch" in caplog.text
    r.close()


# ── wiring: the options the reader relies on ───────────────────────────


def test_spawn_options_enable_replay_and_session_state():
    """Both CLI outputs the reader routes by are switched on per subprocess, and
    options the caller already set are kept."""
    from types import SimpleNamespace
    from core.session_unit import _enable_stream_reader_options
    opts = SimpleNamespace(extra_args={"thinking-display": "summarized"}, env={"A": "1"})
    _enable_stream_reader_options(opts)
    assert opts.extra_args == {"thinking-display": "summarized", "replay-user-messages": None}
    assert opts.env == {"A": "1", "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"}
    bare = SimpleNamespace(extra_args=None, env=None)
    _enable_stream_reader_options(bare)
    assert bare.extra_args == {"replay-user-messages": None}
    assert bare.env == {"CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"}


async def test_drop_claim_restores_quiescence_and_ends_the_stream():
    c = FakeClient()
    r = _reader(c, [])
    r.start()
    ts = r.claim(U1, "human")
    assert not r.quiescent
    r.drop_claim(U1)
    assert r.quiescent
    with pytest.raises(TurnAbandoned):
        await asyncio.wait_for(_drain(ts), 1)
    r.close()
