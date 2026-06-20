"""Property-based tests for PermissionManager.

# Feature: agent-code-refactoring, Property 2: Permission approve/check round-trip
# Feature: agent-code-refactoring, Property 3: Permission decision set/wait round-trip

Uses Hypothesis to verify that PermissionManager correctly tracks command
approvals and permission decisions.

**Validates: Requirements 2.4, 2.5, 2.6**
"""

import pytest
import asyncio
from hypothesis import given, strategies as st, settings

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
    """Fix #2/#3: shorter timeout + a DISTINCT timeout result.

    Previously the default was 7200s (2h) and timeout silently returned
    "deny" — indistinguishable from a real user denial, so the UI could not
    tell the user "审批超时" vs "you denied it". The session would appear to
    hang for up to 2h. Now: default 300s, and timeout returns the sentinel
    "timeout" so the caller can emit a visible expiry message.
    """

    def test_default_timeout_is_300_not_7200(self):
        import inspect
        sig = inspect.signature(PermissionManager.wait_for_permission_decision)
        assert sig.parameters["timeout"].default == 300

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
