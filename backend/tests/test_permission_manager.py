"""Property-based tests for PermissionManager.

# Feature: agent-code-refactoring, Property 2: Permission approve/check round-trip
# Feature: agent-code-refactoring, Property 3: Permission decision set/wait round-trip

Uses Hypothesis to verify that PermissionManager correctly tracks command
approvals and permission decisions.

**Validates: Requirements 2.4, 2.5, 2.6**
"""

import pytest
import asyncio
from hypothesis import given, strategies as st

from core.permission_manager import PermissionManager
from tests.helpers import PROPERTY_SETTINGS





class TestPermissionApproveCheckRoundTrip:
    """Property 2: Permission approve/check round-trip.

    **Validates: Requirements 2.4**

    For any session ID and command string, calling approve_command then
    is_command_approved shall return True. For any unapproved command,
    is_command_approved shall return False.
    """

    @given(
        session_id=st.text(min_size=1),
        command=st.text(min_size=1),
    )
    @PROPERTY_SETTINGS
    def test_approved_command_is_recognized(self, session_id: str, command: str):
        """approve_command then is_command_approved returns True.

        **Validates: Requirements 2.4**
        """
        pm = PermissionManager()
        pm.approve_command(session_id, command)
        assert pm.is_command_approved(session_id, command) is True

    @given(
        session_id=st.text(min_size=1),
        command=st.text(min_size=1),
    )
    @PROPERTY_SETTINGS
    def test_unapproved_command_is_not_recognized(self, session_id: str, command: str):
        """is_command_approved returns False for unapproved commands.

        **Validates: Requirements 2.4**
        """
        pm = PermissionManager()
        assert pm.is_command_approved(session_id, command) is False


class TestPermissionDecisionSetWaitRoundTrip:
    """Property 3: Permission decision set/wait round-trip.

    **Validates: Requirements 2.5, 2.6**

    For any request ID and decision ("approve" or "deny"), calling
    set_permission_decision before wait_for_permission_decision shall
    return the exact decision string.
    """

    @given(
        request_id=st.text(min_size=1),
        decision=st.sampled_from(["approve", "deny"]),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_set_then_wait_returns_exact_decision(self, request_id: str, decision: str):
        """set_permission_decision then wait_for_permission_decision returns exact decision.

        **Validates: Requirements 2.5, 2.6**

        Uses concurrent tasks because wait_for_permission_decision creates
        the asyncio.Event internally — set_permission_decision must be called
        after the wait has started to signal the correct event.
        """
        pm = PermissionManager()

        async def set_after_brief_delay():
            # Yield control so wait_for_permission_decision registers the event first
            await asyncio.sleep(0.01)
            pm.set_permission_decision(request_id, decision)

        wait_task = asyncio.create_task(
            pm.wait_for_permission_decision(request_id, timeout=5)
        )
        set_task = asyncio.create_task(set_after_brief_delay())

        result = await wait_task
        await set_task
        assert result == decision


class TestPermissionTimeout:
    """Fix #2/#3: bounded timeout + a DISTINCT timeout result.

    Previously the default was 7200s (2h) and timeout silently returned
    "deny" — indistinguishable from a real user denial, so the UI could not
    tell the user "审批超时" vs "you denied it". Now: timeout returns the
    sentinel "timeout" so the caller can emit a visible expiry message, and the
    default is a deliberate bounded value (NOT an unbounded/accidental one).

    run_6e780e00: the default was raised 300s→4h (PERMISSION_ANSWER_TIMEOUT_SECONDS)
    for PARITY with the ask gate — once the artificial 5s chain-timeout was removed,
    300s (5min) was too short for a human who steps away, and inconsistent with
    ask_question_gate's 4h. MUST stay < the lifecycle WAITING_INPUT watchdog (4h05m)
    so that watchdog remains the ultimate backstop.
    """

    def test_default_timeout_is_4h_matching_ask_gate(self):
        import inspect
        from core.permission_manager import PERMISSION_ANSWER_TIMEOUT_SECONDS
        from core.ask_question_manager import ASK_ANSWER_TIMEOUT_SECONDS
        sig = inspect.signature(PermissionManager.wait_for_permission_decision)
        # Default is the module constant (single source of truth), not a literal.
        assert sig.parameters["timeout"].default == PERMISSION_ANSWER_TIMEOUT_SECONDS
        # 4h — parity with the ask gate (the run_6e780e00 requirement).
        assert PERMISSION_ANSWER_TIMEOUT_SECONDS == 14400
        assert PERMISSION_ANSWER_TIMEOUT_SECONDS == ASK_ANSWER_TIMEOUT_SECONDS, \
            "permission approval timeout must match the ask gate (both 4h)"

    def test_permission_timeout_stays_below_waiting_input_watchdog(self):
        """The approval timeout MUST stay strictly below the lifecycle
        WAITING_INPUT watchdog, so the watchdog remains the ultimate backstop
        against a genuinely-stuck slot (never fires before a legit approval expires)."""
        from core.permission_manager import PERMISSION_ANSWER_TIMEOUT_SECONDS
        from core.lifecycle_manager import LifecycleManager
        assert PERMISSION_ANSWER_TIMEOUT_SECONDS < LifecycleManager.WAITING_INPUT_TIMEOUT_SECONDS, \
            "approval timeout must be < WAITING_INPUT watchdog (else watchdog reaps a live prompt)"

    @pytest.mark.asyncio
    async def test_timeout_returns_distinct_sentinel(self):
        pm = PermissionManager()
        # No one ever sets a decision → must time out fast and return "timeout".
        result = await pm.wait_for_permission_decision("req_never_answered", timeout=0.05)
        assert result == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_marks_pending_expired(self):
        pm = PermissionManager()
        pm.store_pending_request({"id": "req_x", "session_id": "s1", "status": "pending"})
        await pm.wait_for_permission_decision("req_x", timeout=0.05)
        # _pending_requests is cleaned up in finally; the durable record is gone.
        assert pm.get_pending_request("req_x") is None


class TestLiveWaiterAndSessionLookup:
    """Fix #1B: reconnect re-surface must only fire when a waiter is ALIVE.

    The durable _pending_requests store survives respawn, but the hook that
    awaits the decision does NOT — after a respawn the coroutine is cancelled
    and its event is popped in `finally`. Re-surfacing a request whose waiter
    is dead would let the user "approve" into the void. has_live_waiter is the
    respawn-immune liveness signal (event present == coroutine still blocked).
    """

    @pytest.mark.asyncio
    async def test_has_live_waiter_true_while_blocked(self):
        pm = PermissionManager()
        wait_task = asyncio.create_task(
            pm.wait_for_permission_decision("req_live", timeout=5)
        )
        await asyncio.sleep(0.01)  # let the event register
        assert pm.has_live_waiter("req_live") is True
        pm.set_permission_decision("req_live", "approve")
        await wait_task
        # After resolution the event is popped → no live waiter.
        assert pm.has_live_waiter("req_live") is False

    def test_has_live_waiter_false_when_no_waiter(self):
        pm = PermissionManager()
        pm.store_pending_request({"id": "req_orphan", "session_id": "s1", "status": "pending"})
        # Stored but nobody is awaiting (respawned) → not live.
        assert pm.has_live_waiter("req_orphan") is False

    def test_get_pending_for_session_filters_by_session(self):
        pm = PermissionManager()
        pm.store_pending_request({"id": "r1", "session_id": "sA", "status": "pending"})
        pm.store_pending_request({"id": "r2", "session_id": "sB", "status": "pending"})
        pm.store_pending_request({"id": "r3", "session_id": "sA", "status": "expired"})
        got = pm.get_pending_for_session("sA")
        ids = {r["id"] for r in got}
        assert ids == {"r1"}  # only pending + matching session
