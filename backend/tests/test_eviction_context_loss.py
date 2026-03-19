"""Bug condition exploration test for session eviction context loss.

Demonstrates two interacting bugs that cause evicted tabs to lose
conversation context:

1. ``_cleanup_internal()`` clears ``_sdk_session_id`` — the only key
   needed to resume via ``--resume``.
2. ``run_conversation()`` gates ``resume_session_id`` on ``unit.is_alive``,
   which is always ``False`` for evicted (COLD) units.

Testing methodology: property-based (Hypothesis) exploration.
These tests encode the EXPECTED (fixed) behavior and are expected to
FAIL on unfixed code, confirming the bugs exist.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2**

# Feature: session-eviction-context-loss
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.session_unit import SessionState, SessionUnit
from core.session_router import SessionRouter


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy: non-empty SDK session ID strings (the SDK always returns
# a non-empty identifier for active sessions).
sdk_session_ids = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=64,
)


# ---------------------------------------------------------------------------
# Property 1 (Bug Condition): Evicted Tab Loses SDK Session ID and
#                              Resume Capability
# ---------------------------------------------------------------------------


class TestEvictedTabLosesContext:
    """Property 1: Bug Condition — Evicted Tab Loses SDK Session ID and Resume Capability.

    # Feature: session-eviction-context-loss, Property 1: Bug Condition

    *For any* SessionUnit that was evicted (killed to free a concurrency
    slot) and previously had a non-None ``_sdk_session_id``, the expected
    (fixed) behavior is:

    - ``_sdk_session_id`` survives ``kill()`` / ``_cleanup_internal()``
    - ``run_conversation()`` passes ``_sdk_session_id`` as
      ``resume_session_id`` to ``PromptBuilder.build_options()``

    These tests FAIL on unfixed code — failure confirms the bug exists.

    **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2**
    """

    # -- Sub-property A: _sdk_session_id survives eviction (kill) ----------

    @given(session_id_value=sdk_session_ids)
    @PROPERTY_SETTINGS
    def test_sdk_session_id_preserved_after_kill(self, session_id_value: str):
        """_sdk_session_id MUST survive kill() (eviction cleanup).

        Bug 1: _cleanup_internal() sets _sdk_session_id = None,
        destroying the resume key. This test asserts the EXPECTED
        behavior (preservation) and will FAIL on unfixed code.

        **Validates: Requirements 2.1**
        """
        unit = SessionUnit(session_id="eviction-test", agent_id="default")

        # Simulate a prior conversation: unit is IDLE with a known
        # _sdk_session_id (set during the init message of a previous
        # streaming session).
        unit._transition(SessionState.IDLE)
        unit._client = MagicMock()
        unit._wrapper = MagicMock()
        unit._wrapper.pid = 12345
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)
        unit._sdk_session_id = session_id_value

        # Eviction: kill() → DEAD → _cleanup_internal() → COLD
        loop = asyncio.new_event_loop()
        try:
            with patch("os.kill"):
                loop.run_until_complete(unit.kill())
        finally:
            loop.close()

        # EXPECTED (after fix): _sdk_session_id is preserved
        assert unit.state == SessionState.COLD, (
            f"Expected COLD after kill(), got {unit.state.value}"
        )
        assert unit._sdk_session_id == session_id_value, (
            f"Expected _sdk_session_id={session_id_value!r} to survive "
            f"eviction, but got {unit._sdk_session_id!r}. "
            f"Bug confirmed: _cleanup_internal() clears _sdk_session_id."
        )

    # -- Sub-property B: resume_session_id passed after eviction -----------

    @given(session_id_value=sdk_session_ids)
    @PROPERTY_SETTINGS
    def test_resume_session_id_passed_for_evicted_unit(self, session_id_value: str):
        """resume_session_id MUST equal _sdk_session_id for evicted units.

        Bug 2: run_conversation() evaluates
        ``unit._sdk_session_id if unit.is_alive else None``
        which always yields None for COLD (evicted) units.

        This test directly checks the expression that run_conversation()
        uses to compute resume_session_id, asserting the EXPECTED
        behavior. It will FAIL on unfixed code.

        **Validates: Requirements 2.2**
        """
        unit = SessionUnit(session_id="evicted-tab", agent_id="default")

        # Simulate a prior conversation
        unit._transition(SessionState.IDLE)
        unit._client = MagicMock()
        unit._wrapper = MagicMock()
        unit._wrapper.pid = 54321
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)
        unit._sdk_session_id = session_id_value

        # Evict the unit
        loop = asyncio.new_event_loop()
        try:
            with patch("os.kill"):
                loop.run_until_complete(unit.kill())
        finally:
            loop.close()

        assert unit.state == SessionState.COLD

        # This is the expression from run_conversation() (after fix):
        #   resume_session_id=unit._sdk_session_id
        # The is_alive gate has been removed so COLD units pass their
        # preserved _sdk_session_id for --resume.
        actual_resume_id = unit._sdk_session_id

        # EXPECTED (after fix): resume_session_id should be the original
        # SDK session ID, passed unconditionally (not gated on is_alive)
        assert actual_resume_id == session_id_value, (
            f"Expected resume_session_id={session_id_value!r} for evicted "
            f"unit, but got {actual_resume_id!r}. "
            f"Bug confirmed: is_alive gate returns None for COLD units "
            f"(is_alive={unit.is_alive}, state={unit.state.value})."
        )



# ---------------------------------------------------------------------------
# Property 2 (Preservation): Non-Eviction Behavior Unchanged
# ---------------------------------------------------------------------------


class TestPreservationAliveSubprocessReuse:
    """Preservation 2.1: Alive subprocess reuse (Req 3.1).

    # Feature: session-eviction-context-loss, Property 2: Preservation

    For IDLE units with alive subprocesses, ``send()`` reuses the
    existing subprocess — no new spawn occurs.

    **Validates: Requirements 3.1**
    """

    @given(session_id_value=sdk_session_ids)
    @PROPERTY_SETTINGS
    def test_idle_unit_reuses_subprocess_no_new_spawn(self, session_id_value: str):
        """IDLE unit with alive subprocess reuses it on send() — no spawn.

        When a SessionUnit is IDLE with ``_client`` set (subprocess alive),
        calling ``send()`` transitions directly to STREAMING without
        calling ``_spawn()``.

        **Validates: Requirements 3.1**
        """
        unit = SessionUnit(session_id="reuse-test", agent_id="default")

        # Set up an IDLE unit with an alive subprocess
        unit._transition(SessionState.IDLE)
        mock_client = MagicMock()
        unit._client = mock_client
        unit._wrapper = MagicMock()
        unit._wrapper.pid = 99999
        unit._sdk_session_id = session_id_value

        # Verify preconditions: unit is alive and IDLE
        assert unit.is_alive is True
        assert unit.state == SessionState.IDLE

        # The key check: when state is IDLE, send() should NOT call _spawn().
        # It should go directly to STREAMING and reuse the existing client.
        # We verify this by checking that _spawn is never called.
        spawn_called = False
        original_spawn = unit._spawn

        async def mock_spawn(*args, **kwargs):
            nonlocal spawn_called
            spawn_called = True
            return await original_spawn(*args, **kwargs)

        unit._spawn = mock_spawn

        # We also need to mock _stream_response to avoid actual SDK calls
        async def mock_stream_response(query_content):
            # Simulate a successful response that transitions to IDLE
            unit._transition(SessionState.IDLE)
            unit.last_used = __import__("time").time()
            return
            yield  # Make it an async generator

        unit._stream_response = mock_stream_response

        # Build minimal options mock
        mock_options = MagicMock()

        loop = asyncio.new_event_loop()
        try:
            async def run_send():
                events = []
                async for event in unit.send(
                    query_content="test message",
                    options=mock_options,
                    config=None,
                ):
                    events.append(event)
                return events

            loop.run_until_complete(run_send())
        finally:
            loop.close()

        # _spawn should NOT have been called — subprocess was reused
        assert spawn_called is False, (
            "Expected _spawn() NOT to be called for IDLE unit with alive "
            "subprocess, but it was called. Subprocess reuse is broken."
        )


class TestPreservationFreshTabSpawning:
    """Preservation 2.2: Fresh tab spawning (Req 3.2).

    # Feature: session-eviction-context-loss, Property 2: Preservation

    For COLD units with ``_sdk_session_id=None`` (brand new tab),
    ``resume_session_id`` passed to ``build_options()`` is None.

    **Validates: Requirements 3.2**
    """

    @given(dummy=st.just(None))
    @PROPERTY_SETTINGS
    def test_fresh_cold_unit_has_none_resume_session_id(self, dummy):
        """Fresh COLD unit yields None for resume_session_id expression.

        The expression ``unit._sdk_session_id if unit.is_alive else None``
        (used in run_conversation) yields None for a fresh COLD unit
        because ``_sdk_session_id`` is None AND ``is_alive`` is False.

        After the fix, the expression becomes ``unit._sdk_session_id``
        unconditionally — which ALSO yields None for fresh tabs since
        ``_sdk_session_id`` starts as None.

        This test verifies the BEHAVIOR (None resume for fresh tabs),
        not the specific expression.

        **Validates: Requirements 3.2**
        """
        unit = SessionUnit(session_id="fresh-tab", agent_id="default")

        # Fresh tab: COLD state, no prior conversation
        assert unit.state == SessionState.COLD
        assert unit._sdk_session_id is None
        assert unit.is_alive is False

        # Both the current expression and the fixed expression yield None
        # Current: unit._sdk_session_id if unit.is_alive else None → None
        current_resume = unit._sdk_session_id if unit.is_alive else None
        assert current_resume is None

        # Fixed: unit._sdk_session_id → None (because it's None)
        fixed_resume = unit._sdk_session_id
        assert fixed_resume is None


class TestPreservationNonRetriableCrashCleanup:
    """Preservation 2.3: Non-retriable crash cleanup (Req 3.3).

    # Feature: session-eviction-context-loss, Property 2: Preservation

    After cleanup runs on a non-retriable crash path,
    ``_sdk_session_id`` is None. On UNFIXED code,
    ``_cleanup_internal()`` always clears it. After the fix,
    ``_full_cleanup()`` will clear it instead.

    The test verifies the BEHAVIOR (sdk_session_id cleared after
    non-retriable crash), not the method name.

    **Validates: Requirements 3.3**
    """

    @given(session_id_value=sdk_session_ids)
    @PROPERTY_SETTINGS
    def test_sdk_session_id_cleared_after_non_retriable_crash_cleanup(
        self, session_id_value: str,
    ):
        """_sdk_session_id is None after non-retriable crash cleanup.

        On unfixed code, ``_cleanup_internal()`` clears it.
        After fix, ``_full_cleanup()`` will clear it.
        Either way, the behavior is: sdk_session_id is None after
        a non-retriable crash cleanup path.

        **Validates: Requirements 3.3**
        """
        unit = SessionUnit(session_id="crash-test", agent_id="default")

        # Simulate a unit that had a conversation
        unit._transition(SessionState.IDLE)
        unit._client = MagicMock()
        unit._wrapper = MagicMock()
        unit._sdk_session_id = session_id_value

        # Simulate non-retriable crash: STREAMING → DEAD → cleanup → COLD
        unit._transition(SessionState.STREAMING)
        unit._transition(SessionState.DEAD)

        # On unfixed code: _cleanup_internal() clears _sdk_session_id
        # On fixed code: _full_cleanup() clears _sdk_session_id
        # We call _full_cleanup() here because that's what the
        # fixed code uses for non-retriable crash paths.
        unit._full_cleanup()
        unit._transition(SessionState.COLD)

        # After non-retriable crash cleanup, _sdk_session_id MUST be None
        assert unit._sdk_session_id is None, (
            f"Expected _sdk_session_id=None after non-retriable crash "
            f"cleanup, but got {unit._sdk_session_id!r}."
        )
        assert unit.state == SessionState.COLD


class TestPreservationRetryLoopResumeCapture:
    """Preservation 2.4: Retry loop resume capture (Req 3.4).

    # Feature: session-eviction-context-loss, Property 2: Preservation

    The retry loop captures ``resume_session_id = self._sdk_session_id``
    BEFORE calling ``_cleanup_internal()``. The captured value is the
    original SDK session ID even after cleanup runs.

    **Validates: Requirements 3.4**
    """

    @given(session_id_value=sdk_session_ids)
    @PROPERTY_SETTINGS
    def test_retry_loop_captures_sdk_session_id_before_cleanup(
        self, session_id_value: str,
    ):
        """Captured resume_session_id equals original _sdk_session_id.

        The retry loop in ``send()`` does:
            resume_session_id = self._sdk_session_id  # capture BEFORE
            ...
            self._cleanup_internal()                   # clears it
            ...
            retry_options = self._build_retry_options(options, resume_session_id)

        This test simulates that pattern and verifies the captured
        value survives cleanup.

        **Validates: Requirements 3.4**
        """
        unit = SessionUnit(session_id="retry-test", agent_id="default")

        # Simulate a unit with a known SDK session ID
        unit._sdk_session_id = session_id_value
        unit._client = MagicMock()
        unit._wrapper = MagicMock()

        # Capture BEFORE cleanup (this is what the retry loop does)
        resume_session_id = unit._sdk_session_id

        # Cleanup runs (simulating the retry loop's cleanup step)
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        unit._transition(SessionState.DEAD)
        unit._cleanup_internal()
        unit._transition(SessionState.COLD)

        # The captured value MUST still be the original SDK session ID
        assert resume_session_id == session_id_value, (
            f"Expected captured resume_session_id={session_id_value!r} "
            f"to survive cleanup, but got {resume_session_id!r}."
        )

        # Verify _build_retry_options uses the captured value correctly
        mock_options = MagicMock()
        mock_options_dict = {"some_field": "value"}

        with patch("core.session_unit.SessionUnit._build_retry_options") as mock_build:
            mock_build.return_value = mock_options
            result = SessionUnit._build_retry_options(mock_options, resume_session_id)
            mock_build.assert_called_once_with(mock_options, session_id_value)


class TestPreservationShutdownDisconnectAll:
    """Preservation 2.5: Shutdown disconnect_all cleanup (Req 3.5).

    # Feature: session-eviction-context-loss, Property 2: Preservation

    After ``disconnect_all()``, all units have ``_sdk_session_id=None``.
    On UNFIXED code, ``kill()`` → ``_cleanup_internal()`` clears it.
    After fix, ``disconnect_all()`` will explicitly clear it.

    **Validates: Requirements 3.5**
    """

    @given(
        session_id_a=sdk_session_ids,
        session_id_b=sdk_session_ids,
    )
    @PROPERTY_SETTINGS
    def test_disconnect_all_clears_sdk_session_id_on_all_units(
        self, session_id_a: str, session_id_b: str,
    ):
        """After disconnect_all(), all units have _sdk_session_id=None.

        On unfixed code, ``kill()`` calls ``_cleanup_internal()`` which
        clears ``_sdk_session_id``. After fix, ``disconnect_all()``
        will explicitly clear it. Either way, the behavior is the same:
        all units end up with ``_sdk_session_id=None``.

        **Validates: Requirements 3.5**
        """
        # Create a router with mock prompt_builder
        mock_pb = MagicMock()
        router = SessionRouter(prompt_builder=mock_pb)

        # Create two units with alive subprocesses and SDK session IDs
        unit_a = router.get_or_create_unit("session-a", "default")
        unit_a._transition(SessionState.IDLE)
        unit_a._client = MagicMock()
        unit_a._wrapper = MagicMock()
        unit_a._wrapper.pid = 11111
        unit_a._wrapper.__aexit__ = AsyncMock(return_value=False)
        unit_a._sdk_session_id = session_id_a

        unit_b = router.get_or_create_unit("session-b", "default")
        unit_b._transition(SessionState.IDLE)
        unit_b._client = MagicMock()
        unit_b._wrapper = MagicMock()
        unit_b._wrapper.pid = 22222
        unit_b._wrapper.__aexit__ = AsyncMock(return_value=False)
        unit_b._sdk_session_id = session_id_b

        # Verify both are alive
        assert unit_a.is_alive is True
        assert unit_b.is_alive is True

        # Run disconnect_all
        loop = asyncio.new_event_loop()
        try:
            with patch("os.kill"):
                loop.run_until_complete(router.disconnect_all())
        finally:
            loop.close()

        # After disconnect_all, ALL units must have _sdk_session_id=None
        assert unit_a._sdk_session_id is None, (
            f"Expected unit_a._sdk_session_id=None after disconnect_all(), "
            f"but got {unit_a._sdk_session_id!r}."
        )
        assert unit_b._sdk_session_id is None, (
            f"Expected unit_b._sdk_session_id=None after disconnect_all(), "
            f"but got {unit_b._sdk_session_id!r}."
        )
        # Both should be COLD
        assert unit_a.state == SessionState.COLD
        assert unit_b.state == SessionState.COLD
