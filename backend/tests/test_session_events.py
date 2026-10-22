"""Tests for core.session_events: the per-session event log every turn publishes to.

Covers the hub contract the subscription stream relies on:
- every published event gets the session's next eventSeq (strictly increasing);
- a subscriber replays what came after its cursor, then gets live events, with no
  duplicate when an event lands between registration and replay;
- a cursor older than the replay buffer, or from another epoch, gets a resync;
- a join cursor (-1) replays only the session's active turn;
- a slow subscriber whose queue overflows is closed, others are unaffected;
- drop_session forgets a session's log.
"""
from __future__ import annotations

import asyncio
from collections import deque

import pytest

from core.session_events import JOIN, SessionEventHub


def _ev(i: int) -> dict:
    return {"type": "text_delta", "text": f"t{i}"}


async def _take(agen, n: int, timeout: float = 1.0) -> list[dict]:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(agen.__anext__(), timeout))
    return out


def test_publish_assigns_increasing_seq_per_session():
    hub = SessionEventHub(buffer_size=10, queue_size=10)
    a1 = hub.publish("A", _ev(1), turn_id="t1")
    a2 = hub.publish("A", _ev(2), turn_id="t1")
    b1 = hub.publish("B", _ev(1), turn_id="t9", turn_client_id="local-x-asst")
    assert (a1["eventSeq"], a2["eventSeq"], b1["eventSeq"]) == (1, 2, 1)
    assert a1["sessionId"] == "A" and a1["turnId"] == "t1"
    assert b1["turnClientId"] == "local-x-asst"
    assert "turnClientId" not in a1
    assert hub.current_seq("A") == 2 and hub.current_seq("missing") == 0


def test_publish_does_not_mutate_the_callers_event():
    hub = SessionEventHub()
    ev = _ev(1)
    hub.publish("A", ev, turn_id="t1")
    assert "eventSeq" not in ev


async def test_subscribe_hello_then_replay_after_cursor_then_live():
    hub = SessionEventHub(buffer_size=10, queue_size=10)
    for i in range(1, 4):
        hub.publish("A", _ev(i), turn_id="t1")
    sub = hub.subscribe({"A": 1}, epoch=hub.epoch)
    hello, r2, r3 = await _take(sub, 3)
    assert hello["type"] == "events_hello" and hello["epoch"] == hub.epoch
    assert [r2["eventSeq"], r3["eventSeq"]] == [2, 3]
    hub.publish("A", _ev(4), turn_id="t1")
    hub.publish("B", _ev(1), turn_id="t2")
    live = await _take(sub, 2)
    assert [(e["sessionId"], e["eventSeq"]) for e in live] == [("A", 4), ("B", 1)]
    await sub.aclose()
    assert hub.subscriber_count() == 0


async def test_event_published_during_replay_is_not_duplicated():
    hub = SessionEventHub(buffer_size=10, queue_size=10)
    hub.publish("A", _ev(1), turn_id="t1")
    hub.publish("A", _ev(2), turn_id="t1")
    sub = hub.subscribe({"A": 0}, epoch=hub.epoch)
    await _take(sub, 2)  # hello + seq 1; the subscriber is registered now
    hub.publish("A", _ev(3), turn_id="t1")  # lands in buffer AND queue
    rest = await _take(sub, 2)
    assert [e["eventSeq"] for e in rest] == [2, 3]
    hub.publish("A", _ev(4), turn_id="t1")
    nxt = await _take(sub, 1)
    assert nxt[0]["eventSeq"] == 4
    await sub.aclose()


async def test_cursor_older_than_buffer_gets_resync():
    hub = SessionEventHub(buffer_size=3, queue_size=10)
    for i in range(1, 7):
        hub.publish("A", _ev(i), turn_id="t1")
    sub = hub.subscribe({"A": 1}, epoch=hub.epoch)
    hello, rs = await _take(sub, 2)
    assert rs == {"type": "resync", "sessionId": "A", "eventSeq": 6, "activeTurnId": None}
    hub.publish("A", _ev(7), turn_id="t1")
    nxt = await _take(sub, 1)
    assert nxt[0]["eventSeq"] == 7
    await sub.aclose()


async def test_cursor_from_another_epoch_gets_resync_for_every_session():
    hub = SessionEventHub()
    hub.publish("A", _ev(1), turn_id="t1")
    sub = hub.subscribe({"A": 1, "B": 4}, epoch="old-epoch")
    hello, r1, r2 = await _take(sub, 3)
    assert {r1["sessionId"], r2["sessionId"]} == {"A", "B"}
    assert all(r["type"] == "resync" for r in (r1, r2))
    assert {r["sessionId"]: r["eventSeq"] for r in (r1, r2)} == {"A": 1, "B": 0}
    await sub.aclose()


async def test_cursor_ahead_of_log_gets_resync():
    hub = SessionEventHub()
    hub.publish("A", _ev(1), turn_id="t1")
    sub = hub.subscribe({"A": 9}, epoch=hub.epoch)
    _, rs = await _take(sub, 2)
    assert rs["type"] == "resync" and rs["eventSeq"] == 1
    await sub.aclose()


async def test_join_cursor_replays_only_the_active_turn():
    hub = SessionEventHub(buffer_size=50, queue_size=10)
    hub.publish("A", {"type": "turn_start"}, turn_id="t1")
    hub.publish("A", _ev(1), turn_id="t1")
    hub.publish("A", {"type": "turn_end"}, turn_id="t1")
    hub.publish("A", {"type": "turn_start"}, turn_id="t2")
    hub.publish("A", _ev(2), turn_id="t2")
    sub = hub.subscribe({"A": JOIN}, epoch=hub.epoch)
    got = await _take(sub, 3)
    assert [(e.get("turnId"), e["type"]) for e in got[1:]] == [("t2", "turn_start"), ("t2", "text_delta")]
    await sub.aclose()


async def test_join_cursor_with_no_active_turn_replays_nothing():
    hub = SessionEventHub()
    hub.publish("A", {"type": "turn_start"}, turn_id="t1")
    hub.publish("A", {"type": "turn_end"}, turn_id="t1")
    sub = hub.subscribe({"A": JOIN}, epoch=hub.epoch)
    await _take(sub, 1)
    hub.publish("A", {"type": "turn_start"}, turn_id="t2")
    live = await _take(sub, 1)
    assert live[0]["turnId"] == "t2"
    await sub.aclose()


async def test_overflowing_subscriber_is_closed_and_others_keep_receiving():
    hub = SessionEventHub(buffer_size=100, queue_size=3)
    slow = hub.subscribe({}, epoch=hub.epoch)
    fast = hub.subscribe({}, epoch=hub.epoch)
    await _take(slow, 1)
    await _take(fast, 1)
    for i in range(1, 6):  # 5 > queue_size: slow never reads
        hub.publish("A", _ev(i), turn_id="t1")
        await _take(fast, 1)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(slow.__anext__(), 1.0)
    hub.publish("A", _ev(6), turn_id="t1")
    assert (await _take(fast, 1))[0]["eventSeq"] == 6
    await fast.aclose()
    assert hub.subscriber_count() == 0


async def test_drop_session_frees_the_buffer_but_keeps_the_seq():
    hub = SessionEventHub()
    hub.publish("A", _ev(1), turn_id="t1")
    hub.publish("A", _ev(2), turn_id="t1")
    hub.drop_session("A")
    assert hub._logs["A"].buf == deque()
    # a tab still holding cursor 2 must not see the next event as a duplicate
    assert hub.publish("A", _ev(3), turn_id="t2")["eventSeq"] == 3
    sub = hub.subscribe({"A": 1}, epoch=hub.epoch)
    _, rs = await _take(sub, 2)
    assert rs["type"] == "resync"
    await sub.aclose()
    hub.drop_session("never-existed")  # no error


def test_parse_cursors_bounds_untrusted_input():
    from core.session_events import parse_cursors
    assert parse_cursors(None) == {}
    assert parse_cursors("") == {}
    assert parse_cursors("not json") == {}
    assert parse_cursors('["a"]') == {}
    assert parse_cursors('{"A": 3, "B": -1, "C": "x", "D": -7, "E": 2.9}') == {"A": 3, "B": JOIN, "E": 2}
    big = "{" + ",".join(f'"s{i}": 1' for i in range(500)) + "}"
    assert len(parse_cursors(big)) == 64
    assert parse_cursors('{"' + "a" * 300 + '": 1}') == {}
    assert parse_cursors("x" * 70000) == {}
    assert parse_cursors('{"A": Infinity, "B": NaN, "C": -Infinity, "D": 1e400, "E": 4}') == {"E": 4}


async def test_event_published_before_the_replay_snapshot_is_yielded_once():
    # registered at the hello; the next event lands in the queue AND in the buffer
    # the replay is about to read — it must still come out once
    hub = SessionEventHub(buffer_size=10, queue_size=10)
    hub.publish("A", _ev(1), turn_id="t1")
    sub = hub.subscribe({"A": 0}, epoch=hub.epoch)
    await _take(sub, 1)  # hello: the subscriber is registered, replay not started
    hub.publish("A", _ev(2), turn_id="t1")
    got = await _take(sub, 2)
    assert [e["eventSeq"] for e in got] == [1, 2]
    hub.publish("A", _ev(3), turn_id="t1")
    nxt = await _take(sub, 1)
    assert nxt[0]["eventSeq"] == 3
    await sub.aclose()


async def test_cursor_for_an_unknown_session_does_not_break_the_stream():
    # after a daemon restart a client stores the resync's eventSeq 0 and sends it back
    hub = SessionEventHub()
    sub = hub.subscribe({"fresh": 0, "other": 0}, epoch=hub.epoch)
    hello = await _take(sub, 1)
    assert hello[0]["type"] == "events_hello"
    hub.publish("fresh", _ev(1), turn_id="t")
    live = await _take(sub, 1)
    assert live[0]["sessionId"] == "fresh" and live[0]["eventSeq"] == 1
    await sub.aclose()


async def test_join_after_the_turn_start_left_the_buffer_still_starts_the_turn():
    hub = SessionEventHub(buffer_size=3, queue_size=10)
    hub.publish("A", {"type": "turn_start"}, turn_id="t1")
    for i in range(1, 7):
        hub.publish("A", _ev(i), turn_id="t1")
    sub = hub.subscribe({"A": JOIN}, epoch=hub.epoch)
    got = await _take(sub, 8)
    assert [e["type"] for e in got[1:3]] == ["turn_start", "text_delta"]
    assert [e["eventSeq"] for e in got[1:]] == list(range(1, 8))
    await sub.aclose()


async def test_resync_names_the_active_turn_and_replays_it_whole():
    hub = SessionEventHub(buffer_size=3, queue_size=10)
    hub.publish("A", _ev(0), turn_id="t0")          # seq 1, an older turn
    hub.publish("A", {"type": "turn_start"}, turn_id="t1")  # seq 2
    for i in range(1, 6):
        hub.publish("A", _ev(i), turn_id="t1")      # seq 3..7
    sub = hub.subscribe({"A": 1}, epoch=hub.epoch)  # older than the 3-envelope ring
    got = await _take(sub, 8)
    rs = got[1]
    assert rs == {"type": "resync", "sessionId": "A", "eventSeq": 1, "activeTurnId": "t1"}
    assert [e["eventSeq"] for e in got[2:]] == [2, 3, 4, 5, 6, 7]
    hub.publish("A", {"type": "turn_end"}, turn_id="t1")
    assert (await _take(sub, 1))[0]["type"] == "turn_end"
    await sub.aclose()


def test_an_unencodable_event_is_buffered_as_an_error_envelope():
    hub = SessionEventHub()
    env = hub.publish("A", {"type": "assistant", "content": [object()]}, turn_id="t")
    assert env["type"] == "error" and env["code"] == "SERIALIZATION_ERROR"
    assert env["eventSeq"] == 1 and env["turnId"] == "t"
    import json as _json
    _json.dumps(list(hub._logs["A"].buf))



async def test_subscribe_sends_each_cursors_session_state_snapshot_before_live():
    """S3: after the replay, each cursor's session gets its state snapshot (the
    registered provider = the poll's producer), at the seq the tab now holds and
    outside the buffer; a session the provider knows nothing of gets none."""
    from core import session_events
    hub = SessionEventHub(buffer_size=10, queue_size=10)
    hub.publish("A", _ev(1), turn_id="t1")

    async def provider(sid):
        return {"streaming": False, "state": "idle"} if sid == "A" else None

    old = session_events._state_provider
    session_events.set_state_provider(provider)
    try:
        sub = hub.subscribe({"A": 0, "B": 0}, epoch=hub.epoch)
        hello, r1, snap = await _take(sub, 3)
        assert r1["eventSeq"] == 1
        assert snap == {"type": "session_state", "sessionId": "A", "turnId": "", "eventSeq": 1,
                        "state": {"streaming": False, "state": "idle"}, "snapshot": True}
        hub.publish("A", _ev(2), turn_id="t1")
        (live,) = await _take(sub, 1)
        assert live["eventSeq"] == 2
        assert all(e["type"] != "session_state" for e in hub._logs["A"].buf)
        await sub.aclose()
    finally:
        session_events.set_state_provider(old)


async def test_a_turn_past_the_active_limit_still_replays_whole(monkeypatch):
    """§7.1: at ACTIVE_TURN_LIMIT the deltas an `assistant` envelope already
    carries are dropped, so a JOIN of a very long turn gets its turn_start, every
    assistant / tool envelope and the in-flight deltas — nothing of substance is
    missing even though the ring buffer no longer holds the turn's middle."""
    from core import session_events
    monkeypatch.setattr(session_events, "ACTIVE_TURN_LIMIT", 20)
    hub = SessionEventHub(buffer_size=5, queue_size=100)
    hub.publish("A", {"type": "turn_start", "source": "chat"}, turn_id="t")
    for msg in range(6):                       # 6 messages x (5 deltas + assistant + tool_result)
        for i in range(5):
            hub.publish("A", {"type": "text_delta", "index": 0, "text": f"{msg}.{i}"}, turn_id="t")
        hub.publish("A", {"type": "assistant", "content": [{"type": "text", "text": f"m{msg}"}]}, turn_id="t")
        hub.publish("A", {"type": "tool_result", "content": [], "n": msg}, turn_id="t")
    hub.publish("A", {"type": "text_delta", "index": 0, "text": "live"}, turn_id="t")
    sub = hub.subscribe({"A": JOIN}, epoch=hub.epoch)
    envs = [await asyncio.wait_for(sub.__anext__(), 1)]
    while envs[-1].get("text") != "live":
        envs.append(await asyncio.wait_for(sub.__anext__(), 1))
    await sub.aclose()
    body = envs[1:]
    assert len(body) <= 20                     # it fits the (patched) limit
    assert body[0]["type"] == "turn_start"
    assert [e["content"][0]["text"] for e in body if e["type"] == "assistant"] == [f"m{i}" for i in range(6)]
    assert [e["n"] for e in body if e["type"] == "tool_result"] == list(range(6))
    assert body[-1]["text"] == "live"
    seqs = [e["eventSeq"] for e in body]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
