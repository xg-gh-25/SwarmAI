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
