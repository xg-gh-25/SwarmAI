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
    # A turn's end enqueues a drain (chat-turn-liveness S1), so the worker may have
    # been started on the test function's loop, already closed here.
    task = router._drain_worker_task
    if task is not None and not task.done() and not task.get_loop().is_closed():
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
    """L6/AC7: a transition INTO IDLE (a turn ending: STREAMING→IDLE) fires
    _on_unit_state_change → enqueue_drain, so queued messages drain once the
    subprocess is free."""
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


# ─────────────────────────────────────────────────────────────────────────
# Cold-spawn coalesce (run_25d2b7b8) — the IDLE-only drain trigger strands a
# pending message when a session reaches COLD without a clean IDLE edge (e.g.
# after TOOL_CALL_LEAK_UNRECOVERED: streaming→dead→cold). These tests FORCE the
# new cold-spawn coalesce path: when the next send cold-spawns a session that
# has pending rows, those rows are merged INTO that send's own turn (not left
# for a transient IDLE edge the serial drain worker loses the race to).
# ─────────────────────────────────────────────────────────────────────────


def _make_cold_unit(session_id: str):
    """A MagicMock SessionUnit that looks alive but COLD (about to spawn)."""
    unit = MagicMock()
    unit.session_id = session_id
    unit.agent_id = "agent-1"
    unit.is_alive = True
    unit.state = SessionState.COLD
    unit.has_outstanding_tool_use = False
    unit._last_drained_seqs = []
    return unit


@pytest.mark.asyncio
async def test_cold_spawn_claims_pending_when_cold_and_pending_exist(wired):
    """AC1: a COLD unit with pending rows → _claim_cold_spawn_pending claims the
    whole set (FIFO) so the imminent send can coalesce them into its own turn."""
    router, sp = wired
    sid = "sess-cold-claim"
    router._units[sid] = _make_cold_unit(sid)
    await sp.persist_pending(sid, user_message="stranded-1", content=None, agent_id="a")
    await sp.persist_pending(sid, user_message="stranded-2", content=None, agent_id="a")

    claimed = await router._claim_cold_spawn_pending(router._units[sid], sid)
    assert [c.user_message for c in claimed] == ["stranded-1", "stranded-2"]
    # Claimed (claimed_at set) but NOT yet sent — delivery confirms sent later.
    assert await sp.count_pending(sid) == 2  # still sent=0 until mark_sent


@pytest.mark.asyncio
async def test_cold_spawn_noop_when_not_cold(wired):
    """AC4 guard: an IDLE/STREAMING unit is NOT a cold-spawn — the claim is a
    no-op so the existing serial-worker IDLE-edge drain owns that path."""
    router, sp = wired
    sid = "sess-not-cold"
    unit = _make_cold_unit(sid)
    unit.state = SessionState.IDLE  # not a cold-spawn turn
    router._units[sid] = unit
    await sp.persist_pending(sid, user_message="idle-path", content=None, agent_id="a")

    claimed = await router._claim_cold_spawn_pending(unit, sid)
    assert claimed == []
    assert await sp.count_pending(sid) == 1  # untouched, serial worker will drain


@pytest.mark.asyncio
async def test_cold_spawn_merge_puts_pending_first_arriving_last(wired):
    """AC1: pending FIFO first, the arriving user message LAST (a correction
    reads most-salient-last — the existing combine_pending semantics)."""
    router, sp = wired
    sid = "sess-merge"
    router._units[sid] = _make_cold_unit(sid)
    await sp.persist_pending(sid, user_message="older-pending", content=None, agent_id="a")
    claimed = await router._claim_cold_spawn_pending(router._units[sid], sid)

    merged = router._merge_pending_into_query(claimed, "arriving-now")
    # text path: pending joined before the arriving message, latest-last.
    assert merged == "older-pending\n\narriving-now"


@pytest.mark.asyncio
async def test_cold_spawn_finalize_marks_sent_on_success(wired):
    """AC2: after the coalesced send delivers (result observed, no error), the
    shared finalize helper flips ONLY the claimed seqs to sent=1."""
    router, sp = wired
    sid = "sess-finalize-ok"
    router._units[sid] = _make_cold_unit(sid)
    await sp.persist_pending(sid, user_message="deliver-me", content=None, agent_id="a")
    claimed = await router._claim_cold_spawn_pending(router._units[sid], sid)
    seqs = [c.pending_seq for c in claimed]

    await router._finalize_pending_delivery(sid, seqs, delivered_ok=True, saw_error=None)
    assert await sp.count_pending(sid) == 0  # flipped sent=1


@pytest.mark.asyncio
async def test_cold_spawn_finalize_rolls_back_on_error(wired):
    """AC2: a failed/aborted coalesced turn (error event or no result) rolls the
    claimed set back to pending (sent=0) so nothing is lost."""
    router, sp = wired
    sid = "sess-finalize-fail"
    router._units[sid] = _make_cold_unit(sid)
    await sp.persist_pending(sid, user_message="must-survive", content=None, agent_id="a")
    claimed = await router._claim_cold_spawn_pending(router._units[sid], sid)
    seqs = [c.pending_seq for c in claimed]

    await router._finalize_pending_delivery(sid, seqs, delivered_ok=False, saw_error="SESSION_BUSY")
    assert await sp.count_pending(sid) == 1  # still pending
    reclaim = await sp.claim_pending_batch(sid)
    assert len(reclaim) == 1, "rolled-back row must be re-claimable"


@pytest.mark.asyncio
async def test_cold_spawn_claim_atomic_no_double_delivery(wired):
    """AC3: the cold-spawn claim and the serial drain worker cannot both deliver
    the same seqs — claim_pending_batch is atomic, the second claimant gets []."""
    router, sp = wired
    sid = "sess-atomic"
    router._units[sid] = _make_cold_unit(sid)
    await sp.persist_pending(sid, user_message="only-once", content=None, agent_id="a")

    first = await router._claim_cold_spawn_pending(router._units[sid], sid)
    assert len(first) == 1
    # A concurrent serial-worker claim now sees the rows already claimed → [].
    second = await sp.claim_pending_batch(sid)
    assert second == [], "claimed rows must not be re-claimable (no double-delivery)"


@pytest.mark.asyncio
async def test_cold_spawn_coalesce_integration_delivers_and_marks_sent(wired, monkeypatch):
    """R28 forced-execution INTEGRATION: drive the REAL run_conversation cold-spawn
    path end-to-end — a COLD unit with pending rows, a stubbed unit.send that yields
    a terminal result. Assert (1) the pending text was COALESCED into the delivered
    query_content (pending FIFO first, arriving msg last), (2) the pending rows flip
    sent=1 via the finalize helper. This is the wiring test the per-helper unit tests
    cannot give — it proves run_conversation actually calls claim→merge→send→finalize.

    Mutation-proof: revert the coalesce block in run_conversation and this goes RED
    (pending text absent from delivered query_content + rows stay sent=0).
    """
    from unittest.mock import AsyncMock, patch
    router, sp = wired
    sid = "sess-cold-integ"

    # A real COLD unit (get_or_create then force COLD — the pre-send state).
    unit = router.get_or_create_unit(sid, "default")
    unit._transition(SessionState.COLD) if unit.state != SessionState.COLD else None

    # Two rows stranded in the pending queue (sent=0) — the strand this fix targets.
    await sp.persist_pending(sid, user_message="stranded-older", content=None, agent_id="a")
    await sp.persist_pending(sid, user_message="stranded-newer", content=None, agent_id="a")
    assert await sp.count_pending(sid) == 2

    # Capture what unit.send actually receives as query_content, yield a result.
    seen = {}
    async def _capture_send(**kwargs):
        seen["query_content"] = kwargs.get("query_content")
        yield {"type": "result", "sessionId": sid}

    router._prompt_builder = MagicMock()
    router._prompt_builder.build_options = AsyncMock(
        return_value=MagicMock(model="test", system_prompt="test")
    )

    with patch.object(unit, "send", side_effect=_capture_send), \
         patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}):
        events = []
        async for ev in router.run_conversation(
            session_id=sid, agent_id="default", user_message="arriving-now",
        ):
            events.append(ev)

    # (1) pending was coalesced INTO the delivered turn, FIFO + arriving last.
    qc = seen.get("query_content")
    assert isinstance(qc, str), f"expected text query_content, got {type(qc)}"
    assert "stranded-older" in qc and "stranded-newer" in qc, \
        "pending messages were NOT coalesced into the cold-spawn turn (strand bug)"
    assert qc.index("stranded-older") < qc.index("arriving-now"), \
        "pending must come BEFORE the arriving message (FIFO, correction-reads-last)"
    assert qc.endswith("arriving-now"), "the arriving message must be LAST"

    # (2) the coalesced pending rows were delivered → flipped sent=1 (not stranded).
    assert await sp.count_pending(sid) == 0, \
        "coalesced pending rows must be marked sent after a delivered turn"


@pytest.mark.asyncio
async def test_cold_spawn_rollback_on_abandoned_generator(wired):
    """R28 recovery-path (Gate-2 HIGH #1): if the SSE consumer disconnects mid-turn,
    run_conversation (an async generator) is CLOSED → GeneratorExit raises at the
    suspended `yield event`. GeneratorExit is a BaseException (not caught by
    `except Exception`), so WITHOUT the finally the claimed coalesced set would strand
    forever (sent=0, claimed_at SET, never re-claimable by the serial drain). This
    FORCES that path (aclose() the generator mid-stream) and asserts the pending set
    is rolled back to pending (re-claimable), not stranded.

    Mutation-proof: remove the finally block in run_conversation and this goes RED
    (count_pending stays correct but the row is claimed_at-SET → the re-claim assert
    at the end fails because claim_pending_batch skips claimed_at!=NULL rows).
    """
    from unittest.mock import AsyncMock, patch
    router, sp = wired
    sid = "sess-abandon"
    unit = router.get_or_create_unit(sid, "default")
    unit._transition(SessionState.COLD) if unit.state != SessionState.COLD else None

    await sp.persist_pending(sid, user_message="dont-strand-me", content=None, agent_id="a")

    # unit.send yields one event then WAITS — we abandon the generator at that yield.
    async def _hang_send(**kwargs):
        yield {"type": "assistant", "content": []}
        yield {"type": "result", "sessionId": sid}  # never reached (we aclose first)

    router._prompt_builder = MagicMock()
    router._prompt_builder.build_options = AsyncMock(
        return_value=MagicMock(model="test", system_prompt="test")
    )

    with patch.object(unit, "send", side_effect=_hang_send), \
         patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}):
        agen = router.run_conversation(session_id=sid, agent_id="default", user_message="arriving")
        # Cold resume yields pre-send events (queued / session_resuming) BEFORE the
        # claim+send-loop. Consume until the first send-loop event ("assistant"): by
        # then the claim has run and we're suspended INSIDE the send loop's `yield
        # event` — the exact GeneratorExit window the finally must cover.
        saw_assistant = False
        for _ in range(10):
            ev = await agen.__anext__()
            if ev.get("type") == "assistant":
                saw_assistant = True
                break
        assert saw_assistant, "never reached the send-loop yield (claim window)"
        # Simulate the SSE consumer disconnecting: close the generator → GeneratorExit.
        await agen.aclose()

    # The claimed set must have been ROLLED BACK by the finally — not stranded.
    # Proof it's re-claimable (claimed_at was reset to NULL):
    reclaim = await sp.claim_pending_batch(sid)
    assert len(reclaim) == 1, "abandoned-generator claimed set was STRANDED (not rolled back) — data loss!"
    assert reclaim[0].user_message == "dont-strand-me"


@pytest.mark.asyncio
async def test_cold_spawn_result_then_disconnect_keeps_sent(wired):
    """Gate-2 HIGH (2nd adversarial round): a terminal `result` is observed, then the
    SSE consumer disconnects AT the trailing yield (before the post-loop finalize).
    WITHOUT finalize-on-result, the `finally` would see a non-empty coalesced set and
    ROLL BACK an already-delivered set → the user re-sends → double-delivery. With the
    fix, `result` marks the set sent=1 immediately, so a later disconnect can NEVER
    reopen it.

    Mutation-proof: remove the finalize-on-result block (revert to post-loop-only
    finalize) and this goes RED — count_pending becomes 1 (rolled back) and the
    re-claim at the end returns the row, which this test asserts must NOT happen.
    """
    from unittest.mock import AsyncMock, patch
    router, sp = wired
    sid = "sess-result-disconnect"
    unit = router.get_or_create_unit(sid, "default")
    unit._transition(SessionState.COLD) if unit.state != SessionState.COLD else None

    await sp.persist_pending(sid, user_message="deliver-me-once", content=None, agent_id="a")

    # send yields a result (delivery confirmed) then a TRAILING frame — we disconnect
    # at the trailing frame, i.e. AFTER result was observed but at a later yield.
    async def _result_then_trailing(**kwargs):
        yield {"type": "assistant", "content": []}
        yield {"type": "result", "sessionId": sid}
        yield {"type": "assistant", "content": [{"type": "text", "text": "trailing"}]}

    router._prompt_builder = MagicMock()
    router._prompt_builder.build_options = AsyncMock(
        return_value=MagicMock(model="test", system_prompt="test")
    )

    with patch.object(unit, "send", side_effect=_result_then_trailing), \
         patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}):
        agen = router.run_conversation(session_id=sid, agent_id="default", user_message="arriving")
        # Consume PAST the result frame (so finalize-on-result has run), stop at the
        # trailing frame, then disconnect there.
        saw_result = False
        for _ in range(10):
            ev = await agen.__anext__()
            if ev.get("type") == "result":
                saw_result = True
                # pull ONE more (the trailing frame) then abandon at that yield
                await agen.__anext__()
                break
        assert saw_result, "never observed the result frame"
        await agen.aclose()

    # The delivered set must STAY sent=1 — NOT rolled back by the finally.
    assert await sp.count_pending(sid) == 0, \
        "delivered set was rolled back after result+disconnect → DOUBLE-DELIVERY risk"
    reclaim = await sp.claim_pending_batch(sid)
    assert len(reclaim) == 0, "delivered row became re-claimable → it would double-deliver"


@pytest.mark.asyncio
async def test_cold_spawn_multimodal_pending_gets_converted(wired):
    """Gate-2 MEDIUM (2nd adversarial round): the arriving message is block→path-hint
    converted BEFORE the coalesce merge. A claimed pending row carrying multimodal
    blocks flattens the whole turn to a block list, so WITHOUT a re-run of conversion
    the pending blocks reach the SDK UNCONVERTED (unsupported image/doc → rejection).
    This asserts the conversion runs AGAIN over the merged block list and sees the
    pending block.

    Mutation-proof: remove the post-merge conversion re-run and this goes RED (the
    conversion mock is never called with the pending block).
    """
    from unittest.mock import AsyncMock, patch
    router, sp = wired
    sid = "sess-mm-pending"
    unit = router.get_or_create_unit(sid, "default")
    unit._transition(SessionState.COLD) if unit.state != SessionState.COLD else None

    # A pending row carrying a multimodal (image) block.
    img_block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "ZZ"}}
    await sp.persist_pending(sid, user_message=None, content=[img_block], agent_id="a")

    captured = {}
    async def _fake_convert(blocks, session_id):
        captured["blocks"] = blocks
        # return a path-hint-ified copy (shape doesn't matter for the assert)
        return [{"type": "text", "text": "[image path hint]"}]

    sent_query = {}
    async def _capture_send(**kwargs):
        sent_query["query_content"] = kwargs.get("query_content")
        yield {"type": "assistant", "content": []}
        yield {"type": "result", "sessionId": sid}

    router._prompt_builder = MagicMock()
    router._prompt_builder.build_options = AsyncMock(
        return_value=MagicMock(model="test", system_prompt="test")
    )

    with patch.object(unit, "send", side_effect=_capture_send), \
         patch("core.session_router._convert_unsupported_blocks_to_path_hints",
               new=AsyncMock(side_effect=_fake_convert)), \
         patch("core.agent_defaults.build_agent_config", new_callable=AsyncMock, return_value={"model": "test"}):
        agen = router.run_conversation(session_id=sid, agent_id="default", user_message="arriving-text")
        async for _ in agen:
            pass

    # The conversion must have run over the merged block list containing the pending image.
    assert "blocks" in captured, "post-merge conversion was NOT called on the multimodal set"
    assert any(b.get("type") == "image" for b in captured["blocks"]), \
        "pending image block never reached the conversion → it would hit the SDK raw"
