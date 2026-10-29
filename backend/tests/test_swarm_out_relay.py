"""Swarm Out relay (core/swarm_out_relay.py): a Hive session's events into the local hub.

Questions, one per relay rule (design docs/design/2026-10-29-swarm-out-hive-session-tab.md
§3): is only this session's whitelisted traffic published, with local seqs and the Hive's
turn ids (R4.1); is each Hive seq published once (R4.2); are the Hive's own log markers
never passed on, and does the first connect join while reconnects use the relay's cursor
(R4.3, R4.7); does stopping mid-turn leave no open turn here (R4.4); does a Hive restart
clear the turn and tell the tab to reload with local values (R4.5); do concurrent starts
and two clients keep one upstream (R4.6); does the session always have a state entry,
seeded before acquire returns and turned idle when the Hive stops listing it (R5.1)?
"""
import asyncio
import json

import httpx
import pytest

from core import session_events, swarm_out, swarm_out_relay
from core.swarm_out_remote import RemoteError

SID = "sess-1"
INST = {"id": "h1", "name": "worker-1", "cloudfront_domain": "abc123.cloudfront.net",
        "auth_user": "admin", "auth_password": "pw"}
STATE = {"streaming": True, "state": "streaming", "activity": "streaming", "activity_seq": 1000}


class FakeHive:
    """Each GET /api/chat/events gets its own feed; tests push SSE events into it."""

    def __init__(self):
        self.connects: list[dict] = []
        self.feeds: list[asyncio.Queue] = []
        self.sessions = {SID: dict(STATE)}
        self.events_status = 200
        self.state_reply = None
        self.raw: list[bytes] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat/sessions/streaming-state":
            if self.state_reply is not None:
                return self.state_reply()
            return httpx.Response(200, json={"sessions": None if self.sessions is None else dict(self.sessions)})
        if request.url.path == "/api/chat/events":
            self.connects.append(dict(request.url.params))
            if self.events_status != 200:
                return httpx.Response(self.events_status)
            q: asyncio.Queue = asyncio.Queue()
            self.feeds.append(q)

            async def body():
                while True:
                    item = await q.get()
                    if item is None:
                        return
                    yield item if isinstance(item, bytes) else f"data: {json.dumps(item)}\n\n".encode()
            return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})
        return httpx.Response(404)

    def push(self, *events, feed=-1):
        for e in events:
            self.feeds[feed].put_nowait(e)

    def close(self, feed=-1):
        self.feeds[feed].put_nowait(None)


@pytest.fixture
def env(monkeypatch):
    hive = FakeHive()
    monkeypatch.setattr(session_events, "_hub", session_events.SessionEventHub())
    monkeypatch.setattr(swarm_out_relay, "_manager", None)
    monkeypatch.setattr(swarm_out_relay, "BACKOFF_S", (0,))
    monkeypatch.setattr(swarm_out_relay, "STATE_POLL_S", 0.05)

    def client(inst, timeout=None):
        return httpx.AsyncClient(base_url="https://abc123.cloudfront.net", transport=httpx.MockTransport(hive.handler))
    monkeypatch.setattr(swarm_out, "_client", client)
    return hive


async def until(cond, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def log_of(sid=SID):
    return session_events.hub()._logs.get(sid)


def published(sid=SID):
    log = log_of(sid)
    return [] if log is None else list(log.buf)


def hello(epoch="E1"):
    return {"type": "events_hello", "epoch": epoch, "seqs": {SID: 0}}


def ev(etype, seq, **kw):
    return {"type": etype, "sessionId": SID, "turnId": kw.pop("turn", "t1"), "eventSeq": seq, **kw}


def test_only_this_sessions_whitelisted_events_are_published(env):
    async def go():
        m = swarm_out_relay.manager()
        entry = await m.acquire(SID, "w1", INST)
        assert entry["state"] == "streaming" and entry["activity_seq"] == 1000   # seeded before return
        await until(lambda: env.feeds)
        env.push(hello(),
                 ev("turn_start", 5000, turnClientId="cid-1"),
                 ev("assistant", 5001, content=[{"type": "text", "text": "hi"}]),
                 ev("ui_command", 5002, cmd="open-canvas-file", path="/Users/x/.aws/credentials", allowAbs=True),
                 ev("file_changed", 5003, path="secrets.txt"),
                 {**ev("assistant", 5004), "sessionId": "other"},
                 ev("assistant", 5005, session_id="other"),
                 ev("turn_end", 5006))
        await until(lambda: any(e["type"] == "turn_end" for e in published()))
        got = published()
        assert [e["type"] for e in got] == ["turn_start", "assistant", "turn_end"]
        assert [e["eventSeq"] for e in got] == [1, 2, 3]                     # local numbering
        assert got[0]["turnId"] == "t1" and got[0]["turnClientId"] == "cid-1"
        assert all(e["sessionId"] == SID for e in got)
        assert "credentials" not in json.dumps(got)
        assert env.connects[0]["only"] == "1"
        assert json.loads(env.connects[0]["cursors"]) == {SID: -1}           # first connect joins
        assert m.state_entry(SID)["relay_connected"] is True
        await m.release(SID, "w1")
    asyncio.run(go())


def test_each_hive_seq_is_published_once(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello(), ev("turn_start", 10), ev("assistant", 11), ev("assistant", 11),
                 ev("assistant", 9), ev("assistant", 12))
        await until(lambda: len(published()) >= 3)
        await asyncio.sleep(0.05)
        assert [e["type"] for e in published()] == ["turn_start", "assistant", "assistant"]
        await m.release(SID, "w1")
    asyncio.run(go())


def test_hive_log_markers_are_not_passed_on_and_reconnects_use_the_own_cursor(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello(), ev("turn_start", 40), ev("assistant", 41),
                 {"type": "session_state", "sessionId": SID, "turnId": "", "eventSeq": 41,
                  "state": {**STATE, "activity_seq": 2000}, "snapshot": True})
        await until(lambda: m.state_entry(SID)["activity_seq"] == 2000)
        types = [e["type"] for e in published()]
        assert "events_hello" not in types
        snap = [e for e in published() if e["type"] == "session_state"]
        assert snap and "snapshot" not in snap[0]                           # republished as an ordinary push
        env.close()                                                          # upstream drops
        await until(lambda: len(env.connects) == 2)
        assert json.loads(env.connects[1]["cursors"]) == {SID: 41}          # own cursor, not a join
        assert env.connects[1]["epoch"] == "E1"
        await m.release(SID, "w1")
    asyncio.run(go())


def test_a_hive_resync_restarts_the_session_here(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello(), ev("turn_start", 7), ev("assistant", 8))
        await until(lambda: len(published()) == 2)
        env.push({"type": "resync", "sessionId": SID, "eventSeq": 6, "activeTurnId": "t1"},
                 ev("turn_start", 7), ev("assistant", 8), ev("assistant", 9))
        await until(lambda: published() and published()[-1].get("eventSeq", 0) >= 6)
        await asyncio.sleep(0.05)
        got = published()
        assert got[0]["type"] == "resync" and got[0]["activeTurnId"] is None
        assert got[0]["eventSeq"] == 3                                       # this daemon's head
        assert [e["type"] for e in got[1:]] == ["turn_start", "assistant", "assistant"]
        await m.release(SID, "w1")
    asyncio.run(go())


def test_release_mid_turn_leaves_no_open_turn(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello(), ev("turn_start", 3), ev("assistant", 4))           # no turn_end
        await until(lambda: len(published()) == 2)
        hub = session_events.hub()
        assert hub.open_turn_id(SID) == "t1"
        seq = log_of().seq
        await m.release(SID, "w1")
        assert hub.open_turn_id(SID) is None
        assert log_of().active is None and not log_of().buf
        assert log_of().seq == seq                                           # numbering kept
        assert not hub.is_relayed(SID) and m.state_entry(SID) is None
    asyncio.run(go())


def test_a_hive_restart_clears_the_turn_and_resyncs_locally(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello("E1"), ev("turn_start", 300), ev("assistant", 301))
        await until(lambda: len(published()) == 2)
        env.close()
        await until(lambda: len(env.feeds) == 2)
        env.push(hello("E2"), ev("turn_start", 1, turn="t2"), ev("assistant", 2, turn="t2"))
        await until(lambda: any(e.get("turnId") == "t2" for e in published()))
        await until(lambda: len([e for e in published() if e.get("turnId") == "t2"]) == 2)
        got = published()
        assert got[0]["type"] == "resync" and got[0]["activeTurnId"] is None
        assert [e["turnId"] for e in got[1:]] == ["t2", "t2"]                 # old turn gone, new epoch's seqs accepted
        assert session_events.hub().open_turn_id(SID) == "t2"
        await m.release(SID, "w1")
    asyncio.run(go())


def test_concurrent_starts_and_two_clients_keep_one_upstream(env):
    async def go():
        m = swarm_out_relay.manager()
        await asyncio.gather(m.acquire(SID, "w1", INST), m.acquire(SID, "w2", INST))
        await until(lambda: env.feeds)
        await asyncio.sleep(0.05)
        assert len(env.connects) == 1
        await m.release(SID, "w2")
        assert m.is_relayed(SID)                                             # w1 still holds it
        env.push(hello(), ev("turn_start", 1))
        await until(lambda: len(published()) == 1)
        await m.release(SID, "w1")
        assert not m.is_relayed(SID)
        n = len(published())
        env.push(ev("assistant", 2))                                         # a late event of a stopped relay
        await asyncio.sleep(0.05)
        assert len(published()) <= n
    asyncio.run(go())


def test_too_many_sessions_are_refused(env):
    async def go():
        m = swarm_out_relay.manager()
        for i in range(swarm_out_relay.MAX_SESSIONS):
            env.sessions[f"s{i}"] = dict(STATE)
            await m.acquire(f"s{i}", "w1", INST)
        with pytest.raises(RemoteError) as e:
            await m.acquire("s-extra", "w1", INST)
        assert e.value.status == 429
        await m.stop_all()
    asyncio.run(go())


def test_an_upstream_error_reconnects(env):
    async def go():
        env.events_status = 500
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: len(env.connects) >= 2)
        assert m.state_entry(SID)["relay_connected"] is False
        env.events_status = 200
        await until(lambda: env.feeds)
        await until(lambda: m.state_entry(SID)["relay_connected"] is True)
        await m.release(SID, "w1")
    asyncio.run(go())


def test_a_session_the_hive_no_longer_lists_turns_idle(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        assert m.state_entry(SID)["state"] == "streaming"
        env.sessions.pop(SID)
        await until(lambda: m.state_entry(SID)["state"] == "idle")
        entry = m.state_entry(SID)
        assert entry["activity"] == "idle" and entry["activity_seq"] == 1001  # past the Hive's last seq
        assert any(e["type"] == "session_activity" and e["activity"] == "idle" for e in published())
        await m.release(SID, "w1")
    asyncio.run(go())


def test_a_hive_that_cannot_give_its_state_starts_no_relay(env):
    async def go():
        env.sessions = None   # streaming-state answers {"sessions": null}
        m = swarm_out_relay.manager()
        with pytest.raises(RemoteError):
            await m.acquire(SID, "w1", INST)
        assert not m.is_relayed(SID) and env.connects == []
    asyncio.run(go())


@pytest.mark.parametrize("reply", [
    lambda: httpx.Response(200, content=b"not json"),
    lambda: httpx.Response(200, json=[1, 2]),
    lambda: httpx.Response(200, content=b"[" * 200000),
])
def test_a_malformed_state_answer_starts_no_relay(env, reply):
    async def go():
        env.state_reply = reply
        m = swarm_out_relay.manager()
        with pytest.raises(RemoteError):
            await m.acquire(SID, "w1", INST)
        assert not m.is_relayed(SID)
    asyncio.run(go())


def test_a_line_nested_past_the_parser_depth_does_not_end_the_relay(env):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello())
        env.feeds[-1].put_nowait({"type": "assistant", "sessionId": SID, "eventSeq": 1,
                                  "nested": [[[[[[[[1]]]]]]]]})
        await until(lambda: len(published()) == 1)
        # a raw line the JSON parser cannot take, then a normal event: still relayed
        env.feeds[-1].put_nowait(b"data: " + b"[" * 200000 + b"\n\n")
        env.push(ev("turn_end", 3))
        await until(lambda: any(e["type"] == "turn_end" for e in published()))
        assert m.state_entry(SID) is not None
        await m.release(SID, "w1")
    asyncio.run(go())


def test_an_event_during_a_state_read_wins_over_its_result(env, monkeypatch):
    async def go():
        m = swarm_out_relay.manager()
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello())
        relay = m._relays[SID]
        gate = asyncio.Event()
        real = swarm_out_relay.read_state

        async def slow(inst, sid):
            out = await real(inst, sid)        # read while the Hive had no unit yet
            await gate.wait()
            return out
        env.sessions.pop(SID)
        monkeypatch.setattr(swarm_out_relay, "read_state", slow)
        refresh = asyncio.create_task(m._refresh_state(relay, relay.gen))
        await asyncio.sleep(0.02)
        env.push({"type": "session_activity", "sessionId": SID, "turnId": "", "eventSeq": 5,
                  "activity": "streaming", "activity_seq": 5000})
        await until(lambda: m.state_entry(SID)["activity_seq"] == 5000)
        gate.set()
        await refresh
        assert m.state_entry(SID)["activity"] == "streaming"     # the stale "no entry" lost
        await m.release(SID, "w1")
    asyncio.run(go())


def test_a_slow_state_read_does_not_hold_other_sessions(env, monkeypatch):
    async def go():
        gate = asyncio.Event()
        real = swarm_out_relay.read_state

        async def slow_for_s1(inst, sid):
            if sid == "s-slow":
                await gate.wait()
            return await real(inst, sid)
        monkeypatch.setattr(swarm_out_relay, "read_state", slow_for_s1)
        env.sessions["s-slow"] = dict(STATE)
        m = swarm_out_relay.manager()
        slow = asyncio.create_task(m.acquire("s-slow", "w1", INST))
        await asyncio.sleep(0.02)
        await asyncio.wait_for(m.acquire(SID, "w1", INST), 1)     # not blocked by the slow one
        await asyncio.wait_for(m.release(SID, "w1"), 1)
        gate.set()
        await slow
        await m.stop_all()
    asyncio.run(go())


def test_an_unexpected_error_on_hive_data_reconnects(env, monkeypatch):
    async def go():
        m = swarm_out_relay.manager()
        real = m._handle
        calls = {"n": 0}

        def boom_once(relay, gen, event):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("something no one foresaw")
            return real(relay, gen, event)
        monkeypatch.setattr(m, "_handle", boom_once)
        await m.acquire(SID, "w1", INST)
        await until(lambda: env.feeds)
        env.push(hello(), ev("turn_start", 1))                               # the second call raises
        await until(lambda: len(env.connects) >= 2)                          # it reconnected
        assert m.is_relayed(SID)
        await m.release(SID, "w1")
    asyncio.run(go())
