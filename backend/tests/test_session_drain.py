"""Root-1 SSOT Phase 2 (L3) — serial drain worker forced-execution tests.

These are STEERING #11 recovery-path tests: they FORCE the drain to execute
(not just assert it compiles) and verify the exactly-once coalesce contract (AC3),
the F3 outstanding-tool_use guard, and the F4 rollback-on-failure path.

The drain worker delivers pending messages by calling run_conversation. We stub
run_conversation to a capturing async-generator so the test observes EXACTLY how
many turns are produced and with what coalesced payload — without spawning a real
CLI subprocess.

Methodology: real SQLite (session_pending primitives) + a MagicMock unit in IDLE.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest

from database.sqlite import SQLiteDatabase
from core.session_router import SessionRouter
from core.session_unit import SessionState


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
async def wired(db_path: Path, monkeypatch):
    """Migrated DB + SessionRouter with session_pending pointed at the tmp DB."""
    db = SQLiteDatabase(db_path=db_path)
    await db.initialize()
    import database
    import core.session_pending as sp
    monkeypatch.setattr(database, "db", db, raising=False)
    monkeypatch.setattr(sp, "_db_path_override", str(db_path), raising=False)
    sp._SEQ_LOCKS.clear()

    router = SessionRouter(prompt_builder=MagicMock())
    yield router, sp
    # Cancel any lazily-started drain worker so it doesn't outlive the test loop.
    task = router._drain_worker_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (Exception, BaseException):
            pass


def _make_idle_unit(session_id: str, *, outstanding_tool: bool = False) -> MagicMock:
    """A MagicMock SessionUnit that looks alive + IDLE to drain_pending."""
    unit = MagicMock()
    unit.session_id = session_id
    unit.agent_id = "agent-1"
    unit.is_alive = True
    unit.state = SessionState.IDLE
    unit.has_outstanding_tool_use = outstanding_tool
    unit._last_drained_seqs = []
    return unit


@pytest.mark.asyncio
async def test_drain_coalesces_pending_into_one_turn(wired):
    """AC3: two pending messages drain as exactly ONE coalesced turn and both
    flip to sent=1 (exactly-once)."""
    router, sp = wired
    sid = "sess-drain"
    router._units[sid] = _make_idle_unit(sid)

    await sp.persist_pending(sid, user_message="first", content=None, agent_id="a")
    await sp.persist_pending(sid, user_message="second", content=None, agent_id="a")
    assert await sp.count_pending(sid) == 2

    # Capture every run_conversation invocation.
    calls: list[dict] = []

    def _fake_run_conversation(**kwargs):
        calls.append(kwargs)

        async def _agen():
            # A real delivered turn yields a terminal `result` — mark_sent is now
            # gated on observing it (Gate-2 F1 fix).
            yield {"type": "result", "sessionId": sid}

        return _agen()

    router.run_conversation = _fake_run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)

    # Exactly ONE turn produced (coalesced), not two.
    assert len(calls) == 1, f"expected 1 coalesced turn, got {len(calls)}"
    # FIFO, latest last.
    assert calls[0]["user_message"] == "first\n\nsecond"
    assert calls[0]["_drained_pending"] is True
    # Both rows flipped to sent=1 — nothing left pending.
    assert await sp.count_pending(sid) == 0


@pytest.mark.asyncio
async def test_drain_rolls_back_on_yielded_error_event(wired):
    """Gate-2 F1 (CRITICAL): run_conversation reports SESSION_BUSY / EMPTY_MESSAGE
    as a YIELDED error event (not a raised exception). The drain must NOT mark the
    rows sent in that case — it must roll back so the message is never lost."""
    router, sp = wired
    sid = "sess-err-event"
    router._units[sid] = _make_idle_unit(sid)
    await sp.persist_pending(sid, user_message="must survive an error event", content=None, agent_id="a")

    def _error_event_run_conversation(**kwargs):
        async def _agen():
            # mimic run_conversation losing the slot to a real user mid-drain:
            # it yields a SESSION_BUSY error and RETURNS normally (no raise).
            yield {"type": "error", "code": "SESSION_BUSY", "message": "busy"}
        return _agen()

    router.run_conversation = _error_event_run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)  # must not raise

    # The row must NOT be marked sent — it must remain pending + re-claimable.
    assert await sp.count_pending(sid) == 1, "error-event drain wrongly marked the message sent (data loss!)"
    reclaim = await sp.claim_pending_batch(sid)
    assert len(reclaim) == 1


@pytest.mark.asyncio
async def test_drain_marks_sent_only_on_result_event(wired):
    """Gate-2 F1: mark_sent fires only when a terminal `result` event is observed
    with no error — the happy path stays correct."""
    router, sp = wired
    sid = "sess-ok-event"
    router._units[sid] = _make_idle_unit(sid)
    await sp.persist_pending(sid, user_message="deliver me", content=None, agent_id="a")

    def _ok_run_conversation(**kwargs):
        async def _agen():
            yield {"type": "assistant", "content": []}
            yield {"type": "result", "sessionId": sid}
        return _agen()

    router.run_conversation = _ok_run_conversation  # type: ignore[assignment]
    await router.drain_pending(sid)
    assert await sp.count_pending(sid) == 0, "happy-path drain should mark the message sent"


@pytest.mark.asyncio
async def test_drain_drops_corrupt_empty_payload_without_loop(wired):
    """Gate-2 F1: a pending row that combines to an empty payload is dropped
    (marked sent + logged), NOT routed through EMPTY_MESSAGE → rollback → reclaim
    → infinite loop."""
    router, sp = wired
    sid = "sess-corrupt"
    router._units[sid] = _make_idle_unit(sid)
    # Insert a degenerate pending row directly (empty content list → (None,None)).
    async with aiosqlite.connect(sp._get_db_path()) as conn:
        await conn.execute(
            "INSERT INTO messages (id, session_id, role, content, sent, pending_seq, created_at, updated_at) "
            "VALUES ('corrupt-1', ?, 'user', '[]', 0, 1, '2026-01-01', '2026-01-01')",
            (sid,),
        )
        await conn.commit()
    assert await sp.count_pending(sid) == 1

    called = False
    def _run_conversation(**kwargs):
        nonlocal called
        called = True
        async def _agen():
            if False:
                yield {}
        return _agen()
    router.run_conversation = _run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)  # must terminate, not loop
    assert called is False, "corrupt row should be dropped before delivery, not sent"
    assert await sp.count_pending(sid) == 0, "corrupt row should be cleared from the queue"


@pytest.mark.asyncio
async def test_cleanup_internal_clears_outstanding_tool_use(wired):
    """Gate-2 F3 (CRITICAL): _cleanup_internal (kill/crash/force_unstick path)
    clears _pending_tool_use_id so an abandoned WAITING_INPUT session does not
    leave has_outstanding_tool_use stuck True → drains blocked forever."""
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="sess-cleanup", agent_id="a")
    unit._pending_tool_use_id = "tool-abandoned"
    unit._pending_question = {"tool_use_id": "tool-abandoned", "questions": []}
    assert unit.has_outstanding_tool_use is True

    unit._cleanup_internal()

    assert unit.has_outstanding_tool_use is False, \
        "abandoned tool_use guard must clear on teardown or drains hang forever"
    assert unit._pending_question is None


@pytest.mark.asyncio
async def test_drain_noop_when_outstanding_tool_use(wired):
    """F3: a session with an outstanding tool_use must NOT drain (the pending
    message stays queued until the question is answered)."""
    router, sp = wired
    sid = "sess-tooluse"
    router._units[sid] = _make_idle_unit(sid, outstanding_tool=True)

    await sp.persist_pending(sid, user_message="while waiting", content=None, agent_id="a")

    called = False

    def _fake_run_conversation(**kwargs):
        nonlocal called
        called = True

        async def _agen():
            if False:
                yield {}

        return _agen()

    router.run_conversation = _fake_run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)

    assert called is False, "drain must not start a turn while tool_use outstanding"
    assert await sp.count_pending(sid) == 1, "pending message must be preserved"


@pytest.mark.asyncio
async def test_drain_noop_when_not_idle(wired):
    """Precondition: a non-IDLE unit is not drained (re-driven on next IDLE edge)."""
    router, sp = wired
    sid = "sess-streaming"
    unit = _make_idle_unit(sid)
    unit.state = SessionState.STREAMING  # busy
    router._units[sid] = unit
    await sp.persist_pending(sid, user_message="queued", content=None, agent_id="a")

    called = False

    def _fake_run_conversation(**kwargs):
        nonlocal called
        called = True
        async def _agen():
            if False:
                yield {}
        return _agen()
    router.run_conversation = _fake_run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)
    assert called is False
    assert await sp.count_pending(sid) == 1


@pytest.mark.asyncio
async def test_drain_rollback_on_send_failure(wired):
    """F4: if the coalesced send() fails, the claimed set rolls back to pending
    (claimed_at cleared, sent stays 0) so it re-coalesces on the next IDLE —
    no message is lost."""
    router, sp = wired
    sid = "sess-fail"
    router._units[sid] = _make_idle_unit(sid)
    await sp.persist_pending(sid, user_message="must survive", content=None, agent_id="a")

    def _boom_run_conversation(**kwargs):
        async def _agen():
            raise RuntimeError("spawn failed")
            yield {}  # unreachable; marks async generator
        return _agen()

    router.run_conversation = _boom_run_conversation  # type: ignore[assignment]

    await router.drain_pending(sid)  # must NOT raise

    # Message preserved as pending, and re-claimable (claimed_at was reset).
    assert await sp.count_pending(sid) == 1
    reclaim = await sp.claim_pending_batch(sid)
    assert len(reclaim) == 1, "rolled-back row must be re-claimable"


@pytest.mark.asyncio
async def test_waiting_input_send_raises_busy_when_tool_outstanding(wired):
    """AC5/F3: a real SessionUnit in WAITING_INPUT with a GENUINELY-OPEN
    tool_use (a LIVE waiter is blocked to receive the answer) must reject a new
    send() as SessionBusyError (→ router converts to pending), NOT kill→COLD (the
    abandoned-ask bug).

    NOTE (run_65f317db): the discriminator is now the LIVE WAITER, not merely
    _pending_tool_use_id being set. A DEAD-waiter WAITING_INPUT is reaped instead
    (see test_waiting_input_send_reaps_when_waiter_dead below). Here we register a
    live ask waiter so this is a real open question."""
    from core.session_unit import SessionUnit
    from core.exceptions import SessionBusyError
    from core.ask_question_manager import ask_question_manager

    unit = SessionUnit(session_id="sess-wi", agent_id="a")
    # Simulate the orchestrator having emitted an AskUserQuestion.
    unit.state = SessionState.WAITING_INPUT
    unit._pending_tool_use_id = "tool-99"
    unit._pending_question = {"tool_use_id": "tool-99", "questions": []}
    unit._client = object()  # non-None so the guard path is reached
    # A GENUINELY-open question: register a live waiter (the hook is blocked
    # awaiting the answer). Without this the session would be a dead-waiter zombie.
    ask_question_manager.register_waiter("tool-99")
    try:
        assert unit.has_outstanding_tool_use is True
        with pytest.raises(SessionBusyError):
            async for _ in unit.send("new msg while question open", MagicMock()):
                pass
        # The guard is NOT cleared by a rejected send — only the answer path clears it.
        assert unit._pending_tool_use_id == "tool-99"
    finally:
        ask_question_manager.discard_waiter("tool-99")


@pytest.mark.asyncio
async def test_waiting_input_send_reaps_when_waiter_dead(wired, monkeypatch):
    """AC1 (run_65f317db): a real SessionUnit in WAITING_INPUT whose waiter is
    DEAD (outstanding tool_use but NO live waiter — the approve-into-void deadlock)
    must NOT raise SessionBusyError forever. send() reaps it via
    force_unstick_waiting_input (→ COLD, falls through to spawn-with-resume)."""
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="sess-wi-dead", agent_id="a")
    unit.state = SessionState.WAITING_INPUT
    unit._pending_tool_use_id = "perm-dead"
    unit._pending_question = {"tool_use_id": "perm-dead", "request_id": "perm-dead"}
    unit._client = object()
    # Dead waiter: NO live waiter registered in either manager → reap path.
    reaped = {"called": False}

    async def _fake_force_unstick():
        reaped["called"] = True
        unit.state = SessionState.COLD
        unit._pending_tool_use_id = None

    monkeypatch.setattr(unit, "force_unstick_waiting_input", _fake_force_unstick)
    # Stop send() right after the WAITING_INPUT branch reaps (before real spawn).
    # _ensure_spawned is an async GENERATOR — the stub must be one too.
    async def _boom(*a, **k):
        raise RuntimeError("stop-after-reap")
        yield  # pragma: no cover — makes this an async generator
    monkeypatch.setattr(unit, "_ensure_spawned", _boom, raising=False)

    with pytest.raises(RuntimeError, match="stop-after-reap|spawn|COLD|Cannot"):
        async for _ in unit.send("msg after dead waiter", MagicMock()):
            pass
    assert reaped["called"] is True, "dead-waiter WAITING_INPUT must be reaped, not SessionBusyError"


@pytest.mark.asyncio
async def test_has_outstanding_tool_use_property():
    """has_outstanding_tool_use mirrors _pending_tool_use_id presence."""
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="sess-prop", agent_id="a")
    assert unit.has_outstanding_tool_use is False
    unit._pending_tool_use_id = "x"
    assert unit.has_outstanding_tool_use is True
    unit._pending_tool_use_id = None
    assert unit.has_outstanding_tool_use is False


@pytest.mark.asyncio
async def test_idle_transition_enqueues_drain(wired):
    """L6/AC7: a transition INTO IDLE (e.g. from recover_from_disconnect's clean
    IDLE) fires _on_unit_state_change → enqueue_drain, so queued messages drain
    once the subprocess is free. STREAMING→IDLE is the disconnect-recovery edge."""
    router, _ = wired
    router._on_unit_state_change("sess-idle-edge", SessionState.STREAMING, SessionState.IDLE)
    assert "sess-idle-edge" in router._drain_enqueued
    assert router._drain_queue.qsize() == 1
    # cleanup the lazily-started worker
    router._drain_queue.get_nowait()
    if router._drain_worker_task is not None:
        router._drain_worker_task.cancel()
        try:
            await router._drain_worker_task
        except BaseException:
            pass


@pytest.mark.asyncio
async def test_recover_from_disconnect_clean_idle_no_flag(wired):
    """L6/Option B-soft: recover_from_disconnect produces a clean IDLE (no
    generating-limbo flag) — the deleted flag attribute is never set True."""
    from core.session_unit import SessionUnit
    from unittest.mock import MagicMock as MM

    unit = MM()
    unit.state = SessionState.STREAMING
    unit.last_used = 0
    result = SessionUnit.recover_from_disconnect(unit)
    assert result is True
    unit._transition.assert_called_once_with(SessionState.IDLE)


@pytest.mark.asyncio
async def test_enqueue_drain_is_idempotent(wired):
    """enqueue_drain de-dupes: the same session queued twice yields one entry.

    Both enqueues happen synchronously (no await between them), so the worker
    cannot consume the queue in-between — we observe the dedupe directly."""
    router, _ = wired
    router.enqueue_drain("sess-x")
    router.enqueue_drain("sess-x")  # second — de-duped by _drain_enqueued
    assert router._drain_queue.qsize() == 1
    assert router._drain_enqueued == {"sess-x"}
    # Drain the queue + cancel worker so nothing leaks past the test loop.
    router._drain_queue.get_nowait()
    if router._drain_worker_task is not None:
        router._drain_worker_task.cancel()
        try:
            await router._drain_worker_task
        except BaseException:
            pass


@pytest.mark.asyncio
async def test_interrupt_clears_outstanding_tool_use_on_success():
    """GAP2 (audit 2026-06-23): interrupt() of a WAITING_INPUT session whose
    SDK interrupt SUCCEEDS (subprocess stays alive → IDLE) must clear
    _pending_tool_use_id + _pending_question. Otherwise has_outstanding_tool_use
    stays True and drain_pending no-ops forever — a queued message after a Stop
    on an open question never delivers until the next kill/TTL.
    """
    from unittest.mock import AsyncMock
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="sess-int-clear", agent_id="a")
    unit.state = SessionState.WAITING_INPUT
    unit._pending_tool_use_id = "tool-open"
    unit._pending_question = {"tool_use_id": "tool-open", "questions": []}
    # Mock the SDK client so interrupt() succeeds and the subprocess "stays alive".
    unit._client = MagicMock()
    unit._client.interrupt = AsyncMock(return_value=None)
    assert unit.has_outstanding_tool_use is True

    result = await unit.interrupt(timeout=1.0)

    assert result is True, "interrupt should report the turn was stopped"
    # PIT01 recycle fix: a user Stop (autonomous=False) now recycles the
    # poisoned subprocess to COLD instead of leaving it warm (IDLE). The
    # outstanding-tool_use guard MUST still be released (the GAP2 invariant
    # this test protects) — the interrupt success branch clears it BEFORE the
    # recycle, so a queued message after the Stop can still drain.
    assert unit.state == SessionState.COLD
    assert unit.has_outstanding_tool_use is False, \
        "interrupt success must release the outstanding-tool_use guard or drains hang"
    assert unit._pending_question is None


@pytest.mark.asyncio
async def test_interrupt_stale_preserves_pending():
    """GAP2 companion: when a new send() bumps _send_generation DURING the
    interrupt await (stale-interrupt path), interrupt() must NOT clear the
    pending state — the new turn owns it. The clear belongs ONLY to the
    success→IDLE branch, which the stale guard returns before reaching.
    """
    from unittest.mock import AsyncMock
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="sess-int-stale", agent_id="a")
    unit.state = SessionState.WAITING_INPUT
    unit._pending_tool_use_id = "tool-new-turn"
    unit._pending_question = {"tool_use_id": "tool-new-turn", "questions": []}

    async def _bump_then_return():
        # Simulate a concurrent send() starting while we await the SDK interrupt.
        unit._send_generation += 1

    unit._client = MagicMock()
    unit._client.interrupt = AsyncMock(side_effect=_bump_then_return)

    await unit.interrupt(timeout=1.0)

    # Stale branch returned early — pending state must survive (new send owns it).
    assert unit._pending_tool_use_id == "tool-new-turn", \
        "stale interrupt must NOT wipe pending state owned by the new turn"
    assert unit._pending_question is not None
