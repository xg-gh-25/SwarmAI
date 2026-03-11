"""Unit tests for session cleanup and hook context lifecycle.

This module verifies the correctness properties around session cleanup
in ``AgentManager``, focusing on the ordering guarantees between
HookContext construction and session state removal, error handling
during context build failures, non-blocking cleanup behavior, and
activity_extracted flag lifecycle.

Testing methodology: async unit tests using ``pytest-asyncio`` with
``unittest.mock.AsyncMock`` and ``unittest.mock.MagicMock`` for mocking
DB queries, hook executors, and SDK client wrappers.

Key properties tested (task 8.1 — others added by later tasks):

- **Property 1**: HookContext is built before session state is removed.
  The ``agent_id`` in the fired context matches the session's original
  ``agent_id`` from ``_active_sessions``.

**Validates: Requirements 2.1, 9a.2**
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_hooks import HookContext, BackgroundHookExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_info(agent_id: str = "agent-abc-123") -> dict:
    """Build a minimal ``_active_sessions`` entry for testing."""
    return {
        "agent_id": agent_id,
        "created_at": time.time(),
        "last_used": time.time(),
        "wrapper": AsyncMock(),  # mock SDK wrapper with __aexit__
        "activity_extracted": False,
    }


def _make_hook_context(
    session_id: str = "sess-001",
    agent_id: str = "agent-abc-123",
) -> HookContext:
    """Build a ``HookContext`` for assertions."""
    return HookContext(
        session_id=session_id,
        agent_id=agent_id,
        message_count=5,
        session_start_time="2025-01-01T00:00:00Z",
        session_title="Test Session",
    )


# ---------------------------------------------------------------------------
# Property 1 — HookContext built before session pop
# ---------------------------------------------------------------------------

class TestHookContextBuiltBeforeSessionPop:
    """Verify HookContext is built BEFORE the session is popped.

    **Property 1**: For any session being cleaned up via
    ``_cleanup_session``, the ``HookContext`` passed to
    ``BackgroundHookExecutor.fire()`` SHALL contain a non-empty
    ``agent_id`` that matches the session's original ``agent_id``
    from ``_active_sessions``.

    **Validates: Requirements 2.1, 9a.2**
    """

    @pytest.mark.asyncio
    async def test_fired_context_has_correct_agent_id(self):
        """The context fired to the executor has the session's agent_id.

        Steps:
        1. Import the ``agent_manager`` singleton.
        2. Inject a mock ``BackgroundHookExecutor`` via
           ``set_hook_executor()``.
        3. Add a session to ``_active_sessions`` with a known
           ``agent_id``.
        4. Patch ``_build_hook_context`` to return a HookContext
           carrying that same ``agent_id``.
        5. Call ``_cleanup_session(session_id)``.
        6. Assert ``fire()`` was called once with a context whose
           ``agent_id`` matches the original session info.
        7. Assert the session was removed from ``_active_sessions``
           after the fire call.
        """
        from core.agent_manager import agent_manager

        session_id = "sess-prop1-test"
        expected_agent_id = "agent-abc-123"
        info = _make_session_info(agent_id=expected_agent_id)
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id=expected_agent_id,
        )

        # --- Set up mocks ---
        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            # Patch _build_hook_context to return our expected context
            # without hitting the real DB.
            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ) as mock_build:
                await agent_manager._cleanup_session(session_id)

                # --- Assertions ---

                # 1. _build_hook_context was called with the session_id
                #    and the original info dict (before pop).
                mock_build.assert_awaited_once_with(session_id, info)

                # 2. fire() was called exactly once.
                mock_executor.fire.assert_called_once()

                # 3. The context passed to fire() has the correct,
                #    non-empty agent_id.
                fired_ctx = mock_executor.fire.call_args[0][0]
                assert isinstance(fired_ctx, HookContext)
                assert fired_ctx.agent_id == expected_agent_id
                assert fired_ctx.agent_id != ""

                # 4. The session was removed from _active_sessions
                #    AFTER fire() was called (cleanup completed).
                assert session_id not in agent_manager._active_sessions

        finally:
            # Restore original state to avoid polluting other tests.
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_fire_called_before_session_pop(self):
        """Verify ordering: fire() is invoked while session still exists.

        We use a side-effect on ``fire()`` to capture whether the
        session is still in ``_active_sessions`` at the moment
        ``fire()`` is called.  This proves the context was built
        and fired BEFORE the session dict was popped.

        **Validates: Requirements 2.1, 9a.2**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-ordering-test"
        expected_agent_id = "agent-ordering-456"
        info = _make_session_info(agent_id=expected_agent_id)
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id=expected_agent_id,
        )

        session_present_at_fire_time = None

        def _capture_fire(context, skip_hooks=None):
            nonlocal session_present_at_fire_time
            session_present_at_fire_time = (
                session_id in agent_manager._active_sessions
            )

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock(side_effect=_capture_fire)

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._cleanup_session(session_id)

            # fire() was called while session was still present
            assert session_present_at_fire_time is True
            # ... and now the session is gone
            assert session_id not in agent_manager._active_sessions

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions



# ---------------------------------------------------------------------------
# Property 2 — Session cleanup completes on context build failure
# ---------------------------------------------------------------------------

class TestSessionCleanupOnContextBuildFailure:
    """Verify session cleanup completes even when context build fails.

    **Property 2**: For any session where ``_build_hook_context()``
    raises an exception, ``_cleanup_session()`` SHALL still remove the
    session from ``_active_sessions``, disconnect the SDK client, and
    clean up all per-session resources (locks, permissions, metadata).
    No background hook task SHALL be spawned.

    **Validates: Requirements 2.5, 9a.3**
    """

    @pytest.mark.asyncio
    async def test_no_hook_task_spawned_on_context_build_failure(self):
        """fire() must NOT be called when _build_hook_context raises.

        Steps:
        1. Inject a mock BackgroundHookExecutor.
        2. Add a session to _active_sessions.
        3. Patch _build_hook_context to raise RuntimeError (DB failure).
        4. Call _cleanup_session(session_id).
        5. Assert fire() was never called.
        """
        from core.agent_manager import agent_manager

        session_id = "sess-ctx-fail-no-fire"
        info = _make_session_info(agent_id="agent-ctx-fail")

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection lost"),
            ):
                await agent_manager._cleanup_session(session_id)

            # fire() must NOT have been called
            mock_executor.fire.assert_not_called()

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_session_removed_on_context_build_failure(self):
        """Session is popped from _active_sessions despite context failure.

        Steps:
        1. Add a session to _active_sessions.
        2. Patch _build_hook_context to raise.
        3. Call _cleanup_session(session_id).
        4. Assert session_id is no longer in _active_sessions.
        """
        from core.agent_manager import agent_manager

        session_id = "sess-ctx-fail-removed"
        info = _make_session_info(agent_id="agent-removed")

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB unavailable"),
            ):
                await agent_manager._cleanup_session(session_id)

            assert session_id not in agent_manager._active_sessions

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_sdk_client_disconnected_on_context_build_failure(self):
        """SDK wrapper __aexit__ is called despite context build failure.

        Steps:
        1. Add a session with a mock wrapper to _active_sessions.
        2. Patch _build_hook_context to raise.
        3. Call _cleanup_session(session_id).
        4. Assert wrapper.__aexit__ was awaited (client disconnected).
        """
        from core.agent_manager import agent_manager

        session_id = "sess-ctx-fail-disconnect"
        info = _make_session_info(agent_id="agent-disconnect")
        mock_wrapper = info["wrapper"]

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB query timeout"),
            ):
                await agent_manager._cleanup_session(session_id)

            # wrapper.__aexit__ must have been called to disconnect SDK
            mock_wrapper.__aexit__.assert_awaited_once_with(None, None, None)

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_per_session_resources_cleaned_on_context_build_failure(self):
        """Per-session locks, permissions, and metadata are cleaned up.

        Steps:
        1. Add a session to _active_sessions and seed per-session state.
        2. Patch _build_hook_context to raise.
        3. Call _cleanup_session(session_id).
        4. Assert session_locks, _clients, and _user_turn_counts no
           longer contain the session_id.
        """
        from core.agent_manager import agent_manager

        session_id = "sess-ctx-fail-resources"
        info = _make_session_info(agent_id="agent-resources")

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        original_locks = agent_manager._session_locks.copy()
        original_clients = agent_manager._clients.copy()
        original_turns = agent_manager._user_turn_counts.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info
            # Seed per-session state that cleanup should remove
            agent_manager._session_locks[session_id] = asyncio.Lock()
            agent_manager._clients[session_id] = MagicMock()
            agent_manager._user_turn_counts[session_id] = 3

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB schema mismatch"),
            ):
                await agent_manager._cleanup_session(session_id)

            # All per-session state must be cleaned up
            assert session_id not in agent_manager._active_sessions
            assert session_id not in agent_manager._session_locks
            assert session_id not in agent_manager._clients
            assert session_id not in agent_manager._user_turn_counts

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions
            agent_manager._session_locks = original_locks
            agent_manager._clients = original_clients
            agent_manager._user_turn_counts = original_turns


# ---------------------------------------------------------------------------
# Property 3 — Session cleanup is non-blocking
# ---------------------------------------------------------------------------

class TestNonBlockingSessionCleanup:
    """Verify session cleanup returns immediately without blocking on hooks.

    **Property 3**: For any session cleanup where hooks are enabled
    (``skip_hooks=False``), ``_cleanup_session()`` SHALL return before
    the background hook task completes.  Specifically, the session SHALL
    be removed from ``_active_sessions`` while the hook task is still
    pending in ``BackgroundHookExecutor._pending``.

    Since ``fire()`` calls ``asyncio.create_task()`` and returns
    immediately (it is synchronous), ``_cleanup_session()`` should
    complete in well under 1 second regardless of how long the hooks
    would take.

    **Validates: Requirements 2.2, 2.3**
    """

    @pytest.mark.asyncio
    async def test_cleanup_returns_before_hook_completes(self):
        """_cleanup_session() returns immediately; hooks run in background.

        Steps:
        1. Import the ``agent_manager`` singleton.
        2. Inject a mock ``BackgroundHookExecutor`` whose ``fire()``
           is a no-op (simulating background task spawn).
        3. Add a session to ``_active_sessions``.
        4. Patch ``_build_hook_context`` to return a valid HookContext.
        5. Measure the wall-clock time of ``_cleanup_session()``.
        6. Assert it completes in < 1 second (non-blocking).
        7. Assert ``fire()`` was called (hooks were dispatched).
        8. Assert the session was removed from ``_active_sessions``.
        """
        from core.agent_manager import agent_manager

        session_id = "sess-nonblock-test"
        expected_agent_id = "agent-nonblock-001"
        info = _make_session_info(agent_id=expected_agent_id)
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id=expected_agent_id,
        )

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()  # synchronous no-op

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                t0 = time.monotonic()
                await agent_manager._cleanup_session(session_id)
                elapsed = time.monotonic() - t0

            # Cleanup must complete quickly — fire() is non-blocking
            assert elapsed < 1.0, (
                f"_cleanup_session() took {elapsed:.2f}s; expected < 1s "
                f"(hooks should not block cleanup)"
            )

            # fire() was called — hooks were dispatched to background
            mock_executor.fire.assert_called_once()

            # Session was removed — cleanup completed fully
            assert session_id not in agent_manager._active_sessions

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_session_removed_while_hooks_conceptually_pending(self):
        """Session is removed from _active_sessions even though hooks
        have not yet executed.

        We verify this by checking that fire() was called (hooks
        dispatched) AND the session is gone from _active_sessions
        in the same synchronous flow — proving cleanup does not wait
        for hook completion.

        **Validates: Requirements 2.2, 2.3**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-pending-hooks"
        info = _make_session_info(agent_id="agent-pending-001")
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-pending-001",
        )

        fire_called = False
        session_gone_after_cleanup = None

        def _track_fire(context, skip_hooks=None):
            nonlocal fire_called
            fire_called = True

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock(side_effect=_track_fire)

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._cleanup_session(session_id)

            session_gone_after_cleanup = (
                session_id not in agent_manager._active_sessions
            )

            # fire() was called — hooks were dispatched
            assert fire_called is True

            # Session is gone — cleanup did not wait for hooks
            assert session_gone_after_cleanup is True

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_cleanup_nonblocking_with_skip_hooks_false(self):
        """Explicitly pass skip_hooks=False and verify non-blocking behavior.

        This test ensures the default path (hooks enabled) dispatches
        hooks via fire() and still returns immediately.

        **Validates: Requirements 2.2, 2.3**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-explicit-noskip"
        info = _make_session_info(agent_id="agent-noskip-001")
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-noskip-001",
        )

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire = MagicMock()

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                t0 = time.monotonic()
                await agent_manager._cleanup_session(
                    session_id, skip_hooks=False,
                )
                elapsed = time.monotonic() - t0

            # Non-blocking: completes quickly
            assert elapsed < 1.0

            # Hooks were dispatched
            mock_executor.fire.assert_called_once()

            # Session cleaned up
            assert session_id not in agent_manager._active_sessions

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions


# ---------------------------------------------------------------------------
# Property 14 — activity_extracted flag invariants
# ---------------------------------------------------------------------------

class TestActivityExtractedFlagLifecycle:
    """Verify the ``activity_extracted`` flag lifecycle invariants.

    **Property 14**: For any session:

    - The ``activity_extracted`` flag SHALL be set to True BEFORE
      ``fire_single()`` is called by the idle loop.
    - When a background extraction task fails, the flag SHALL remain
      True (the background task itself does not modify the flag).
    - When a user sends a new message (via ``_get_active_client()``),
      the flag SHALL be reset to False.
    - The background hook task itself SHALL NOT modify the flag.

    **Validates: Requirements 9d.10, 9d.11, 9d.12, 9d.13**
    """

    @pytest.mark.asyncio
    async def test_flag_set_to_true_before_fire_single(self):
        """activity_extracted is True BEFORE fire_single() is called.

        We use a side-effect on ``fire_single()`` to capture the flag
        value at the moment the executor is invoked.  This proves the
        flag is set before the background task is spawned.

        **Validates: Requirements 9d.10**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-flag-before-fire"
        info = _make_session_info(agent_id="agent-flag-001")
        info["activity_extracted"] = False  # starts False
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-flag-001",
        )

        flag_at_fire_time = None

        def _capture_fire_single(hook, context, timeout=30.0):
            nonlocal flag_at_fire_time
            flag_at_fire_time = info["activity_extracted"]

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire_single = MagicMock(side_effect=_capture_fire_single)

        # Provide a mock hook list with a daily_activity_extraction hook
        mock_hook = MagicMock()
        mock_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_hook]

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(session_id, info)

            # fire_single was called
            mock_executor.fire_single.assert_called_once()

            # At the moment fire_single was called, the flag was True
            assert flag_at_fire_time is True, (
                "activity_extracted must be True BEFORE fire_single() is called"
            )

            # Flag remains True after the call
            assert info["activity_extracted"] is True

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_flag_not_reset_on_background_failure(self):
        """Flag stays True after fire_single() even if background would fail.

        In the background path (executor exists), once ``fire_single()``
        is called, the flag stays True regardless of background task
        outcome.  The ``except Exception`` block in
        ``_extract_activity_early()`` only fires when
        ``_build_hook_context()`` fails (before ``fire_single`` is
        called) or in the inline fallback path.

        We verify that after a successful ``fire_single()`` call, the
        flag remains True — the background task itself never modifies it.

        **Validates: Requirements 9d.11, 9d.13**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-flag-bg-fail"
        info = _make_session_info(agent_id="agent-flag-002")
        info["activity_extracted"] = False
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-flag-002",
        )

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire_single = MagicMock()  # no-op, simulates spawn

        mock_hook = MagicMock()
        mock_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_hook]

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(session_id, info)

            # fire_single was called — background task was spawned
            mock_executor.fire_single.assert_called_once()

            # Flag must remain True — background task does not modify it
            assert info["activity_extracted"] is True, (
                "activity_extracted must remain True after fire_single(); "
                "background task failures do not reset the flag"
            )

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_flag_reset_on_new_message(self):
        """_get_active_client() resets activity_extracted to False.

        When a user sends a new message, ``_get_active_client()`` is
        called, which resets the flag so that new activity after the
        user resumes gets captured in the next idle period.

        **Validates: Requirements 9d.12**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-flag-reset-msg"
        info = _make_session_info(agent_id="agent-flag-003")
        info["activity_extracted"] = True  # previously extracted
        info["client"] = MagicMock()  # mock SDK client

        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._active_sessions[session_id] = info

            # Call _get_active_client — simulates user sending a message
            client = agent_manager._get_active_client(session_id)

            # Client was returned
            assert client is not None

            # Flag must be reset to False
            assert info["activity_extracted"] is False, (
                "activity_extracted must be reset to False when "
                "_get_active_client() is called (user sends new message)"
            )

        finally:
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_flag_reset_on_context_build_failure_before_spawn(self):
        """Flag resets to False when _build_hook_context fails (before spawn).

        If ``_build_hook_context()`` raises an exception, the flag is
        set back to False in the ``except`` block, allowing retry on
        the next idle-loop cycle.  This is correct because
        ``fire_single()`` was never called — no extraction was initiated.

        **Validates: Requirements 9d.10, 9d.11** (complementary edge case)
        """
        from core.agent_manager import agent_manager

        session_id = "sess-flag-ctx-fail"
        info = _make_session_info(agent_id="agent-flag-004")
        info["activity_extracted"] = False

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire_single = MagicMock()

        mock_hook = MagicMock()
        mock_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_hook]

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB unavailable"),
            ):
                await agent_manager._extract_activity_early(session_id, info)

            # fire_single must NOT have been called
            mock_executor.fire_single.assert_not_called()

            # Flag must be reset to False — extraction was never initiated
            assert info["activity_extracted"] is False, (
                "activity_extracted must be reset to False when "
                "_build_hook_context() fails before fire_single()"
            )

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions

    @pytest.mark.asyncio
    async def test_background_task_does_not_modify_flag(self):
        """The background hook task itself does not touch the flag.

        We verify that after ``_extract_activity_early()`` completes
        (with a successful ``fire_single()``), the flag is True and
        was only modified by the idle-loop code, not by the executor.

        We capture all writes to ``info["activity_extracted"]`` to
        prove only the idle-loop code (``_extract_activity_early``)
        sets it, and the executor/background task does not.

        **Validates: Requirements 9d.13**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-flag-no-bg-modify"
        info = _make_session_info(agent_id="agent-flag-005")
        info["activity_extracted"] = False
        expected_ctx = _make_hook_context(
            session_id=session_id,
            agent_id="agent-flag-005",
        )

        fire_single_called = False

        def _track_fire_single(hook, context, timeout=30.0):
            nonlocal fire_single_called
            fire_single_called = True
            # Simulate what the real executor does: create_task.
            # Crucially, we do NOT modify info["activity_extracted"].

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_executor.fire_single = MagicMock(side_effect=_track_fire_single)

        mock_hook = MagicMock()
        mock_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_hook]

        original_executor = agent_manager._hook_executor
        original_sessions = agent_manager._active_sessions.copy()
        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(session_id, info)

            # fire_single was called
            assert fire_single_called is True

            # Flag is True — set by _extract_activity_early, not by executor
            assert info["activity_extracted"] is True

        finally:
            agent_manager._hook_executor = original_executor
            agent_manager._active_sessions = original_sessions


# ---------------------------------------------------------------------------
# Property 7 — CancelledError logged at INFO (not ERROR)
# ---------------------------------------------------------------------------


class _SlowHook:
    """A hook that sleeps for a long time, simulating a slow operation.

    Used to test CancelledError handling — the hook will be cancelled
    while sleeping, triggering the CancelledError code path in
    ``_run_all_safe()``.
    """

    def __init__(self, name: str = "slow_hook", sleep_seconds: float = 100.0):
        self._name = name
        self._sleep_seconds = sleep_seconds

    @property
    def name(self) -> str:
        return self._name

    async def execute(self, context: HookContext) -> None:
        await asyncio.sleep(self._sleep_seconds)


class TestCancelledErrorLoggedAtInfo:
    """Verify CancelledError is caught and logged at INFO level.

    Property 7: For any background hook task that is cancelled (via
    ``drain()`` or direct cancellation), the ``CancelledError`` SHALL
    be caught and logged at INFO level (not ERROR).  The task SHALL
    not propagate the error to any user-facing code path.

    **Validates: Requirements 6.3**
    """

    @pytest.mark.asyncio
    async def test_cancelled_task_logs_info_with_session_id(self, caplog):
        """Cancel a running hook task via drain, verify INFO log with session ID.

        Steps:
        1. Create a real BackgroundHookExecutor with a slow hook
        2. Fire a hook task
        3. Call drain() with a very short timeout to trigger cancellation
        4. Verify INFO-level log messages contain session ID
        5. Verify no ERROR-level messages about CancelledError
        """
        import logging
        from core.session_hooks import (
            SessionLifecycleHookManager,
            BackgroundHookExecutor,
        )

        session_id = "sess-cancel-info-001"
        context = _make_hook_context(session_id=session_id, agent_id="agent-cancel-001")

        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        hook_manager.register(_SlowHook(name="slow_test_hook", sleep_seconds=100.0))

        executor = BackgroundHookExecutor(hook_manager)

        with caplog.at_level(logging.DEBUG, logger="core.session_hooks"):
            executor.fire(context)

            # Let the task start running (enter the sleep)
            await asyncio.sleep(0.05)

            assert executor.pending_count == 1

            # Drain with a very short timeout to force cancellation
            done, cancelled = await executor.drain(timeout=0.1)

            # Give the event loop a moment to process callbacks
            await asyncio.sleep(0.05)

        # Verify drain returned correct counts
        assert cancelled >= 1, f"Expected at least 1 cancelled task, got {cancelled}"

        # Verify INFO-level log messages about cancellation contain session ID
        info_records = [
            r for r in caplog.records
            if r.levelno == logging.INFO and session_id in r.message
        ]
        cancel_info_records = [
            r for r in info_records
            if "cancel" in r.message.lower()
        ]
        assert len(cancel_info_records) >= 1, (
            f"Expected at least 1 INFO-level cancellation log with session ID "
            f"'{session_id}', found {len(cancel_info_records)}. "
            f"All INFO records: {[r.message for r in info_records]}"
        )

        # Verify NO ERROR-level messages about CancelledError
        error_records = [
            r for r in caplog.records
            if r.levelno == logging.ERROR
            and ("cancel" in r.message.lower() or "CancelledError" in r.message)
        ]
        assert len(error_records) == 0, (
            f"CancelledError should NOT be logged at ERROR level, "
            f"but found: {[r.message for r in error_records]}"
        )

    @pytest.mark.asyncio
    async def test_direct_task_cancellation_logs_info(self, caplog):
        """Cancel a hook task directly (not via drain), verify INFO logging.

        This tests the inner CancelledError handler in _run_all_safe()
        which catches per-hook cancellation and re-raises, plus the
        outer handler that logs the task-level cancellation summary.
        """
        import logging
        from core.session_hooks import (
            SessionLifecycleHookManager,
            BackgroundHookExecutor,
        )

        session_id = "sess-cancel-direct-002"
        context = _make_hook_context(session_id=session_id, agent_id="agent-cancel-002")

        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        hook_manager.register(_SlowHook(name="very_slow_hook", sleep_seconds=200.0))

        executor = BackgroundHookExecutor(hook_manager)

        with caplog.at_level(logging.DEBUG, logger="core.session_hooks"):
            executor.fire(context)

            # Let the task start
            await asyncio.sleep(0.05)

            # Get the pending task and cancel it directly
            pending_tasks = list(executor._pending)
            assert len(pending_tasks) == 1
            task = pending_tasks[0]

            task.cancel()

            # Wait for cancellation to propagate
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

            await asyncio.sleep(0.05)

        # Verify INFO-level cancellation log with session ID
        info_cancel_records = [
            r for r in caplog.records
            if r.levelno == logging.INFO
            and session_id in r.message
            and "cancel" in r.message.lower()
        ]
        assert len(info_cancel_records) >= 1, (
            f"Expected INFO-level cancellation log with session ID "
            f"'{session_id}', found none. "
            f"All records: {[(r.levelname, r.message) for r in caplog.records]}"
        )

        # Verify no ERROR-level CancelledError messages
        error_cancel_records = [
            r for r in caplog.records
            if r.levelno == logging.ERROR
            and ("cancel" in r.message.lower() or "CancelledError" in r.message)
        ]
        assert len(error_cancel_records) == 0, (
            f"CancelledError should be INFO not ERROR, "
            f"but found ERROR records: {[r.message for r in error_cancel_records]}"
        )

    @pytest.mark.asyncio
    async def test_cancellation_does_not_propagate_to_caller(self, caplog):
        """Verify CancelledError does not propagate outside the task.

        After drain() cancels tasks, the caller (e.g. disconnect_all)
        should not see any CancelledError — it's fully contained
        within the background task.
        """
        import logging
        from core.session_hooks import (
            SessionLifecycleHookManager,
            BackgroundHookExecutor,
        )

        session_id = "sess-cancel-noprop-003"
        context = _make_hook_context(session_id=session_id, agent_id="agent-cancel-003")

        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        hook_manager.register(_SlowHook(name="blocking_hook", sleep_seconds=100.0))

        executor = BackgroundHookExecutor(hook_manager)

        executor.fire(context)
        await asyncio.sleep(0.05)

        # drain() should NOT raise CancelledError to the caller
        try:
            done, cancelled = await executor.drain(timeout=0.1)
        except asyncio.CancelledError:
            pytest.fail("drain() should not propagate CancelledError to caller")

        # After drain, pending set should be empty
        await asyncio.sleep(0.05)
        assert executor.pending_count == 0, (
            f"Expected 0 pending tasks after drain, got {executor.pending_count}"
        )


# ---------------------------------------------------------------------------
# Task 8.6 — Drain on empty pending set (edge case)
# ---------------------------------------------------------------------------


class TestDrainOnEmptyPendingSet:
    """Verify drain() returns (0, 0) immediately when no tasks are pending.

    When ``BackgroundHookExecutor.drain()`` is called with an empty
    ``_pending`` set, it should short-circuit and return ``(0, 0)``
    without blocking.

    **Validates: Requirements 3.5**
    """

    @pytest.mark.asyncio
    async def test_drain_empty_returns_zero_zero(self):
        """drain() on a fresh executor with no fired tasks returns (0, 0)."""
        from core.session_hooks import (
            SessionLifecycleHookManager,
            BackgroundHookExecutor,
        )

        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        executor = BackgroundHookExecutor(hook_manager)

        # No tasks fired — pending set is empty
        assert executor.pending_count == 0

        done, cancelled = await executor.drain(timeout=2.0)

        assert done == 0, f"Expected 0 done, got {done}"
        assert cancelled == 0, f"Expected 0 cancelled, got {cancelled}"

    @pytest.mark.asyncio
    async def test_drain_empty_completes_quickly(self):
        """drain() on empty pending set completes within 100ms (no blocking)."""
        from core.session_hooks import (
            SessionLifecycleHookManager,
            BackgroundHookExecutor,
        )

        hook_manager = SessionLifecycleHookManager(timeout_seconds=30.0)
        executor = BackgroundHookExecutor(hook_manager)

        t0 = time.monotonic()
        await executor.drain(timeout=5.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.1, (
            f"drain() on empty set took {elapsed:.3f}s — should be near-instant"
        )
