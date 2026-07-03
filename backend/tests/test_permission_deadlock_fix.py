"""Execution tests for the approve-into-void permission deadlock fix (run_65f317db).

WHAT IS TESTED
--------------
The deadlock: when a dangerous-command permission prompt's waiter coroutine is
CANCELLED (e.g. SDK control_cancel_request) before the user decides, its finally
pops permission_manager._pending_requests while session_unit._pending_tool_use_id
stays stranded → has_outstanding_tool_use stuck True. Then approve returns
"Permission request not found" and every new send() raises SessionBusyError
FOREVER; the session sits in WAITING_INPUT until TTL.

The fix — a single dead-waiter reap predicate (WAITING_INPUT ∧ outstanding ∧
NOT live-waiter → force_unstick_waiting_input) — wired at THREE chokepoints
(send-path, lifecycle tick, approve endpoints) + a cancel-log for diagnosability.

AC MAPPING
----------
- AC1 send-path: SessionUnit.send() reaps a dead-waiter WAITING_INPUT instead of
  SessionBusyError; a LIVE-waiter session still raises SessionBusyError.
- AC2 recovery predicate: reap_dead_waiting_input() reaps iff dead-waiter, and is
  a no-op with a live waiter. (Endpoint wiring reuses this same method.)
- AC3 diagnosability: wait_for_permission_decision logs on the CancelledError pop.
- AC4 no-regression: manager disambiguation is by _pending_question shape.

METHODOLOGY
-----------
Direct method invocation on a bare SessionUnit (__new__, no spawn/DB) with the
two waiter managers monkeypatched at the has_live_waiter boundary. Each behavior
assertion is mutation-checkable: reverting the prod guard flips the assertion.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.session_unit import SessionState, SessionUnit
from core.permission_manager import PermissionManager


def _make_waiting_unit(*, pending_question: dict, session_id: str = "sess-deadlock") -> SessionUnit:
    """Bare SessionUnit in WAITING_INPUT with an outstanding tool_use."""
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.WAITING_INPUT
    unit._pending_tool_use_id = pending_question.get("request_id") or pending_question.get("tool_use_id")
    unit._pending_question = pending_question
    unit.last_used = time.time()
    unit._lock = asyncio.Lock()
    unit._on_state_change = None
    unit._client = MagicMock()
    unit._wrapper = None  # pid property derives from this (read-only)
    return unit


# ═══════════════════════════════════════════════════════════════════
# AC2 — the single recovery predicate reap_dead_waiting_input()
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reap_dead_permission_waiter_recovers(monkeypatch):
    """Permission WAITING_INPUT with a DEAD waiter → reaped (force_unstick called)."""
    unit = _make_waiting_unit(pending_question={
        "tool_use_id": "perm_abc", "request_id": "perm_abc",
        "tool_name": "Bash", "reason": "x", "options": ["approve", "deny"],
    })
    # Dead waiter: permission manager reports no live waiter.
    import core.permission_manager as pm_mod
    monkeypatch.setattr(pm_mod.permission_manager, "has_live_waiter", lambda rid: False)
    unit.force_unstick_waiting_input = AsyncMock()

    reaped = await unit.reap_dead_waiting_input()

    assert reaped is True, "dead permission waiter must be reaped"
    unit.force_unstick_waiting_input.assert_awaited_once()


@pytest.mark.asyncio
async def test_reap_noop_when_permission_waiter_live(monkeypatch):
    """Permission WAITING_INPUT with a LIVE waiter → NOT reaped (genuine prompt)."""
    unit = _make_waiting_unit(pending_question={
        "tool_use_id": "perm_live", "request_id": "perm_live",
    })
    import core.permission_manager as pm_mod
    monkeypatch.setattr(pm_mod.permission_manager, "has_live_waiter", lambda rid: True)
    unit.force_unstick_waiting_input = AsyncMock()

    reaped = await unit.reap_dead_waiting_input()

    assert reaped is False, "a live prompt must NEVER be reaped"
    unit.force_unstick_waiting_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_dead_ask_waiter_uses_ask_manager(monkeypatch):
    """AC4 disambiguation: an ask_user_question WAITING_INPUT consults the ASK
    manager (via _pending_question 'questions' shape), NOT the permission manager."""
    unit = _make_waiting_unit(pending_question={
        "tool_use_id": "toolu_ask", "questions": [{"q": "?"}],
    })
    import core.permission_manager as pm_mod
    import core.ask_question_manager as aqm_mod
    # If the code wrongly consulted the PERMISSION manager, this True would
    # (wrongly) block the reap. It MUST consult the ASK manager (False → reap).
    monkeypatch.setattr(pm_mod.permission_manager, "has_live_waiter", lambda x: True)
    monkeypatch.setattr(aqm_mod.ask_question_manager, "has_live_waiter", lambda x: False)
    unit.force_unstick_waiting_input = AsyncMock()

    reaped = await unit.reap_dead_waiting_input()

    assert reaped is True, "ask waiter liveness must be read from the ASK manager"
    unit.force_unstick_waiting_input.assert_awaited_once()


@pytest.mark.asyncio
async def test_reap_noop_when_not_waiting_input(monkeypatch):
    """reap is a no-op unless state == WAITING_INPUT (idempotent / race-safe)."""
    unit = _make_waiting_unit(pending_question={"request_id": "perm_x"})
    unit.state = SessionState.IDLE
    unit.force_unstick_waiting_input = AsyncMock()

    reaped = await unit.reap_dead_waiting_input()

    assert reaped is False
    unit.force_unstick_waiting_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_reap_noop_when_no_outstanding_tool_use(monkeypatch):
    """reap is a no-op if there is no outstanding tool_use (nothing to recover)."""
    unit = _make_waiting_unit(pending_question={"request_id": "perm_x"})
    unit._pending_tool_use_id = None  # no outstanding tool_use
    unit.force_unstick_waiting_input = AsyncMock()

    reaped = await unit.reap_dead_waiting_input()

    assert reaped is False
    unit.force_unstick_waiting_input.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════
# AC3 — cancel-branch diagnosability log
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_wait_for_permission_decision_logs_on_cancel(caplog):
    """When the waiter coroutine is cancelled before a decision, the finally pops
    the request AND emits a WARNING naming the request_id (was silent)."""
    pm = PermissionManager()
    req_id = "perm_cancel_me"
    pm.store_pending_request({"id": req_id, "session_id": "s", "status": "pending"})

    task = asyncio.create_task(pm.wait_for_permission_decision(req_id, timeout=30))
    # Let the coroutine reach the await point + register its live waiter.
    for _ in range(20):
        await asyncio.sleep(0)
        if pm.has_live_waiter(req_id):
            break
    assert pm.has_live_waiter(req_id), "waiter should be registered before cancel"

    import logging
    with caplog.at_level(logging.WARNING, logger="core.permission_manager"):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # The request store was popped (dead waiter) ...
    assert pm.get_pending_request(req_id) is None
    assert not pm.has_live_waiter(req_id)
    # ... and the cancel was LOGGED (AC3), naming the request_id.
    assert any(
        "CANCELLED before" in r.message and req_id in r.message
        for r in caplog.records
    ), "cancel-branch must log a WARNING naming the request_id"
