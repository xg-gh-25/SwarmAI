"""Property-based exploration test for parallel chat session blocking bug.

**Bugfix: parallel-chat-session-blocking, Property 1: Fault Condition**

Tests that two concurrent ``_execute_on_session()`` calls with
``session_id=None``, ``app_session_id=None``, and the same ``agent_id``
do NOT block each other with ``SESSION_BUSY``.  Each new session should
receive a unique lock key (UUID) and proceed independently.

This is a BUG CONDITION EXPLORATION test.  On UNFIXED code these tests
are EXPECTED TO FAIL — failure confirms the bug exists.  The tests
encode the EXPECTED (correct) behavior and will pass once the fix is
implemented.

Testing methodology: property-based exploration using Hypothesis to
generate random ``agent_id`` strings, confirming the bug is not specific
to ``"default"``.

Key property being verified:
  For any two concurrent new sessions (both IDs None, same agent_id),
  neither call should yield a SESSION_BUSY error event.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from hypothesis import given, strategies as st, settings as h_settings, HealthCheck

from claude_agent_sdk import SystemMessage, ResultMessage

from core.agent_manager import AgentManager

from tests.helpers_parallel_session import (
    build_mock_agent_manager,
    build_mock_options,
    build_mock_client_wrapper,
    collect_events_new_session,
)


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = h_settings(
    max_examples=3,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate arbitrary non-empty agent_id strings
agent_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_parallel_sessions(agent_id: str) -> tuple[list[dict], list[dict]]:
    """Launch two concurrent _execute_on_session calls and collect events.

    Both calls use session_id=None, app_session_id=None (new sessions)
    with the same agent_id.  Returns (events_a, events_b).
    """
    agent_manager = build_mock_agent_manager()

    session_id_a = str(uuid4())
    session_id_b = str(uuid4())

    mock_options = build_mock_options()
    wrapper_factory = build_mock_client_wrapper(session_id_a, session_id_b)

    with patch("core.agent_manager._configure_claude_environment", new_callable=MagicMock):
        with patch.object(
            agent_manager, "_build_options",
            new_callable=AsyncMock, return_value=mock_options,
        ):
            with patch(
                "core.agent_manager._ClaudeClientWrapper",
                side_effect=wrapper_factory,
            ):
                with patch(
                    "core.agent_manager.session_manager"
                ) as mock_sm:
                    mock_sm.store_session = AsyncMock()

                    events_a, events_b = await asyncio.gather(
                        collect_events_new_session(agent_manager, agent_id),
                        collect_events_new_session(agent_manager, agent_id),
                    )

    return events_a, events_b


# ---------------------------------------------------------------------------
# Property Tests — Fault Condition Exploration
# ---------------------------------------------------------------------------


class TestParallelNewSessionsFaultCondition:
    """Property 1: Fault Condition — Parallel New Sessions Blocked by Shared Agent Lock.

    For any two concurrent ``_execute_on_session()`` calls where both
    ``app_session_id`` and ``session_id`` are ``None`` and ``agent_id``
    is the same, the function SHALL assign each call a unique lock key
    (UUID) so that neither call blocks the other with ``SESSION_BUSY``.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**
    """

    @pytest.mark.asyncio
    async def test_parallel_new_sessions_default_agent(self):
        """Concrete case: two new sessions with agent_id='default'.

        On UNFIXED code, the second session receives SESSION_BUSY because
        both compute lock_key = "default" (the agent_id fallback).

        **Validates: Requirements 1.1, 2.1**
        """
        events_a, events_b = await _run_parallel_sessions("default")

        busy_a = [e for e in events_a if e.get("code") == "SESSION_BUSY"]
        busy_b = [e for e in events_b if e.get("code") == "SESSION_BUSY"]

        assert len(busy_a) == 0, (
            f"Session A received SESSION_BUSY — new sessions should not "
            f"block each other. Events: {events_a}"
        )
        assert len(busy_b) == 0, (
            f"Session B received SESSION_BUSY — new sessions should not "
            f"block each other. Events: {events_b}"
        )

    @given(agent_id=agent_id_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_parallel_new_sessions_any_agent(self, agent_id: str):
        """Property: for ALL agent_id values, two concurrent new sessions
        (both session_id=None, app_session_id=None) SHALL NOT block each
        other with SESSION_BUSY.

        Uses Hypothesis to generate random agent_id strings, confirming
        the bug is not specific to "default".

        **Validates: Requirements 1.1, 1.2, 2.1, 2.2**
        """
        events_a, events_b = await _run_parallel_sessions(agent_id)

        busy_a = [e for e in events_a if e.get("code") == "SESSION_BUSY"]
        busy_b = [e for e in events_b if e.get("code") == "SESSION_BUSY"]

        assert len(busy_a) == 0, (
            f"Session A received SESSION_BUSY for agent_id={agent_id!r} — "
            f"new sessions should not block each other. Events: {events_a}"
        )
        assert len(busy_b) == 0, (
            f"Session B received SESSION_BUSY for agent_id={agent_id!r} — "
            f"new sessions should not block each other. Events: {events_b}"
        )
