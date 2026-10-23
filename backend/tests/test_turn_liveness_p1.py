"""P1 v4 acceptance tests for docs/design/chat-turn-liveness.md — the failure windows
the independent review found (§9 v4). Each is driven deterministically with gates.

- the claim never outlives its request: closing the turn generator right after
  ``accepted`` releases it; a claim whose task ended is no claim (RC-A);
- the one busy check protects a claimed session from release / eviction / TTL /
  stale-COLD purge (RC-A);
- a drain that loses the race to a user send is delivered when that send's turn
  ends; a Stop's end that arrives before the turn's end still delivers (RC-B);
- a send that outwaits a Stop gets SESSION_BUSY and advances nothing (RC-C);
- cancelling a queued message races a drain's claim with exactly one winner, and
  the ledger lets the same client_id be sent again (RC-D).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core import session_events
from core.session_router import SessionRouter, TurnOpener
from core.session_unit import SessionState, SessionUnit
from core.turn_phase import turn_phase_event


@pytest.fixture
def hub(monkeypatch):
    h = session_events.SessionEventHub(buffer_size=500, queue_size=500)
    monkeypatch.setattr(session_events, "_hub", h)
    return h


_ROUTERS: list[SessionRouter] = []


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    r = SessionRouter(prompt_builder=pb, config=MagicMock())
    _ROUTERS.append(r)
    return r


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _shut_down_routers():
    """Each test's routers are shut down here: their drain workers stopped and the
    state publishes they scheduled finished, so no task outlives its test."""
    yield
    while _ROUTERS:
        await _stop_drain(_ROUTERS.pop())
    # the publishes (core/session_state.publish) are fire-and-forget tasks of a
    # few awaits each; the loop is shared by the whole session, so wait for those
    # by name rather than for every task on it
    pubs = [t for t in asyncio.all_tasks()
            if not t.done() and "publish" in getattr(t.get_coro(), "__qualname__", "")]
    if pubs:
        await asyncio.wait(pubs, timeout=2)


async def _stop_drain(r):
    t = r._drain_worker_task
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except BaseException:  # noqa: BLE001
            pass


def _script(gate: asyncio.Event | None = None, reached: asyncio.Event | None = None,
            record: list | None = None):
    """unit.send: optionally stop before the reply (a cold start), then answer."""
    async def send(**kwargs: Any):
        if record is not None:
            record.append(kwargs.get("query_content"))
        sid = kwargs.get("app_session_id")
        yield turn_phase_event("starting")
        if reached is not None:
            reached.set()
        if gate is not None:
            await gate.wait()
        yield {"type": "session_start", "sessionId": sid}
        yield {"type": "assistant", "content": [{"type": "text", "text": "ok"}], "model": "t"}
        yield {"type": "result", "sessionId": sid, "durationMs": 1}
    return send


def _patched(send):
    return (patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock,
                  return_value={"model": "test"}),
            patch("core.session_unit.SessionUnit.send", side_effect=send, autospec=False))


async def _wait(pred, secs=5.0):
    for _ in range(int(secs / 0.02)):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


# ── RC-A ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closing_the_turn_right_after_accepted_releases_the_claim(hub):
    r = _router()
    sid = "s-close"
    r.get_or_create_unit(sid, "default")
    a, b = _patched(_script(gate=asyncio.Event()))
    with a, b:
        agen = r.run_conversation(agent_id="default", user_message="hi", session_id=sid,
                                  client_id="local-close")
        while True:
            ev = await agen.__anext__()
            if ev.get("type") == "accepted":
                break
        assert hub.open_turn_id(sid) is not None, "admission did not claim the turn"
        await agen.aclose()
    assert hub.open_turn_id(sid) is None, "the claim outlived the closed turn"
    assert r.is_busy(sid) is False
    await _stop_drain(r)


@pytest.mark.asyncio
async def test_a_claim_whose_task_ended_is_no_claim(hub):
    async def claim():
        assert hub.try_claim_turn("s-dead", "t1")
    await asyncio.ensure_future(claim())        # the claiming task is done now
    assert hub.open_turn_id("s-dead") is None
    assert hub.try_claim_turn("s-dead", "t2")   # and the session can be claimed again


@pytest.mark.asyncio
async def test_cleanup_paths_leave_a_claimed_session_alone(hub):
    from core.lifecycle_manager import LifecycleManager
    r = _router()
    sid = "s-claimed"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)
    unit.last_used = 0.0                         # as old as it gets
    unit.kill = AsyncMock()
    assert TurnOpener(source="chat", turn_client_id=None).open(sid, unit)
    assert r.is_busy(sid) is True
    out = await r.release_session(sid, force=True)
    assert out["status"] == "skipped_active" and hub.open_turn_id(sid) is not None
    assert await r._evict_idle(exclude=None) is False
    lm = LifecycleManager(router=r)
    lm.TTL_SECONDS = 0
    await lm._check_ttl()
    unit._transition(SessionState.COLD)
    await lm._purge_stale_cold()
    unit.kill.assert_not_awaited()
    assert r.get_unit(sid) is unit, "a claimed COLD unit was purged"
    assert hub.open_turn_id(sid) is not None


# ── RC-B ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_drain_that_loses_the_race_is_delivered_when_the_winner_ends(hub, monkeypatch):
    from core import session_pending
    r = _router()
    sid = "s-race"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)      # an IDLE edge kicks a drain: let it pass
    await asyncio.sleep(0.1)
    await _stop_drain(r)
    await session_pending.persist_pending(sid, user_message="queued one", content=None,
                                          agent_id="default", client_id="local-q1")
    in_claim, go = asyncio.Event(), asyncio.Event()
    real_claim = session_pending.claim_pending_batch

    async def gated_claim(s):
        if not in_claim.is_set():
            in_claim.set()
            await go.wait()
        return await real_claim(s)
    monkeypatch.setattr(session_pending, "claim_pending_batch", gated_claim)

    user_gate, user_reached, sent = asyncio.Event(), asyncio.Event(), []
    a, b = _patched(_script(gate=user_gate, reached=user_reached, record=sent))
    with a, b:
        drain = asyncio.ensure_future(r.drain_pending(sid))
        await asyncio.wait_for(in_claim.wait(), 5)        # the drain passed its busy check
        user = asyncio.ensure_future(_consume(r, sid, "the user", "local-u"))
        await asyncio.wait_for(user_reached.wait(), 5)    # the user's send holds the turn
        go.set()
        await asyncio.wait_for(drain, 5)                  # lost admission → rolled back
        assert await session_pending.count_pending(sid) == 1
        user_gate.set()
        await asyncio.wait_for(user, 5)                   # its end kicks the drain
        assert await _wait(lambda: len(sent) == 2), sent
        assert "queued one" in str(sent[1])
        assert await _wait(lambda: hub.open_turn_id(sid) is None)
    assert await session_pending.count_pending(sid) == 0
    await _stop_drain(r)


async def _consume(r, sid, text, cid):
    async for _ in r.run_conversation(agent_id="default", user_message=text,
                                      session_id=sid, client_id=cid):
        pass


@pytest.mark.asyncio
async def test_a_stop_end_before_the_turn_end_still_delivers(hub):
    """stop_done arrives while the stopped turn is still open: the drain is held back
    (the session is busy) and runs when the turn ends."""
    from core import session_pending
    r = _router()
    sid = "s-stopfirst"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)
    gate, reached, sent = asyncio.Event(), asyncio.Event(), []
    a, b = _patched(_script(gate=gate, reached=reached, record=sent))
    with a, b:
        first = asyncio.ensure_future(_consume(r, sid, "first", "local-f"))
        await asyncio.wait_for(reached.wait(), 5)
        await _consume(r, sid, "second", "local-s")       # queued behind the first
        assert await session_pending.count_pending(sid) == 1
        r._on_unit_cli_event(sid, "stop_done")            # before the first turn ends
        await asyncio.sleep(0.1)
        assert len(sent) == 1, "drained onto a busy session"
        gate.set()
        await asyncio.wait_for(first, 5)
        assert await _wait(lambda: len(sent) == 2), sent
        assert "second" in str(sent[1])
    assert await _wait(lambda: hub.open_turn_id(sid) is None)
    assert await session_pending.count_pending(sid) == 0
    await _stop_drain(r)


# ── RC-C ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_send_that_outwaits_a_stop_is_busy_and_advances_nothing(monkeypatch):
    from core.exceptions import SessionBusyError
    unit = SessionUnit(session_id="s-slowstop", agent_id="default")
    unit._stopping = True
    monkeypatch.setattr(SessionUnit, "STOP_WAIT_BUDGET_S", 0.1)
    gen = unit._send_generation
    with pytest.raises(SessionBusyError):
        await unit.send("hi", options=MagicMock()).__anext__()
    assert unit._send_generation == gen


# ── RC-D ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_and_claim_race_has_one_winner_and_the_client_id_can_be_resent():
    from core import send_requests, session_pending
    sid = "s-cancel"
    for _ in range(5):
        res = await send_requests.reserve("local-c", sid)
        assert res.taken
        row = await session_pending.persist_pending(sid, user_message="x", content=None,
                                                    agent_id="default", client_id="local-c")
        await send_requests.persisted("local-c", row.id)
        cancel, claimed = await asyncio.gather(
            session_pending.cancel_unclaimed(sid, "local-c"),
            session_pending.claim_pending_batch(sid))
        assert (cancel == "cancelled") != bool(claimed), (cancel, claimed)
        if claimed:
            await session_pending.mark_sent_batch(sid, [c.pending_seq for c in claimed])
            await send_requests.release("local-c")    # as a delivered + reset send
        rec = await send_requests.reserve("local-c", sid)
        assert rec.taken, "the cancelled client_id cannot be sent again"
        await send_requests.release("local-c")


# ── second review (§9 v4 实现, review 2) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_dead_cleanup_leaves_a_claimed_session_alone(hub):
    from core.lifecycle_manager import LifecycleManager
    r = _router()
    sid = "s-dead-claimed"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.DEAD)
    unit._hooks_enqueued = True
    assert TurnOpener(source="chat", turn_client_id=None).open(sid, unit)
    await LifecycleManager(router=r)._cleanup_dead()
    assert hub.open_turn_id(sid) is not None, "DEAD cleanup dropped a live claim"
    assert unit.state == SessionState.DEAD   # the claimed send recovers it


def test_a_dropped_session_forgets_its_waiter(hub):
    hub.note_waiter("s-drop")
    hub.drop_session("s-drop")
    assert hub.take_waiters("s-drop") is False


@pytest.mark.asyncio
async def test_a_queue_timeout_is_delivered_when_a_slot_frees(hub, monkeypatch):
    from core import session_pending
    r = _router()
    sid, other = "s-qt", "s-qt-other"
    r.get_or_create_unit(sid, "default")
    busy = r.get_or_create_unit(other, "default")
    busy._transition(SessionState.IDLE)
    busy._transition(SessionState.STREAMING)

    async def no_slot(_unit):
        return "timeout"
    monkeypatch.setattr(SessionRouter, "_acquire_slot", lambda self, u: no_slot(u))
    a, b = _patched(_script())
    with a, b:
        events = [ev async for ev in r.run_conversation(
            agent_id="default", user_message="later", session_id=sid, client_id="local-qt")]
    assert any(e.get("code") == "QUEUE_TIMEOUT" and e.get("pendingSeq") for e in events)
    assert await session_pending.count_pending(sid) == 1
    kicked = []
    monkeypatch.setattr(SessionRouter, "kick", lambda self, s: kicked.append(s))
    busy._transition(SessionState.IDLE)                  # the other tab's turn ends
    assert sid in kicked, "a freed slot did not kick the timed-out session"
    await _stop_drain(r)


@pytest.mark.asyncio
async def test_a_send_whose_row_failed_still_waits_its_turn(hub, monkeypatch):
    from database import db
    r = _router()
    sid = "s-nodb"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)
    assert hub.try_claim_turn(sid, "other-turn")         # another turn holds it

    async def boom(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(db.messages, "put", boom)
    sent = []
    a, b = _patched(_script(record=sent))
    with a, b:
        events = [ev async for ev in r.run_conversation(
            agent_id="default", user_message="hi", session_id=sid, client_id="local-nodb")]
    assert sent == [], "a second turn ran beside the one holding the session"
    busy = [e for e in events if e.get("code") == "SESSION_BUSY"]
    assert busy and busy[0].get("retryPayload", {}).get("clientId") == "local-nodb"


@pytest.mark.asyncio
async def test_the_queue_preview_is_computed_without_parsing_on_the_loop():
    from core import session_pending
    sid = "s-preview"
    big = "x" * 500_000
    await session_pending.persist_pending(sid, user_message=big, content=None,
                                          agent_id="default", client_id="local-p1")
    await session_pending.persist_pending(
        sid, user_message=None, agent_id="default", client_id="local-p2",
        content=[{"type": "image", "source": {"data": "A" * 1000}},
                 {"type": "text", "text": "hello"}, {"type": "text", "text": "world"}])
    real_loads = session_pending.json.loads

    def small_only(text, *args, **kwargs):     # metadata is parsed, never the content
        assert len(text) < 10_000, "a queued message's content was parsed on the loop"
        return real_loads(text, *args, **kwargs)
    with patch.object(session_pending.json, "loads", side_effect=small_only):
        rows = await session_pending.list_pending(sid)
    assert [r["preview"] for r in rows] == ["x" * session_pending._PREVIEW_CHARS, "hello world"]


# ── P2c: the queue is pushed (docs/design/chat-turn-liveness.md S3) ─────────────

@pytest.mark.asyncio
async def test_a_message_queued_while_busy_pushes_the_queue(hub):
    """A send that cannot take the turn is queued and the session's state, with
    the queue naming it, is pushed to its tabs (the frontend's only queue)."""
    r = _router()
    sid = "s-pushq"
    unit = r.get_or_create_unit(sid, "default")
    unit._transition(SessionState.IDLE)
    assert hub.try_claim_turn(sid, "running")             # a turn holds the session
    a, b = _patched(_script())
    with a, b:
        events = [ev async for ev in r.run_conversation(
            agent_id="default", user_message="later", session_id=sid, client_id="local-pq")]
    assert any(e.get("type") == "accepted" and e.get("queued") for e in events)
    pushed = None
    for _ in range(50):
        await asyncio.sleep(0.02)
        snap = [e for e in hub._logs[sid].buf if e.get("type") == "session_state"]
        if snap:
            pushed = snap[-1]
            break
    assert pushed is not None, "queuing pushed no session_state"
    assert [p.get("client_id") for p in pushed["state"]["pending"]] == ["local-pq"]


@pytest.mark.asyncio
async def test_a_burst_of_queue_changes_shares_one_read(monkeypatch):
    """Several publishes asked for before the scheduled read starts share it (a
    burst of queued sends is one queue read, not one per send); a publish after
    the read began schedules a new one; a push cancelled before it ran does not
    silence the next."""
    from core import session_state
    reads = []

    async def entry(unit):
        reads.append(unit.session_id)
        return {"pending": []}
    monkeypatch.setattr(session_state, "session_state_entry", entry)
    unit = MagicMock(session_id="s-burst")
    router = MagicMock()
    router.get_unit.return_value = unit
    for _ in range(5):
        session_state.publish(router, "s-burst")
    await asyncio.sleep(0.05)
    assert reads == ["s-burst"]
    session_state.publish(router, "s-burst")
    await asyncio.sleep(0.05)
    assert reads == ["s-burst", "s-burst"]
    session_state.publish(router, "s-burst")
    session_state._SCHEDULED["s-burst"].cancel()
    await asyncio.sleep(0)
    session_state.publish(router, "s-burst")
    await asyncio.sleep(0.05)
    assert reads == ["s-burst"] * 3
