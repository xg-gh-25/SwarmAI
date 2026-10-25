"""The reader's steer claim: where the CLI read a message written into a running turn.

Design docs/design/2026-10-24-chat-steer.md D3 (T1-T4, T19, the reader half of T22).
A steer claim is registered mid-turn. The CLI echoes it where it read it:

- inside another open turn (a human turn, a sink-started injected turn, another
  steer's round): ``folded`` -- a ``SteerRead`` marker goes into THAT turn at the
  echo, the claim ends, nothing is merged (no frame written twice);
- as its own next round (``init`` opened it, X7/X8/E10) or inside an injected turn
  nobody consumes yet (E11/E13a: a notification moved the count): ``opened`` --
  the claim owns the round, the marker sits at its echo.

``on_steer_echo(uuid, outcome)`` fires once at the echo; ``on_steer_end(uuid,
outcome, echo_seen)`` once when the claim ends (``folded`` at the echo, ``opened``
at its round's Result, ``reader_closed`` when the reader ends it without one).
A steer claim is never dropped by the idle rule for unconsumed claims.

Frames are built like the CLI's (SDK parser), fed through the same FakeClient as
the reader's own tests. Each test fails with its branch reverted.
"""
from __future__ import annotations

import asyncio

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from core.cli_stream_reader import CliStreamReader, MessageNotConsumed, SteerRead
from tests.test_cli_stream_reader import FakeClient, _drain, _settle, _texts

U1 = "00000000-0000-4000-8000-0000000000a1"
S1 = "00000000-0000-4000-8000-0000000000b1"
S2 = "00000000-0000-4000-8000-0000000000b2"
H2 = "00000000-0000-4000-8000-0000000000a2"


def _state(s):
    return {"type": "system", "subtype": "session_state_changed", "state": s, "session_id": "S"}


def _init():
    return {"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
            "mcp_servers": [], "model": "m", "permissionMode": "default"}


def _thinking():
    return {"type": "system", "subtype": "thinking_tokens", "session_id": "S"}


def _echo(uuid, text="msg"):
    return {"type": "user", "uuid": uuid, "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "user", "content": text}}


def _text(t):
    return {"type": "assistant", "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "assistant", "model": "m", "content": [{"type": "text", "text": t}]}}


def _tool_use(tid):
    return {"type": "assistant", "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "assistant", "model": "m", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "ls"}}]}}


def _tool_result(tid):
    return {"type": "user", "session_id": "S", "parent_tool_use_id": None,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def _result(subtype="success", text="done"):
    return {"type": "result", "subtype": subtype, "session_id": "S", "is_error": subtype != "success",
            "num_turns": 1, "result": text, "duration_ms": 1, "duration_api_ms": 1, "total_cost_usd": 0}


def _notification(tid="bg1"):
    return {"type": "system", "subtype": "task_notification", "session_id": "S", "task_id": tid,
            "tool_use_id": f"toolu_{tid}", "status": "completed", "summary": "done",
            "uuid": f"00000000-0000-4000-8000-0000000{sum(map(ord, tid)) % 100000:05d}",
            "output_file": "/tmp/task.output"}


class _Calls:
    def __init__(self):
        self.echo: list = []
        self.end: list = []
        self.injected: list = []


def _reader(c, calls: _Calls) -> CliStreamReader:
    r = CliStreamReader(
        c, "sess-steer",
        on_injected=calls.injected.append,
        on_steer_echo=lambda uuid, outcome: calls.echo.append((uuid, outcome)),
        on_steer_end=lambda uuid, outcome, seen: calls.end.append((uuid, outcome, seen)),
    )
    r.start()
    return r


def _kinds(msgs) -> list:
    """The stream as a compact trace: marker / text / init / tool_result / result."""
    out = []
    for m in msgs:
        if isinstance(m, SteerRead):
            out.append(f"steer:{m.outcome}:{m.client_id}")
        elif isinstance(m, ResultMessage):
            out.append("result")
        elif isinstance(m, SystemMessage):
            out.append(m.subtype)
        else:
            texts = _texts([m])
            if texts:
                out.append(f"text:{texts[0]}")
            elif any(getattr(b, "tool_use_id", None) for b in getattr(m, "content", None) or []):
                out.append("tool_result")
            elif any(getattr(b, "name", None) for b in getattr(m, "content", None) or []):
                out.append("tool_use")
    return out


async def _human_turn_open(c, r, calls):
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _tool_use("t1")])
    await _settle(c)
    return first


# ── folded (T1, T3) ──────────────────────────────────────────────────────────

async def test_t1_a_steer_read_at_a_tool_result_is_folded_into_the_running_turn():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = await _human_turn_open(c, r, calls)
    steer = r.claim(S1, "steer", client_id="c-s1")
    assert r.next_steer() is steer and not r.quiescent
    c.feed([_tool_result("t1"), _echo(S1), _text("OK, skipping it"), _result(), _state("idle")])
    await _settle(c)

    got = await asyncio.wait_for(_drain(first), 1)
    assert _kinds(got) == ["init", "tool_use", "tool_result", "steer:folded:c-s1",
                           "text:OK, skipping it", "result"]
    assert r._merged is None and r.next_steer() is None and r.quiescent
    assert calls.echo == [(S1, "folded")]
    assert calls.end == [(S1, "folded", True)]
    assert steer.closed and steer.echo_seen
    r.close()
    assert calls.end == [(S1, "folded", True)]  # reported once


async def test_t3_two_steers_read_in_one_gap_keep_their_write_order():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = await _human_turn_open(c, r, calls)
    r.claim(S1, "steer", client_id="c-s1")
    r.claim(S2, "steer", client_id="c-s2")
    c.feed([_tool_result("t1"), _echo(S1), _echo(S2), _text("both"), _result(), _state("idle")])
    await _settle(c)
    got = await asyncio.wait_for(_drain(first), 1)
    assert [k for k in _kinds(got) if k.startswith("steer")] == ["steer:folded:c-s1", "steer:folded:c-s2"]
    assert calls.echo == [(S1, "folded"), (S2, "folded")]
    r.close()


async def test_a_steer_echoed_in_a_sink_started_injected_turn_is_folded_there():
    """The sink already consumes the injected turn: the steer is folded into it
    (D3 row 3), never merged into a second stream."""
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    steer = r.claim(S1, "steer", client_id="c-s1")
    c.feed([_notification(), _state("running"), _init(), _tool_use("t1")])
    await _settle(c)
    (inj,) = calls.injected
    assert r.start_sink(inj)
    c.feed([_tool_result("t1"), _echo(S1), _text("noted"), _result(), _state("idle")])
    await _settle(c)
    got = await asyncio.wait_for(_drain(inj), 1)
    assert "steer:folded:c-s1" in _kinds(got)
    assert r._merged is None and steer.closed
    assert calls.end == [(S1, "folded", True)]
    r.close()


# ── opened (T2, T19) ─────────────────────────────────────────────────────────

async def test_t2_a_steer_the_cli_runs_as_its_next_round_owns_that_round():
    """E10: written at the Result boundary; the CLI reports idle, then runs it."""
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    steer = r.claim(S1, "steer", client_id="c-s1")
    c.feed([_state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _result(), _state("idle")])
    await _settle(c)

    assert _kinds(await asyncio.wait_for(_drain(first), 1)) == ["init", "text:A", "result"]
    assert _kinds(await asyncio.wait_for(_drain(steer), 1)) == ["init", "steer:opened:c-s1",
                                                                  "text:B", "result"]
    assert calls.echo == [(S1, "opened")]
    assert calls.end == [(S1, "opened", True)]
    r.close()


async def test_t19_a_second_steer_read_inside_the_first_steers_round_is_folded_there():
    """H4: S1 becomes the next round, S2 is read at S1's tool result. S2 folds into
    S1's round; nothing is merged, every frame reaches one stream once."""
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    s1 = r.claim(S1, "steer", client_id="c-s1")
    s2 = r.claim(S2, "steer", client_id="c-s2")
    c.feed([_state("idle"), _state("running"), _init(), _echo(S1), _tool_use("t1"),
            _tool_result("t1"), _echo(S2), _text("B"), _result(), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert _kinds(await asyncio.wait_for(_drain(s1), 1)) == [
        "init", "steer:opened:c-s1", "tool_use", "tool_result", "steer:folded:c-s2", "text:B", "result"]
    assert r._merged is None and s2.closed
    assert calls.echo == [(S1, "opened"), (S2, "folded")]
    assert calls.end == [(S2, "folded", True), (S1, "opened", True)]
    r.close()


# ── E11 / E13a: the steer's round is taken for an injected turn (T22, reader) ───

async def test_t22a_a_notification_after_the_claim_makes_the_round_injected_and_the_echo_takes_it():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = await _human_turn_open(c, r, calls)
    steer = r.claim(S1, "steer", client_id="c-s1")
    # the foreground Bash ends: its notification arrives after the claim (E11)
    c.feed([_notification("t1"), _tool_result("t1"), _text("A"), _result(),
            _state("idle"), _state("running"), _init(), _thinking(), _thinking(), _echo(S1),
            _text("B"), _result(), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    (inj,) = calls.injected
    assert inj.transferred
    assert _kinds(await asyncio.wait_for(_drain(steer), 1)) == [
        "init", "thinking_tokens", "thinking_tokens", "steer:opened:c-s1", "text:B", "result"]
    assert calls.end == [(S1, "opened", True)]
    r.close()


async def test_t22b_a_notification_turn_first_then_the_steers_round():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    steer = r.claim(S1, "steer", client_id="c-s1")
    c.feed([_state("idle"), _notification(), _state("running"), _init(), _text("N"), _result(),
            _state("idle"), _state("running"), _init(), _echo(S1), _text("B"), _result(), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    notif, second = calls.injected
    assert not notif.transferred
    assert _texts(await asyncio.wait_for(_drain(notif), 1)) == ["N"]
    assert second.transferred
    assert _kinds(await asyncio.wait_for(_drain(steer), 1)) == ["init", "steer:opened:c-s1",
                                                                  "text:B", "result"]
    assert calls.end == [(S1, "opened", True)]
    r.close()


# ── the idle rule (T4) ───────────────────────────────────────────────────────

async def test_t4_an_idle_after_running_drops_a_human_claim_but_not_a_steer_claim():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result(), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    steer = r.claim(S1, "steer", client_id="c-s1")
    human = r.claim(H2, "human")
    c.feed([_state("running"), _state("idle")])
    await _settle(c)
    with pytest.raises(MessageNotConsumed):
        await asyncio.wait_for(_drain(human), 1)
    assert not steer.closed and r.next_steer() is steer
    assert calls.end == []
    r.close()


# ── ends without an echo ─────────────────────────────────────────────────────

async def test_a_steer_never_read_ends_reader_closed_when_the_reader_goes():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    await _human_turn_open(c, r, calls)
    r.claim(S1, "steer", client_id="c-s1")
    r.close()
    assert calls.end == [(S1, "reader_closed", False)]
    assert calls.echo == []


async def test_a_steer_never_read_ends_reader_closed_at_end_of_stream():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    await _human_turn_open(c, r, calls)
    r.claim(S1, "steer", client_id="c-s1")
    c.q.put_nowait(None)
    await _settle(c)
    assert calls.end == [(S1, "reader_closed", False)]


async def test_an_opened_round_that_never_finishes_ends_reader_closed_with_its_echo():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    r.claim(S1, "steer", client_id="c-s1")
    c.feed([_state("idle"), _state("running"), _init(), _echo(S1), _tool_use("t1")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    c.q.put_nowait(None)
    await _settle(c)
    assert calls.end == [(S1, "reader_closed", True)]


async def test_a_withdrawn_steer_claim_is_not_reported():
    """drop_claim: the write failed, the caller re-queues the row itself."""
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    await _human_turn_open(c, r, calls)
    r.claim(S1, "steer", client_id="c-s1")
    r.drop_claim(S1)
    assert r.next_steer() is None
    r.close()
    assert calls.end == []


async def test_an_opened_round_the_cli_abandons_with_an_idle_ends_reader_closed():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    r.claim(S1, "steer", client_id="c-s1")
    c.feed([_state("idle"), _state("running"), _init(), _echo(S1), _tool_use("t1"), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert calls.end == [(S1, "reader_closed", True)]
    r.close()
    assert calls.end == [(S1, "reader_closed", True)]


# ── handing the steer's stream to the running turn (D6) ──────────────────────

async def test_take_steer_hands_out_a_round_the_reader_already_opened():
    """The reader is its own task: it can open (and finish) the steer's round
    before the turn reads its Result. take_steer still hands that stream out,
    with every frame; a folded steer is never handed out."""
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    s0 = r.claim(S2, "steer", client_id="c-s0")
    c.feed([_state("running"), _init(), _echo(U1), _tool_use("t1"), _tool_result("t1"), _echo(S2)])
    s1 = r.claim(S1, "steer", client_id="c-s1")
    c.feed([_text("A"), _result(), _state("idle"), _state("running"), _init(), _echo(S1),
            _text("B"), _result(), _state("idle")])
    await _settle(c)
    assert s0._steer_outcome == "folded" and r.next_steer() is None
    assert await asyncio.wait_for(_drain(first), 1)
    got = r.take_steer()
    assert got is s1
    assert _kinds(await asyncio.wait_for(_drain(got), 1)) == ["init", "steer:opened:c-s1", "text:B", "result"]
    assert r.take_steer() is None
    r.close()


async def test_take_steer_skips_a_steer_that_ended_unread_and_any_after_a_dead_stream():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    await _human_turn_open(c, r, calls)
    r.claim(S1, "steer", client_id="c-s1")
    r.drop_claim(S1)
    assert r.take_steer() is None
    r.claim(S2, "steer", client_id="c-s2")
    c.q.put_nowait(None)
    await _settle(c)
    assert r.take_steer() is None



async def test_a_steer_round_that_reaches_its_result_without_an_echo_counts_as_read():
    c, calls = FakeClient(), _Calls()
    r = _reader(c, calls)
    first = r.claim(U1, "human")
    c.feed([_state("running"), _init(), _echo(U1), _text("A"), _result()])
    await _settle(c)
    r.claim(S1, "steer", client_id="c-s1")
    c.feed([_state("idle"), _state("running"), _init(), _text("B"), _result(), _state("idle")])
    await _settle(c)
    await asyncio.wait_for(_drain(first), 1)
    assert calls.echo == [(S1, "opened")]
    assert calls.end == [(S1, "opened", True)]
    r.close()
