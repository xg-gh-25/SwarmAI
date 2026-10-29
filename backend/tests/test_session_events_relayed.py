"""The hub's relayed sessions and ``only`` subscriptions (core/session_events.py), and
what the poll and the snapshot give with and without a relay.

Questions: do local activity writes leave a relayed session alone, and only it (R5.3);
does reset_relayed clear the turn and keep the seq (R4.4); does ``only`` deliver just
the cursors' sessions, and is a subscription without it unchanged (R7); with no relay,
are streaming-state and the snapshot what they were, and with one, is the relayed
session listed (R5.1, R5.2)?
"""
import asyncio

import pytest

from core import session_events, session_state, swarm_out_relay
from routers import chat as chat_router


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub()
    monkeypatch.setattr(session_events, "_hub", h)
    monkeypatch.setattr(swarm_out_relay, "_manager", None)
    return h


def test_local_activity_writes_skip_a_relayed_session_only(hub):
    hub.mark_relayed("hive-s")
    hub.publish("hive-s", {"type": "turn_start"}, turn_id="t1")
    assert hub.set_activity("hive-s", "streaming") is None
    hub.drop_session("hive-s")
    assert hub.open_turn_id("hive-s") == "t1"                      # drop_session did nothing
    assert hub.activity_of("hive-s") == ("idle", 0)
    assert hub.set_activity("local-s", "streaming") is not None     # a local session is untouched
    hub.publish("local-s", {"type": "turn_start"}, turn_id="t9")
    hub.drop_session("local-s")
    assert hub.open_turn_id("local-s") is None


def test_reset_relayed_clears_the_turn_and_keeps_the_seq(hub):
    hub.mark_relayed("hive-s")
    for t in ("turn_start", "assistant"):
        hub.publish("hive-s", {"type": t}, turn_id="t1")
    hub.reset_relayed("hive-s")
    log = hub._logs["hive-s"]
    assert log.active is None and log.active_turn is None and not log.buf and log.seq == 2
    assert hub.open_turn_id("hive-s") is None
    assert hub.publish("hive-s", {"type": "turn_start"}, turn_id="t2")["eventSeq"] == 3


async def _collect(hub, cursors, only, publish):
    out = []

    async def read():
        async for env in hub.subscribe(cursors, None, only=only):
            out.append(env)
    task = asyncio.create_task(read())
    await asyncio.sleep(0.01)
    publish()
    await asyncio.sleep(0.01)
    task.cancel()
    return out


def test_only_delivers_the_cursors_sessions(hub):
    hub.publish("other", {"type": "turn_start"}, turn_id="x")
    got = asyncio.run(_collect(hub, {"mine": -1}, True, lambda: (
        hub.publish("mine", {"type": "turn_start"}, turn_id="a"),
        hub.publish("other", {"type": "assistant"}, turn_id="x"))))
    assert got[0]["type"] == "events_hello" and set(got[0]["seqs"]) <= {"mine"}
    assert {e["sessionId"] for e in got[1:]} == {"mine"}


def test_without_only_every_session_is_delivered(hub):
    hub.publish("other", {"type": "turn_start"}, turn_id="x")
    got = asyncio.run(_collect(hub, {"mine": -1}, False, lambda: (
        hub.publish("mine", {"type": "turn_start"}, turn_id="a"),
        hub.publish("other", {"type": "assistant"}, turn_id="x"))))
    assert "other" in got[0]["seqs"]
    assert {e["sessionId"] for e in got[1:]} == {"mine", "other"}


class _Router:
    def list_units(self):
        return []

    def get_unit(self, sid):
        return None


def test_no_relay_means_streaming_state_and_snapshot_are_unchanged(hub, monkeypatch):
    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())
    assert asyncio.run(chat_router.get_streaming_state_endpoint()) == {"sessions": {}}
    assert asyncio.run(session_state.snapshot(_Router(), "hive-s")) is None


def test_a_relayed_session_is_listed_with_its_hive_state(hub, monkeypatch):
    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())
    m = swarm_out_relay.manager()
    relay = swarm_out_relay._Relay("hive-s", {}, {"streaming": True, "state": "streaming",
                                                 "activity": "streaming", "activity_seq": 7})
    m._relays["hive-s"] = relay
    out = asyncio.run(chat_router.get_streaming_state_endpoint())["sessions"]
    assert out["hive-s"]["state"] == "streaming" and out["hive-s"]["relay_connected"] is False
    snap = asyncio.run(session_state.snapshot(_Router(), "hive-s"))
    assert snap["activity_seq"] == 7
