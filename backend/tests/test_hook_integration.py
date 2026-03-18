"""Integration tests for end-to-end hook execution decoupling flows.

This module verifies the full shutdown flow through the ``POST /shutdown``
endpoint, ensuring that:

- The endpoint returns ``{"status": "shutting_down"}`` successfully
- All active sessions are cleaned up (``_active_sessions`` empty)
- The idle cleanup loop is cancelled
- ``drain()`` is called on the ``BackgroundHookExecutor``
- Background hook tasks are fired for each session before cleanup

Testing methodology: integration tests using FastAPI's ``TestClient``
with ``unittest.mock.AsyncMock`` and ``unittest.mock.MagicMock`` for
mocking hook executors, SDK client wrappers, and DB queries.  The tests
exercise the real ``POST /shutdown`` endpoint handler but mock internal
dependencies to avoid real infrastructure.

Key flows tested:

- ``TestFullShutdownFlow`` — Task 9.1: Full shutdown via POST /shutdown

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.session_hooks import (
    BackgroundHookExecutor,
    HookContext,
    SessionLifecycleHookManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_client():
    """Create a synchronous TestClient for endpoint testing."""
    from main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_info(agent_id: str = "agent-integ-001") -> dict:
    """Build a minimal ``_active_sessions`` entry for testing."""
    return {
        "agent_id": agent_id,
        "created_at": time.time(),
        "last_used": time.time(),
        "wrapper": AsyncMock(),  # mock SDK wrapper with __aexit__
        "activity_extracted": False,
    }


def _make_hook_context(
    session_id: str = "sess-integ-001",
    agent_id: str = "agent-integ-001",
) -> HookContext:
    """Build a ``HookContext`` for assertions."""
    return HookContext(
        session_id=session_id,
        agent_id=agent_id,
        message_count=5,
        session_start_time="2025-01-01T00:00:00Z",
        session_title="Integration Test Session",
    )


def _setup_agent_manager_with_sessions(session_ids: list[str]):
    """Inject mock sessions and executor into the agent_manager singleton.

    Returns a tuple of (mock_executor, session_infos, contexts, originals)
    where ``originals`` is a dict of original state to restore in teardown.
    """
    from core.agent_manager import agent_manager

    mock_executor = MagicMock(spec=BackgroundHookExecutor)
    mock_executor.fire = MagicMock()
    mock_executor.pending_count = 0
    mock_executor.drain = AsyncMock(return_value=(0, 0))

    originals = {
        "executor": agent_manager._hook_executor,
        "sessions": agent_manager._active_sessions.copy(),
        "cleanup_task": agent_manager._cleanup_task,
    }

    session_infos = {}
    contexts = {}
    for sid in session_ids:
        aid = f"agent-{sid}"
        info = _make_session_info(agent_id=aid)
        session_infos[sid] = info
        contexts[sid] = _make_hook_context(session_id=sid, agent_id=aid)
        agent_manager._active_sessions[sid] = info

    agent_manager._hook_executor = mock_executor
    agent_manager._cleanup_task = None

    return mock_executor, session_infos, contexts, originals


def _restore_agent_manager(originals: dict):
    """Restore agent_manager singleton to its original state."""
    from core.agent_manager import agent_manager

    agent_manager._hook_executor = originals["executor"]
    agent_manager._active_sessions = originals["sessions"]
    agent_manager._cleanup_task = originals["cleanup_task"]


# ---------------------------------------------------------------------------
# Task 9.1 — Full shutdown flow integration test
# ---------------------------------------------------------------------------


class TestFullShutdownFlow:
    """Integration test: POST /shutdown fires hooks, cleans sessions, drains.

    This test exercises the real ``POST /shutdown`` endpoint handler,
    verifying the complete shutdown flow end-to-end:

    1. Set up the AgentManager with active sessions and a mock executor
    2. Call ``POST /shutdown``
    3. Verify the response is ``{"status": "shutting_down"}``
    4. Verify all sessions are cleaned up (``_active_sessions`` empty)
    5. Verify the cleanup loop is cancelled
    6. Verify ``drain()`` was called on the hook executor

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    def test_shutdown_returns_shutting_down_status(self, test_client):
        """POST /shutdown returns {"status": "shutting_down"}.

        The shutdown endpoint must return a JSON response with the
        ``shutting_down`` status regardless of how many sessions are
        active.

        **Validates: Requirements 3.1, 3.4**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-shutdown-001", "sess-shutdown-002"]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200
            body = resp.json()
            assert body == {"status": "shutting_down"}
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_cleans_up_all_sessions(self, test_client):
        """All sessions removed from _active_sessions after shutdown.

        After ``POST /shutdown`` returns, ``_active_sessions`` must be
        empty — every session's resources (SDK wrapper, locks,
        permissions) have been cleaned up.

        **Validates: Requirements 3.1, 3.2**
        """
        from core.agent_manager import agent_manager

        session_ids = [
            "sess-cleanup-001",
            "sess-cleanup-002",
            "sess-cleanup-003",
        ]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200

            # All sessions must be cleaned up
            for sid in session_ids:
                assert sid not in agent_manager._active_sessions, (
                    f"Session {sid} still in _active_sessions after shutdown"
                )
            assert len(agent_manager._active_sessions) == 0
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_fires_hooks_for_each_session(self, test_client):
        """Background hooks are fired for every active session.

        ``disconnect_all()`` must call ``fire()`` on the executor once
        per active session before cleaning up resources.

        **Validates: Requirements 3.1, 3.2**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-fire-001", "sess-fire-002"]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200

            # fire() called once per session
            assert mock_executor.fire.call_count == len(session_ids)

            # Each fire() call received a valid HookContext
            for call in mock_executor.fire.call_args_list:
                ctx = call[0][0]
                assert isinstance(ctx, HookContext)
                assert ctx.agent_id != ""
                assert ctx.session_id in session_ids
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_calls_drain_on_executor(self, test_client):
        """drain() is called on the hook executor during shutdown.

        After firing hooks and cleaning up sessions, ``disconnect_all()``
        must call ``drain()`` with a bounded timeout (8.0s) to give
        hooks a best-effort chance to complete.

        **Validates: Requirements 3.3**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-drain-001"]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200

            # drain() must have been called
            mock_executor.drain.assert_awaited_once()

            # drain() must be called with timeout=8.0
            call_kwargs = mock_executor.drain.call_args
            if call_kwargs[1]:
                assert call_kwargs[1].get("timeout") == 8.0
            else:
                # positional arg
                assert call_kwargs[0][0] == 8.0
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_cancels_cleanup_loop(self, test_client):
        """The idle cleanup loop is cancelled during shutdown.

        ``disconnect_all()`` must cancel the ``_cleanup_task`` so the
        periodic idle-session checker stops running.

        **Validates: Requirements 3.1**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-loop-001"]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        # Create a mock cleanup task that tracks cancellation
        mock_cleanup_task = MagicMock()
        mock_cleanup_task.done.return_value = False
        mock_cleanup_task.cancel = MagicMock()
        agent_manager._cleanup_task = mock_cleanup_task

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200

            # Cleanup task must have been cancelled
            mock_cleanup_task.cancel.assert_called_once()
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_with_no_active_sessions(self, test_client):
        """Shutdown with zero sessions completes cleanly.

        When no sessions are active, ``disconnect_all()`` fast-returns
        without reaching the drain phase. No hooks are fired.

        **Validates: Requirements 3.1, 3.4**
        """
        from core.agent_manager import agent_manager

        mock_executor, _, _, originals = (
            _setup_agent_manager_with_sessions([])
        )

        try:
            with patch("main._startup_complete", True):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200
            assert resp.json() == {"status": "shutting_down"}

            # No sessions → fire() never called
            mock_executor.fire.assert_not_called()

            # No sessions → fast-return before drain phase
            mock_executor.drain.assert_not_awaited()
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_skips_extracted_sessions_da_hook(self, test_client):
        """Sessions with activity_extracted=True skip DA extraction hook.

        When a session has ``activity_extracted=True``, the
        ``skip_hooks`` parameter passed to ``fire()`` must include
        ``"daily_activity_extraction"``.

        **Validates: Requirements 3.1, 3.2**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-skip-001"]
        mock_executor, session_infos, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        # Mark the session as already extracted
        session_infos["sess-skip-001"]["activity_extracted"] = True
        agent_manager._active_sessions["sess-skip-001"] = (
            session_infos["sess-skip-001"]
        )

        def _build_ctx_side_effect(sid, info):
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200

            # fire() was called with skip_hooks including DA extraction
            mock_executor.fire.assert_called_once()
            call_kwargs = mock_executor.fire.call_args
            skip_list = call_kwargs[1].get(
                "skip_hooks", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
            )
            assert skip_list == ["daily_activity_extraction"]
        finally:
            _restore_agent_manager(originals)

    def test_shutdown_continues_on_context_build_failure(self, test_client):
        """Shutdown completes even if _build_hook_context fails for a session.

        If building the hook context fails for one session, the shutdown
        must still clean up that session and continue to the next.
        ``fire()`` should not be called for the failed session.

        **Validates: Requirements 3.1, 3.2**
        """
        from core.agent_manager import agent_manager

        session_ids = ["sess-fail-001", "sess-ok-002"]
        mock_executor, _, contexts, originals = (
            _setup_agent_manager_with_sessions(session_ids)
        )

        call_count = 0

        async def _build_ctx_side_effect(sid, info):
            nonlocal call_count
            call_count += 1
            if sid == "sess-fail-001":
                raise RuntimeError("DB connection lost")
            return contexts[sid]

        try:
            with patch("main._startup_complete", True), \
                 patch.object(
                     agent_manager,
                     "_build_hook_context",
                     new_callable=AsyncMock,
                     side_effect=_build_ctx_side_effect,
                 ):
                resp = test_client.post("/shutdown")

            assert resp.status_code == 200
            assert resp.json() == {"status": "shutting_down"}

            # Both sessions cleaned up despite one context failure
            assert "sess-fail-001" not in agent_manager._active_sessions
            assert "sess-ok-002" not in agent_manager._active_sessions
            assert len(agent_manager._active_sessions) == 0

            # fire() called only for the successful session
            assert mock_executor.fire.call_count == 1
            fired_ctx = mock_executor.fire.call_args[0][0]
            assert fired_ctx.session_id == "sess-ok-002"
        finally:
            _restore_agent_manager(originals)


# ---------------------------------------------------------------------------
# Task 9.2 — Idle loop extraction fires in background
# ---------------------------------------------------------------------------


class TestIdleLoopExtractionFiresInBackground:
    """Integration test: Idle loop extraction fires via fire_single().

    Verifies that when a session has been idle for more than 30 minutes
    (``ACTIVITY_IDLE_SECONDS``), the idle cleanup loop triggers early
    DailyActivity extraction as a background task via
    ``BackgroundHookExecutor.fire_single()``, and that the
    ``activity_extracted`` flag is set to ``True`` before the fire.

    The tests exercise ``_extract_activity_early()`` directly (the
    method called by the idle loop) rather than running the full
    ``_cleanup_stale_sessions_loop()`` coroutine, to avoid timing
    sensitivity from ``asyncio.sleep(60)``.

    **Validates: Requirements 4.1, 4.4**
    """

    @pytest.mark.asyncio
    async def test_extraction_fires_via_fire_single(self):
        """fire_single() is called for an idle session past 30min.

        Sets up a session with ``last_used`` > 30 minutes ago, calls
        ``_extract_activity_early()``, and verifies that
        ``fire_single()`` was invoked on the executor with the
        DailyActivity extraction hook and a valid HookContext.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-idle-extract-001"
        agent_id = "agent-idle-001"
        info = _make_session_info(agent_id=agent_id)
        # Set last_used to 35 minutes ago (past the 30min threshold)
        info["last_used"] = time.time() - (35 * 60)
        info["activity_extracted"] = False

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_extraction_hook = MagicMock()
        mock_extraction_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_extraction_hook]
        mock_executor.fire_single = MagicMock()

        expected_ctx = _make_hook_context(
            session_id=session_id, agent_id=agent_id,
        )

        originals = {
            "executor": agent_manager._hook_executor,
            "sessions": agent_manager._active_sessions.copy(),
        }

        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(
                    session_id, info,
                )

            # fire_single was called exactly once
            mock_executor.fire_single.assert_called_once()

            # Verify the hook passed is the extraction hook
            call_args = mock_executor.fire_single.call_args
            fired_hook = call_args[0][0]
            assert fired_hook.name == "daily_activity_extraction"

            # Verify the context passed is valid
            fired_ctx = call_args[0][1]
            assert isinstance(fired_ctx, HookContext)
            assert fired_ctx.session_id == session_id
            assert fired_ctx.agent_id == agent_id
        finally:
            agent_manager._hook_executor = originals["executor"]
            agent_manager._active_sessions = originals["sessions"]

    @pytest.mark.asyncio
    async def test_activity_extracted_flag_set_before_fire(self):
        """activity_extracted is True before fire_single() is called.

        The idle loop must set ``activity_extracted = True`` BEFORE
        spawning the background task to prevent re-entry from the
        next 60-second loop iteration.  We capture the flag value
        at the moment ``fire_single()`` is called.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-idle-flag-001"
        agent_id = "agent-idle-flag-001"
        info = _make_session_info(agent_id=agent_id)
        info["last_used"] = time.time() - (35 * 60)
        info["activity_extracted"] = False

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_extraction_hook = MagicMock()
        mock_extraction_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_extraction_hook]

        # Capture the flag value when fire_single is called
        flag_at_fire_time = None

        def _capture_fire_single(*args, **kwargs):
            nonlocal flag_at_fire_time
            flag_at_fire_time = info["activity_extracted"]

        mock_executor.fire_single = MagicMock(
            side_effect=_capture_fire_single,
        )

        expected_ctx = _make_hook_context(
            session_id=session_id, agent_id=agent_id,
        )

        originals = {
            "executor": agent_manager._hook_executor,
            "sessions": agent_manager._active_sessions.copy(),
        }

        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(
                    session_id, info,
                )

            # Flag must have been True at the moment fire_single ran
            assert flag_at_fire_time is True

            # Flag remains True after the call
            assert info["activity_extracted"] is True
        finally:
            agent_manager._hook_executor = originals["executor"]
            agent_manager._active_sessions = originals["sessions"]

    @pytest.mark.asyncio
    async def test_extraction_does_not_block_caller(self):
        """_extract_activity_early() returns immediately (non-blocking).

        Since ``fire_single()`` is fire-and-forget, the method should
        return without waiting for the hook to complete.  We verify
        this by checking that ``fire_single()`` was called (not
        awaited) and the method returned promptly.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-idle-nonblock-001"
        agent_id = "agent-idle-nonblock-001"
        info = _make_session_info(agent_id=agent_id)
        info["last_used"] = time.time() - (35 * 60)
        info["activity_extracted"] = False

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_extraction_hook = MagicMock()
        mock_extraction_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_extraction_hook]
        mock_executor.fire_single = MagicMock()

        expected_ctx = _make_hook_context(
            session_id=session_id, agent_id=agent_id,
        )

        originals = {
            "executor": agent_manager._hook_executor,
            "sessions": agent_manager._active_sessions.copy(),
        }

        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions[session_id] = info

            t0 = time.monotonic()
            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                await agent_manager._extract_activity_early(
                    session_id, info,
                )
            elapsed = time.monotonic() - t0

            # fire_single was called (synchronous, not awaited)
            mock_executor.fire_single.assert_called_once()

            # Method returned within 1 second (Req 4.4)
            assert elapsed < 1.0, (
                f"_extract_activity_early took {elapsed:.2f}s, "
                "expected < 1.0s (non-blocking)"
            )
        finally:
            agent_manager._hook_executor = originals["executor"]
            agent_manager._active_sessions = originals["sessions"]

    @pytest.mark.asyncio
    async def test_idle_loop_triggers_extraction_for_idle_session(self):
        """The idle cleanup loop calls _extract_activity_early for idle sessions.

        Simulates one iteration of the idle cleanup loop by directly
        checking the idle-session detection logic: a session with
        ``last_used`` > 30 minutes ago and ``activity_extracted=False``
        should be selected for extraction.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-idle-loop-001"
        agent_id = "agent-idle-loop-001"
        info = _make_session_info(agent_id=agent_id)
        # 35 minutes idle — past the 30min threshold
        info["last_used"] = time.time() - (35 * 60)
        info["activity_extracted"] = False

        mock_executor = MagicMock(spec=BackgroundHookExecutor)
        mock_extraction_hook = MagicMock()
        mock_extraction_hook.name = "daily_activity_extraction"
        mock_executor.hooks = [mock_extraction_hook]
        mock_executor.fire_single = MagicMock()

        expected_ctx = _make_hook_context(
            session_id=session_id, agent_id=agent_id,
        )

        originals = {
            "executor": agent_manager._hook_executor,
            "sessions": agent_manager._active_sessions.copy(),
            "cleanup_task": agent_manager._cleanup_task,
        }

        try:
            agent_manager._hook_executor = mock_executor
            agent_manager._active_sessions = {session_id: info}

            # Simulate the idle detection logic from the loop
            now = time.time()
            idle_for_extraction = [
                (sid, sess_info)
                for sid, sess_info in agent_manager._active_sessions.items()
                if (
                    now - sess_info.get(
                        "last_used", sess_info["created_at"],
                    ) > agent_manager.ACTIVITY_IDLE_SECONDS
                    and not sess_info.get("activity_extracted")
                )
            ]

            # Session should be detected as idle
            assert len(idle_for_extraction) == 1
            assert idle_for_extraction[0][0] == session_id

            # Now call _extract_activity_early as the loop would
            with patch.object(
                agent_manager,
                "_build_hook_context",
                new_callable=AsyncMock,
                return_value=expected_ctx,
            ):
                for sid, sess_info in idle_for_extraction:
                    await agent_manager._extract_activity_early(
                        sid, sess_info,
                    )

            # fire_single was called
            mock_executor.fire_single.assert_called_once()

            # Flag is now True
            assert info["activity_extracted"] is True
        finally:
            agent_manager._hook_executor = originals["executor"]
            agent_manager._active_sessions = originals["sessions"]
            agent_manager._cleanup_task = originals["cleanup_task"]

    @pytest.mark.asyncio
    async def test_non_idle_session_not_selected_for_extraction(self):
        """Sessions idle < 30min are NOT selected for extraction.

        A session with ``last_used`` only 10 minutes ago should not
        appear in the idle-for-extraction list.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-active-001"
        info = _make_session_info(agent_id="agent-active-001")
        # Only 10 minutes idle — below the 30min threshold
        info["last_used"] = time.time() - (10 * 60)
        info["activity_extracted"] = False

        originals = {
            "sessions": agent_manager._active_sessions.copy(),
        }

        try:
            agent_manager._active_sessions = {session_id: info}

            now = time.time()
            idle_for_extraction = [
                (sid, sess_info)
                for sid, sess_info in agent_manager._active_sessions.items()
                if (
                    now - sess_info.get(
                        "last_used", sess_info["created_at"],
                    ) > agent_manager.ACTIVITY_IDLE_SECONDS
                    and not sess_info.get("activity_extracted")
                )
            ]

            # Session should NOT be detected as idle
            assert len(idle_for_extraction) == 0
        finally:
            agent_manager._active_sessions = originals["sessions"]

    @pytest.mark.asyncio
    async def test_already_extracted_session_not_selected(self):
        """Sessions with activity_extracted=True are skipped.

        Even if a session is idle > 30min, if ``activity_extracted``
        is already ``True``, it should not be selected for extraction
        again.

        **Validates: Requirements 4.1, 4.4**
        """
        from core.agent_manager import agent_manager

        session_id = "sess-already-001"
        info = _make_session_info(agent_id="agent-already-001")
        # 35 minutes idle but already extracted
        info["last_used"] = time.time() - (35 * 60)
        info["activity_extracted"] = True

        originals = {
            "sessions": agent_manager._active_sessions.copy(),
        }

        try:
            agent_manager._active_sessions = {session_id: info}

            now = time.time()
            idle_for_extraction = [
                (sid, sess_info)
                for sid, sess_info in agent_manager._active_sessions.items()
                if (
                    now - sess_info.get(
                        "last_used", sess_info["created_at"],
                    ) > agent_manager.ACTIVITY_IDLE_SECONDS
                    and not sess_info.get("activity_extracted")
                )
            ]

            # Session should NOT be selected (already extracted)
            assert len(idle_for_extraction) == 0
        finally:
            agent_manager._active_sessions = originals["sessions"]


# ---------------------------------------------------------------------------
# Task 9.3 — Concurrent session close with git lock
# ---------------------------------------------------------------------------


class TestConcurrentSessionCloseWithGitLock:
    """Integration tests for concurrent session close with git_lock.

    Verifies that when multiple sessions close simultaneously, their
    background hook tasks run concurrently but git operations are
    properly serialized via the shared ``asyncio.Lock`` (``git_lock``).

    The test creates a real ``BackgroundHookExecutor`` with a real
    ``SessionLifecycleHookManager``, registers mock hooks that simulate
    git operations (acquire git_lock, sleep briefly, release), fires
    5 hook tasks simultaneously, and verifies:

    - All 5 tasks complete successfully (no errors)
    - Git operations were serialized (no overlapping execution times)
    - No git lock contention errors occur

    **Validates: Requirements 9c.7, 9e.14**
    """

    @pytest.mark.asyncio
    async def test_five_sessions_close_all_hooks_complete(self):
        """Five sessions close simultaneously; all hook tasks complete.

        Creates a real ``BackgroundHookExecutor`` with a mock hook that
        acquires the ``git_lock``, sleeps briefly, and releases it.
        Fires 5 items into the serialization queue and drains.  All 5
        must complete (serialized, one at a time) with zero cancellations.

        **Validates: Requirements 9c.7, 9e.14**
        """
        hook_manager = SessionLifecycleHookManager(timeout_seconds=10.0)
        executor = BackgroundHookExecutor(hook_manager)
        git_lock = executor.git_lock

        # Track execution per session
        execution_log: list[str] = []

        class GitSimHook:
            """Mock hook simulating a git operation under git_lock."""

            @property
            def name(self) -> str:
                return "git_sim_hook"

            async def execute(self, context: HookContext) -> None:
                async with git_lock:
                    execution_log.append(f"start-{context.session_id}")
                    await asyncio.sleep(0.02)
                    execution_log.append(f"end-{context.session_id}")

        hook_manager.register(GitSimHook())

        # Fire 5 items into the serialization queue
        contexts = [
            _make_hook_context(
                session_id=f"sess-conc-{i:03d}",
                agent_id=f"agent-conc-{i:03d}",
            )
            for i in range(5)
        ]
        for ctx in contexts:
            executor.fire(ctx)

        # pending_count reflects queued items + worker
        assert executor.pending_count >= 5

        done, cancelled = await executor.drain(timeout=10.0)

        # Worker task completes after processing all items
        assert cancelled == 0
        assert executor.pending_count == 0
        # All 5 sessions should have start+end entries (serialized)
        assert len(execution_log) == 10

    @pytest.mark.asyncio
    async def test_git_operations_serialized_no_overlap(self):
        """Git operations from sessions never overlap (serialized queue).

        Records wall-clock timestamps when each hook acquires and
        releases the ``git_lock``.  With the serialization queue,
        hooks execute one at a time so git operations are inherently
        serialized even without the git_lock.

        **Validates: Requirements 9c.7**
        """
        hook_manager = SessionLifecycleHookManager(timeout_seconds=10.0)
        executor = BackgroundHookExecutor(hook_manager)
        git_lock = executor.git_lock

        # Record (session_id, acquire_time, release_time)
        intervals: list[tuple[str, float, float]] = []

        class TimedGitHook:
            @property
            def name(self) -> str:
                return "timed_git_hook"

            async def execute(self, context: HookContext) -> None:
                async with git_lock:
                    t_acquire = time.monotonic()
                    await asyncio.sleep(0.03)
                    t_release = time.monotonic()
                    intervals.append(
                        (context.session_id, t_acquire, t_release)
                    )

        hook_manager.register(TimedGitHook())

        contexts = [
            _make_hook_context(
                session_id=f"sess-serial-{i:03d}",
                agent_id=f"agent-serial-{i:03d}",
            )
            for i in range(5)
        ]
        for ctx in contexts:
            executor.fire(ctx)

        await executor.drain(timeout=10.0)

        assert len(intervals) == 5

        # Sort by acquire time and verify no overlaps
        intervals.sort(key=lambda x: x[1])
        for i in range(len(intervals) - 1):
            _, _, release_i = intervals[i]
            _, acquire_next, _ = intervals[i + 1]
            assert release_i <= acquire_next, (
                f"Overlap detected: session {intervals[i][0]} released at "
                f"{release_i:.6f} but session {intervals[i + 1][0]} "
                f"acquired at {acquire_next:.6f}"
            )

    @pytest.mark.asyncio
    async def test_no_git_lock_contention_errors(self):
        """No errors raised from git_lock contention across 5 sessions.

        Registers a hook that acquires the lock and performs a simulated
        git operation.  Captures any exceptions raised during execution.
        All 5 items must complete without errors (serialized via queue).

        **Validates: Requirements 9c.7, 9e.14**
        """
        hook_manager = SessionLifecycleHookManager(timeout_seconds=10.0)
        executor = BackgroundHookExecutor(hook_manager)
        git_lock = executor.git_lock

        errors: list[tuple[str, Exception]] = []
        successes: list[str] = []

        class SafeGitHook:
            @property
            def name(self) -> str:
                return "safe_git_hook"

            async def execute(self, context: HookContext) -> None:
                try:
                    async with git_lock:
                        await asyncio.sleep(0.01)
                    successes.append(context.session_id)
                except Exception as exc:
                    errors.append((context.session_id, exc))

        hook_manager.register(SafeGitHook())

        contexts = [
            _make_hook_context(
                session_id=f"sess-safe-{i:03d}",
                agent_id=f"agent-safe-{i:03d}",
            )
            for i in range(5)
        ]
        for ctx in contexts:
            executor.fire(ctx)

        done, cancelled = await executor.drain(timeout=10.0)

        assert cancelled == 0
        assert len(errors) == 0
        assert len(successes) == 5

    @pytest.mark.asyncio
    async def test_pending_count_reflects_queued_items(self):
        """pending_count reflects queued items while worker processes them.

        Fires 5 items into the serialization queue.  The worker
        processes them one at a time.  pending_count should reflect
        the total queued + in-flight items.

        **Validates: Requirements 9e.14**
        """
        hook_manager = SessionLifecycleHookManager(timeout_seconds=10.0)
        executor = BackgroundHookExecutor(hook_manager)

        gate = asyncio.Event()
        entered_count = 0

        class GatedHook:
            @property
            def name(self) -> str:
                return "gated_hook"

            async def execute(self, context: HookContext) -> None:
                nonlocal entered_count
                entered_count += 1
                await gate.wait()

        hook_manager.register(GatedHook())

        contexts = [
            _make_hook_context(
                session_id=f"sess-gate-{i:03d}",
                agent_id=f"agent-gate-{i:03d}",
            )
            for i in range(5)
        ]
        for ctx in contexts:
            executor.fire(ctx)

        # Let worker start processing the first item
        await asyncio.sleep(0.05)

        # Worker is processing first item, 4 remain in queue + worker = 5
        assert executor.pending_count >= 4
        # Only 1 hook entered (serialized — worker processes one at a time)
        assert entered_count == 1

        # Release all items
        gate.set()
        done, cancelled = await executor.drain(timeout=5.0)

        assert cancelled == 0
        assert executor.pending_count == 0
        # All 5 hooks eventually executed
        assert entered_count == 5

    @pytest.mark.asyncio
    async def test_multiple_hooks_with_git_lock_all_serialize(self):
        """Multiple hooks per item, only the git hook uses the lock.

        Registers a fast non-git hook and a git hook that acquires the
        lock.  Fires 5 items.  With the serialization queue, all hooks
        execute one item at a time.  Git hooks are additionally
        serialized by the git_lock within each item.

        **Validates: Requirements 9c.7, 9e.14**
        """
        hook_manager = SessionLifecycleHookManager(timeout_seconds=10.0)
        executor = BackgroundHookExecutor(hook_manager)
        git_lock = executor.git_lock

        fast_log: list[str] = []
        git_intervals: list[tuple[str, float, float]] = []

        class FastHook:
            @property
            def name(self) -> str:
                return "fast_hook"

            async def execute(self, context: HookContext) -> None:
                fast_log.append(context.session_id)

        class GitLockHook:
            @property
            def name(self) -> str:
                return "git_lock_hook"

            async def execute(self, context: HookContext) -> None:
                async with git_lock:
                    t0 = time.monotonic()
                    await asyncio.sleep(0.02)
                    t1 = time.monotonic()
                    git_intervals.append(
                        (context.session_id, t0, t1)
                    )

        hook_manager.register(FastHook())
        hook_manager.register(GitLockHook())

        contexts = [
            _make_hook_context(
                session_id=f"sess-multi-{i:03d}",
                agent_id=f"agent-multi-{i:03d}",
            )
            for i in range(5)
        ]
        for ctx in contexts:
            executor.fire(ctx)

        done, cancelled = await executor.drain(timeout=10.0)

        assert cancelled == 0

        # All 5 fast hooks ran
        assert len(fast_log) == 5

        # All 5 git hooks ran and were serialized
        assert len(git_intervals) == 5
        git_intervals.sort(key=lambda x: x[1])
        for i in range(len(git_intervals) - 1):
            _, _, release_i = git_intervals[i]
            _, acquire_next, _ = git_intervals[i + 1]
            assert release_i <= acquire_next, (
                f"Git overlap: {git_intervals[i][0]} released at "
                f"{release_i:.6f}, {git_intervals[i + 1][0]} "
                f"acquired at {acquire_next:.6f}"
            )
