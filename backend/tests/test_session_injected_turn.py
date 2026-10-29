"""Tests for CLI-initiated turns end to end: SessionUnit + StreamingOrchestrator +
SessionRouter on top of core.cli_stream_reader.

A background task that finishes while nothing is sending makes the CLI start a
turn of its own. Before the stream reader that turn parked in the SDK buffer:
invisible, and its first tool call waited for the user's next message. These
tests drive the real unit, orchestrator and router with a fake SDK client whose
frames are recorded from the real CLI (tests/fixtures/cli_stream/), and check:

- AC2: the turn is persisted (label ``injected``, its own client_id) and the unit
  reports STREAMING while it runs and IDLE after;
- AC4: while the CLI is not quiescent a user send is refused with
  SessionBusyError before anything is written, the drain waits, and the CLI's
  idle report re-drives it;
- AC6: an IDLE unit with background work survives slot eviction, the TTL and
  the orphan reaper;
- the single writer claims the turn under a server-side uuid before writing, and
  a unit with a reader never falls back to a second stream reader.
"""
from __future__ import annotations

import asyncio
import json
import time
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from claude_agent_sdk._internal.message_parser import parse_message

from core.cli_stream_reader import CliStreamReader
from core.exceptions import SessionBusyError
from core.lifecycle_manager import LifecycleManager
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import write_user_message

FIX = Path(__file__).parent / "fixtures" / "cli_stream"


def _frames(name: str) -> list[dict]:
    return [json.loads(ln) for ln in (FIX / f"{name}.jsonl").read_text().splitlines() if ln.strip()]


def _injected_turn_frames() -> list[dict]:
    """idle_notification from the CLI's first idle on: the background task's
    notification, then the turn the CLI starts for it, then idle."""
    frames = [f for f in _frames("idle_notification") if "_marker" not in f]
    i = next(i for i, f in enumerate(frames)
             if f.get("subtype") == "session_state_changed" and f.get("state") == "idle")
    return frames[i:]


class FakeClient:
    """SDK client stand-in: receive_messages() over a queue, query() recorded."""

    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.written: list = []

    async def receive_messages(self):
        while True:
            item = await self.q.get()
            if item is None:
                return
            msg = parse_message(item)
            if msg is not None:
                yield msg

    async def query(self, prompt, session_id="default"):
        if isinstance(prompt, str):
            self.written.append(prompt)
        else:
            async for m in prompt:
                self.written.append(m)

    def receive_response(self):  # pragma: no cover - must never be called with a reader
        raise AssertionError("receive_response() read while a stream reader exists")

    def feed(self, frames: list[dict]) -> None:
        for f in frames:
            self.q.put_nowait(f)


async def _until(pred, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


_ROUTERS: list[SessionRouter] = []


#: Fire-and-forget tasks the code under test starts by design and a test may leave
#: pending: (coroutine name, file). Anything else left on the loop is reported as a
#: warning (not a failure: a new fire-and-forget task must not make this file flaky).
_EXPECTED_BACKGROUND = {
    ("go", "session_state.py"),    # session_state.publish: the state push (reads the DB)
    ("kick", "turn_changes.py"),   # TurnChanges.start: the per-send start snapshot
    ("record_token_usage", "sqlite.py"),  # the result's usage row
}


@pytest_asyncio.fixture(autouse=True, loop_scope="function")
async def _no_task_outlives_the_test(monkeypatch):
    """Close every reader and cancel every router task a test started, also when
    an assertion failed first: a reader task left pending on the fake client's
    queue would otherwise hang the event loop's teardown."""
    readers: list[CliStreamReader] = []
    orig_start = CliStreamReader.start

    def _start(self):
        readers.append(self)
        orig_start(self)

    monkeypatch.setattr(CliStreamReader, "start", _start)
    _ROUTERS.clear()
    yield
    tasks = []
    for r in readers:
        r.close()
        if r._task is not None:
            tasks.append(r._task)
    for router in _ROUTERS:
        for t in router._injected_tasks.values():
            t.cancel()
            tasks.append(t)
    # The session_state push a test left on its loop (it reads the DB): abandoned
    # mid-DB-call when the loop closes, its aiosqlite worker posts to a closed
    # loop, raises and dies, and the next pool close waits on it forever (the
    # teardown / exit hang, 2026-10-24). That one is expected: cancel it here.
    # Any OTHER leftover task is cancelled too (so teardown cannot hang) and
    # reported as a warning.
    current = asyncio.current_task()
    unexpected = []
    for t in asyncio.all_tasks():
        if t is current or t.done() or t in tasks:
            continue
        code = getattr(t.get_coro(), "cr_code", None)
        if not (code and (code.co_name, Path(code.co_filename).name) in _EXPECTED_BACKGROUND):
            unexpected.append(repr(t)[:200])
        t.cancel()
        tasks.append(t)
    if tasks:
        await asyncio.wait(tasks, timeout=2)
    # This loop's pools, closed while it still runs (reset_database's teardown
    # runs on the session loop, after this loop is gone).
    from database.sqlite import close_loop_pools
    await close_loop_pools()
    if unexpected:
        warnings.warn(f"tasks left pending by the test (cancelled): {unexpected}", stacklevel=1)


def _router() -> SessionRouter:
    router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    _ROUTERS.append(router)
    return router


def _live_unit(router: SessionRouter, sid: str = "sess-inj") -> tuple[SessionUnit, FakeClient]:
    """A router-owned unit with a fake client and its stream reader, as _spawn
    leaves it (IDLE, reader started)."""
    unit = router.get_or_create_unit(sid, "default")
    # The pending-message drain is not under test here (it has its own tests).
    router.enqueue_drain = MagicMock()
    client = FakeClient()
    unit._client = client
    unit._last_turn_clean = True
    unit._transition(SessionState.IDLE)
    unit._start_stream_reader(client)
    return unit, client


# ── AC2: a CLI-initiated turn is persisted, STREAMING while it runs ──────────


async def test_injected_turn_is_persisted_and_the_unit_returns_to_idle():
    router = _router()
    unit, client = _live_unit(router)
    rows: list[dict] = []

    async def _persist(session_id, blocks, model, *, label="", client_id=None):
        rows.append({"sid": session_id, "label": label, "client_id": client_id,
                     "state": unit.state, "blocks": list(blocks)})
        return list(blocks)

    router._persist_assistant_blocks = _persist
    client.feed(_injected_turn_frames())
    await _until(lambda: rows and router._injected_tasks.get(unit.session_id) is not None
                 and router._injected_tasks[unit.session_id].done())

    texts = [b.get("text") for r in rows for b in r["blocks"] if isinstance(b, dict)]
    assert "NOTIFIED" in texts
    # the turn's outcome block (core/turn_writer.record_outcome) lands on the turn's row,
    # or as a row of its own under the same key when that row is not stored yet
    assert {r["label"] for r in rows} <= {"injected", "injected-results", "injected-outcome"}
    keys = {r["client_id"] for r in rows}
    assert len(keys) == 1 and next(iter(keys)).startswith("injected-")
    assert all(r["sid"] == unit.session_id for r in rows)
    assert rows[0]["state"] == SessionState.STREAMING
    assert unit.state == SessionState.IDLE
    assert unit.cli_quiescent
    unit._stream_reader.close()


async def test_injected_turn_waits_while_a_compact_holds_the_subprocess():
    router = _router()
    unit, client = _live_unit(router)
    router._persist_assistant_blocks = AsyncMock(side_effect=lambda s, b, m, **k: list(b))
    async with unit._client_io:
        client.feed(_injected_turn_frames())
        await _until(lambda: len(unit._injected_turns) == 1)
        await asyncio.sleep(0.05)
        assert unit.state == SessionState.IDLE  # not started under the held lock
    # the next IDLE edge re-drives it (here: the router is told directly)
    router.enqueue_injected(unit.session_id)
    await _until(lambda: router._injected_tasks[unit.session_id].done())
    assert router._persist_assistant_blocks.await_count >= 1
    unit._stream_reader.close()


async def test_a_turn_queued_during_compact_runs_when_compact_releases_the_subprocess():
    """compact() stays IDLE (no IDLE edge follows it): a CLI-initiated turn that
    arrived while compact held _client_io must still reach the sink."""
    router = _router()
    unit, client = _live_unit(router)
    router._persist_assistant_blocks = AsyncMock(side_effect=lambda s, b, m, **k: list(b))
    compact_turn = [f for f in _frames("compact")[18:29] if "_marker" not in f]
    real_query = client.query

    async def query(prompt, session_id="default"):
        await real_query(prompt, session_id)
        # the compact turn, then the CLI's own turn for a finished background task
        client.feed(compact_turn + _injected_turn_frames())
        # let the reader route it and the sink try (and find _client_io held)
        await _until(lambda: len(unit._injected_turns) == 1)
        await asyncio.sleep(0.05)

    client.query = query
    out = await unit.compact()
    assert out["success"] is True, out
    await _until(lambda: router._persist_assistant_blocks.await_count >= 1)
    assert not unit._injected_turns
    unit._stream_reader.close()


# ── AC4: the send gate and the drain wait for quiescence ────────────────────


async def test_send_is_refused_before_writing_while_the_cli_runs_its_own_turn():
    router = _router()
    unit, client = _live_unit(router)
    unit._on_cli_event = None  # keep the injected turn queued: no sink
    frames = _injected_turn_frames()
    init = next(i for i, f in enumerate(frames) if f.get("subtype") == "init")
    client.feed(frames[:init + 1])  # notification + the CLI's own init: turn open
    await _until(lambda: len(unit._injected_turns) == 1)
    assert not unit.cli_quiescent
    with pytest.raises(SessionBusyError):
        async for _ in unit.send("hi", MagicMock(), app_session_id="app-1"):
            pass
    assert client.written == []
    assert unit.state == SessionState.IDLE
    unit._stream_reader.close()


async def test_send_is_queued_when_a_cli_turn_takes_the_unit_during_its_waits():
    """The sink may switch the unit to STREAMING while send() waits for a
    streaming slot: the message must become pending (SessionBusyError), not be
    answered with a 'send again' error and dropped."""
    router = _router()
    unit, client = _live_unit(router)
    router._persist_assistant_blocks = AsyncMock(side_effect=lambda s, b, m, **k: list(b))
    frames = _injected_turn_frames()
    upto_tool = next(i for i, f in enumerate(frames) if f.get("type") == "assistant")

    async def slot_wait():
        client.feed(frames[:upto_tool + 1])  # the CLI's own turn starts meanwhile
        await _until(lambda: unit.state == SessionState.STREAMING)

    unit._await_streaming_slot = slot_wait
    with pytest.raises(SessionBusyError):
        async for _ in unit.send("hi", MagicMock(), app_session_id="app-1"):
            pass
    assert client.written == []
    client.feed(frames[upto_tool + 1:])
    await _until(lambda: unit.state == SessionState.IDLE and unit.cli_quiescent)
    unit._stream_reader.close()


async def test_a_transferred_turn_does_not_keep_the_cli_busy():
    """Notification race: the CLI folded the user's message into its own turn
    and the reader handed that turn to the user's claim. The queued copy must not
    hold quiescence (a pending message would wait for the next idle report)."""
    router = _router()
    unit, client = _live_unit(router)
    unit._on_cli_event = None  # no sink: inspect the queue the reader left
    race = [f for f in _frames("race_fold") if "_marker" not in f]
    first_idle = next(i for i, f in enumerate(race)
                      if f.get("subtype") == "session_state_changed" and f.get("state") == "idle")
    first = unit._stream_reader.claim("00000000-0000-4000-8000-000000000001")
    client.feed(race[:first_idle + 1])
    async for _ in first:
        pass
    second = unit._stream_reader.claim("00000000-0000-4000-8000-000000000002")
    client.feed(race[first_idle + 1:])
    async for _ in second:
        pass
    await _until(lambda: unit._stream_reader.quiescent)
    assert len(unit._injected_turns) == 1 and unit._injected_turns[0].transferred
    assert unit.cli_quiescent
    assert not unit.has_background_work
    unit._stream_reader.close()


async def test_a_dead_reader_at_idle_is_respawned_not_reported_busy():
    """The CLI exited between turns: the unit must not look busy forever. The drain is
    re-driven, and send() respawns with --resume instead of queuing the message."""
    router = _router()
    unit, client = _live_unit(router)
    unit._sdk_session_id = "sdk-keep"
    client.q.put_nowait(None)  # the CLI's stream ends
    await _until(lambda: unit._stream_reader.dead)
    assert unit.cli_quiescent and not unit.has_background_work
    router.enqueue_drain.assert_called_with(unit.session_id)

    crashes: list = []

    async def _crash(*, clear_identity=False):
        crashes.append(clear_identity)
        unit._client = None
        unit._transition(SessionState.DEAD)
        unit._transition(SessionState.COLD)

    async def _spawn_stop(options, config):
        yield {"_abort": True}

    unit._crash_to_cold_async = _crash
    unit._ensure_spawned = _spawn_stop
    async for _ in unit.send("hi", MagicMock(), app_session_id="app-1"):
        pass
    assert crashes == [False]
    assert unit._sdk_session_id == "sdk-keep"
    assert (await unit.compact())["success"] is False


async def test_an_abandoned_user_turn_keeps_the_session_identity():
    """The CLI went idle with the user's turn still open: the turn ends with a
    TURN_ABANDONED error, the unit goes back to IDLE with its subprocess and --resume
    identity; a message the CLI never took is queued (SessionBusyError) instead."""
    router = _router()
    unit, client = _live_unit(router)
    unit._sdk_session_id = "sdk-keep"
    running = {"type": "system", "subtype": "session_state_changed", "state": "running", "session_id": "S"}
    idle = {"type": "system", "subtype": "session_state_changed", "state": "idle", "session_id": "S"}
    # the CLI reports the same session it resumed (the orchestrator records it)
    init = {"type": "system", "subtype": "init", "session_id": "sdk-keep", "cwd": "/", "tools": [],
            "mcp_servers": [], "model": "m", "permissionMode": "default"}
    real_query = client.query

    async def query_cut(prompt, session_id="default"):
        await real_query(prompt, session_id)
        client.feed([running, init, idle])  # a turn opened, then idle without a result

    client.query = query_cut
    events = [e async for e in unit.send("hi", MagicMock(), app_session_id="app-1")]
    assert any(e.get("code") == "TURN_ABANDONED" for e in events)
    assert unit.state == SessionState.IDLE
    assert unit._sdk_session_id == "sdk-keep" and unit._client is client

    async def query_skipped(prompt, session_id="default"):
        await real_query(prompt, session_id)
        client.feed([running, idle])  # the CLI never took it

    client.query = query_skipped
    # (a cut turn leaves the subprocess not-clean, so the next send would recycle it
    # with --resume first; reset that here to drive the second case on the same CLI)
    unit._last_turn_clean = True
    with pytest.raises(SessionBusyError):
        async for _ in unit.send("again", MagicMock(), app_session_id="app-1"):
            pass
    assert unit.state == SessionState.IDLE and unit._sdk_session_id == "sdk-keep"


async def test_a_buffered_injected_turn_is_not_killed_as_a_zombie():
    """A CLI-initiated turn the sink runs after it already finished drains in
    milliseconds: the fast-empty-turn guards measure it from its own first frame."""
    unit = SessionUnit(session_id="z1", agent_id="default")
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    unit._streaming_start_time = time.time()
    ts = unit._stream_reader.claim("00000000-0000-4000-8000-00000000000b")
    ts.opened_at = unit._streaming_start_time - 30
    unit._turn_stream = ts
    started = unit._streaming_orchestrator._turn_started_at()
    assert time.time() - started >= 30


async def test_drain_waits_for_quiescence_and_the_idle_report_redrives_it():
    router = _router()
    unit = MagicMock()
    unit.is_alive = True
    unit.state = SessionState.IDLE
    unit.has_outstanding_tool_use = False
    unit.cli_quiescent = False
    router._units["s1"] = unit
    import core.session_pending as sp
    with patch.object(sp, "claim_pending_batch", new=AsyncMock(return_value=[])) as claim:
        await router.drain_pending("s1")
        claim.assert_not_awaited()
        unit.cli_quiescent = True
        await router.drain_pending("s1")
        claim.assert_awaited_once()
    with patch.object(router, "enqueue_drain") as enq:
        router._on_unit_cli_event("s1", "idle")
        enq.assert_called_once_with("s1")


async def test_reader_idle_report_reaches_the_router():
    router = _router()
    unit, client = _live_unit(router)
    client.feed([{"type": "system", "subtype": "session_state_changed",
                  "state": "idle", "session_id": "S"}])
    await _until(lambda: router.enqueue_drain.called)
    router.enqueue_drain.assert_called_with(unit.session_id)
    unit._stream_reader.close()


# ── AC6: background work survives routine reclaim ──────────────────────────


def _bg_unit(sid: str) -> SessionUnit:
    unit = SessionUnit(session_id=sid, agent_id="default")
    unit._transition(SessionState.IDLE)
    unit.last_used = time.time() - 10 * 24 * 3600
    unit._hooks_enqueued = True
    unit.kill = AsyncMock()
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    client.feed([_frame("task_started")])
    return unit


def _frame(subtype: str) -> dict:
    """The recorded frame of that subtype (the real CLI's field set)."""
    return next(f for f in _frames("idle_notification") if f.get("subtype") == subtype)


async def test_background_task_protects_an_owned_unit_but_not_an_orphan():
    """A unit some window still owns survives the TTL while its CLI runs a background
    task; an orphan does not get that protection (no window would see the turn, and a
    never-ending task would pin the slot)."""
    unit = _bg_unit("bg-sess")
    await _until(lambda: unit.has_background_work)

    lm_router = MagicMock()
    lm_router.list_units.return_value = [unit]
    mgr = LifecycleManager(router=lm_router)
    mgr._release_session_state = MagicMock()
    await mgr._check_ttl()
    with patch.object(LifecycleManager, "_owned_session_ids", return_value={unit.session_id}):
        await mgr._check_orphan_sessions()
    unit.kill.assert_not_awaited()  # owned + background work: kept

    with patch.object(LifecycleManager, "_owned_session_ids", return_value=set()):
        await mgr._check_orphan_sessions()
    unit.kill.assert_awaited()  # orphan: reaped despite the task
    unit.kill.reset_mock()

    orphan = _bg_unit("bg-orphan")
    await _until(lambda: orphan.has_background_work)
    router = _router()
    router._lifecycle_manager = None
    router._units[orphan.session_id] = orphan
    with patch("routers.settings.owned_session_ids", return_value=set()):
        assert await router._evict_idle(exclude=SessionUnit(session_id="x", agent_id="d"),
                                        force=True) is True
    orphan.kill.assert_awaited()

    # the task finishes → the same unit is reclaimable again
    unit._client.feed([_frame("task_notification")])
    await _until(lambda: not unit.has_background_work)
    await mgr._check_ttl()
    unit.kill.assert_awaited()
    unit._stream_reader.close()


# ── the single writer and the no-second-reader rule ─────────────────────────


async def test_write_user_message_claims_before_writing_under_a_server_uuid():
    unit = SessionUnit(session_id="w1", agent_id="default")
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    ts = await write_user_message(unit, "hello", kind="human")
    assert unit._turn_stream is ts  # the turn the unit reads, also after a pause
    assert len(client.written) == 1
    msg = client.written[0]
    assert msg["message"] == {"role": "user", "content": "hello"}
    assert msg["uuid"] == ts.uuid and len(ts.uuid) == 36
    assert not unit.cli_quiescent  # the claim waits for its turn
    unit._stream_reader.close()


async def test_the_gate_mark_reaches_the_claim():
    """A task finishes between send()'s gate and the write: the CLI's next turn is its
    own and goes to the sink, not to the user's claim."""
    unit = SessionUnit(session_id="w5", agent_id="default")
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    unit._record_gate_mark()  # the gate saw a quiescent CLI
    client.feed([_frame("task_notification")])
    await _until(lambda: unit._stream_reader.notif_count == 1)
    ts = await write_user_message(unit, "hello", kind="human")
    client.feed([{"type": "system", "subtype": "init", "session_id": "S", "cwd": "/", "tools": [],
                  "mcp_servers": [], "model": "m", "permissionMode": "default"}])
    await _until(lambda: len(unit._injected_turns) == 1)
    assert not ts.closed and unit._gate_mark is None  # consumed once
    unit._stream_reader.close()


async def test_a_failed_write_withdraws_the_claim():
    unit = SessionUnit(session_id="w2", agent_id="default")
    client = FakeClient()
    client.query = AsyncMock(side_effect=OSError("pipe closed"))
    unit._client = client
    unit._start_stream_reader(client)
    with pytest.raises(OSError):
        await write_user_message(unit, "hello", kind="human")
    assert unit.cli_quiescent
    unit._stream_reader.close()


async def test_no_turn_stream_with_a_reader_raises_instead_of_reading_twice():
    unit = SessionUnit(session_id="w3", agent_id="default")
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    with pytest.raises(RuntimeError, match="no turn stream"):
        unit._streaming_orchestrator._turn_iter()
    unit._stream_reader.close()


async def test_teardown_closes_the_reader_and_drops_queued_turns():
    unit = SessionUnit(session_id="w4", agent_id="default")
    client = FakeClient()
    unit._client = client
    unit._start_stream_reader(client)
    reader = unit._stream_reader
    waiting = reader.claim("00000000-0000-4000-8000-00000000000a")
    unit._injected_turns.append(object())
    unit._cleanup_internal()
    assert unit._stream_reader is None and not unit._injected_turns
    assert not unit.has_background_work
    with pytest.raises(Exception):
        async for _ in waiting:
            pass
    assert isinstance(reader, CliStreamReader)
