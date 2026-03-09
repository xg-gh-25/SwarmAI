"""Preservation property tests for parallel chat session blocking bugfix.

**Bugfix: parallel-chat-session-blocking, Property 2: Preservation**

Tests that verify the existing double-send protection and lock cleanup
behavior on UNFIXED code, establishing a baseline that must be preserved
after the fix is applied.

Testing methodology: property-based preservation using Hypothesis to
generate random session ID strings, confirming that double-send protection
and lock cleanup work for all valid session identifiers.

Key properties being verified:

- ``Property 2a`` — For all non-None ``session_id`` values, two concurrent
  ``_execute_on_session()`` calls with the same ``session_id`` result in
  exactly one ``SESSION_BUSY`` rejection.
- ``Property 2b`` — For all non-None ``app_session_id`` values, two concurrent
  ``_execute_on_session()`` calls with the same ``app_session_id`` result in
  exactly one ``SESSION_BUSY`` rejection.
- ``Property 2c`` — For all session IDs, after ``_cleanup_session(sid)``
  completes, ``sid`` is NOT in ``self._session_locks`` and NOT in
  ``self._active_sessions``.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from hypothesis import given, strategies as st, settings as h_settings, HealthCheck

from core.agent_manager import AgentManager

from tests.helpers_parallel_session import (
    build_mock_agent_manager,
    build_mock_options,
    build_mock_client_wrapper,
    collect_events_resumed,
)


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = h_settings(
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate arbitrary non-empty session ID strings (non-None)
session_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=30,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_parallel_resumed_sessions_by_session_id(
    session_id: str,
) -> tuple[list[dict], list[dict]]:
    """Launch two concurrent _execute_on_session calls with the same session_id."""
    agent_manager = build_mock_agent_manager()
    mock_options = build_mock_options()
    sdk_sid = str(uuid4())
    wrapper_factory = build_mock_client_wrapper(sdk_sid, sdk_sid)

    with patch("core.agent_manager._configure_claude_environment", new_callable=MagicMock):
        with patch.object(
            agent_manager, "_build_options",
            new_callable=AsyncMock, return_value=mock_options,
        ):
            with patch(
                "core.agent_manager._ClaudeClientWrapper",
                side_effect=wrapper_factory,
            ):
                with patch("core.agent_manager.session_manager") as mock_sm:
                    mock_sm.store_session = AsyncMock()
                    events_a, events_b = await asyncio.gather(
                        collect_events_resumed(
                            agent_manager, "default", session_id=session_id,
                        ),
                        collect_events_resumed(
                            agent_manager, "default", session_id=session_id,
                        ),
                    )
    return events_a, events_b


async def _run_parallel_resumed_sessions_by_app_session_id(
    app_session_id: str,
) -> tuple[list[dict], list[dict]]:
    """Launch two concurrent _execute_on_session calls with the same app_session_id."""
    agent_manager = build_mock_agent_manager()
    mock_options = build_mock_options()
    sdk_sid = str(uuid4())
    wrapper_factory = build_mock_client_wrapper(sdk_sid, sdk_sid)

    with patch("core.agent_manager._configure_claude_environment", new_callable=MagicMock):
        with patch.object(
            agent_manager, "_build_options",
            new_callable=AsyncMock, return_value=mock_options,
        ):
            with patch(
                "core.agent_manager._ClaudeClientWrapper",
                side_effect=wrapper_factory,
            ):
                with patch("core.agent_manager.session_manager") as mock_sm:
                    mock_sm.store_session = AsyncMock()
                    events_a, events_b = await asyncio.gather(
                        collect_events_resumed(
                            agent_manager, "default",
                            session_id=str(uuid4()),
                            app_session_id=app_session_id,
                        ),
                        collect_events_resumed(
                            agent_manager, "default",
                            session_id=str(uuid4()),
                            app_session_id=app_session_id,
                        ),
                    )
    return events_a, events_b


# ---------------------------------------------------------------------------
# Property Tests — Preservation
# ---------------------------------------------------------------------------


class TestDoubleSendProtectionBySessionId:
    """Property 2a: Double-Send Protection for resumed sessions (session_id).

    For all ``session_id`` values that are not ``None``, two concurrent
    ``_execute_on_session()`` calls with the same ``session_id`` SHALL
    result in exactly one call yielding ``SESSION_BUSY``.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(session_id=session_id_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_double_send_by_session_id(self, session_id: str):
        """Property 2a: for ALL non-None session_id values, two concurrent
        resumed-session calls with the same session_id SHALL result in
        exactly one SESSION_BUSY rejection.

        **Validates: Requirements 3.1, 3.2**
        """
        events_a, events_b = await _run_parallel_resumed_sessions_by_session_id(
            session_id,
        )

        busy_a = [e for e in events_a if e.get("code") == "SESSION_BUSY"]
        busy_b = [e for e in events_b if e.get("code") == "SESSION_BUSY"]

        total_busy = len(busy_a) + len(busy_b)
        assert total_busy == 1, (
            f"Expected exactly 1 SESSION_BUSY for session_id={session_id!r}, "
            f"got {total_busy}. "
            f"busy_a={busy_a}, busy_b={busy_b}"
        )


class TestDoubleSendProtectionByAppSessionId:
    """Property 2b: Double-Send Protection for resumed sessions (app_session_id).

    For all ``app_session_id`` values that are not ``None``, two concurrent
    ``_execute_on_session()`` calls with the same ``app_session_id`` SHALL
    result in exactly one call yielding ``SESSION_BUSY``.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(app_session_id=session_id_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_double_send_by_app_session_id(self, app_session_id: str):
        """Property 2b: for ALL non-None app_session_id values, two concurrent
        resumed-session calls with the same app_session_id SHALL result in
        exactly one SESSION_BUSY rejection.

        **Validates: Requirements 3.1, 3.2**
        """
        events_a, events_b = await _run_parallel_resumed_sessions_by_app_session_id(
            app_session_id,
        )

        busy_a = [e for e in events_a if e.get("code") == "SESSION_BUSY"]
        busy_b = [e for e in events_b if e.get("code") == "SESSION_BUSY"]

        total_busy = len(busy_a) + len(busy_b)
        assert total_busy == 1, (
            f"Expected exactly 1 SESSION_BUSY for app_session_id={app_session_id!r}, "
            f"got {total_busy}. "
            f"busy_a={busy_a}, busy_b={busy_b}"
        )


class TestLockCleanup:
    """Property 2c: Lock Cleanup after _cleanup_session().

    For all session IDs, after ``_cleanup_session(sid)`` completes,
    ``sid`` is NOT in ``self._session_locks`` and NOT in
    ``self._active_sessions``.

    **Validates: Requirements 3.3, 3.4**
    """

    @given(session_id=session_id_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_cleanup_removes_lock_and_session(self, session_id: str):
        """Property 2c: for ALL session IDs, after _cleanup_session(sid)
        completes, sid is NOT in self._session_locks and NOT in
        self._active_sessions.

        **Validates: Requirements 3.3, 3.4**
        """
        agent_manager = build_mock_agent_manager()

        # Manually populate _session_locks and _active_sessions
        agent_manager._session_locks[session_id] = asyncio.Lock()
        agent_manager._active_sessions[session_id] = {
            "client": MagicMock(),
            "wrapper": MagicMock(
                __aexit__=AsyncMock(return_value=False),
            ),
            "created_at": 0,
            "last_used": 0,
        }

        # Verify they exist before cleanup
        assert session_id in agent_manager._session_locks
        assert session_id in agent_manager._active_sessions

        # Run cleanup
        await agent_manager._cleanup_session(session_id, skip_hooks=True)

        # Verify both are removed
        assert session_id not in agent_manager._session_locks, (
            f"session_id={session_id!r} still in _session_locks after cleanup"
        )
        assert session_id not in agent_manager._active_sessions, (
            f"session_id={session_id!r} still in _active_sessions after cleanup"
        )
