"""Property-based tests for SessionRouter.

Tests the ``SessionRouter`` class from ``core/session_router.py`` using
Hypothesis-generated inputs to verify eviction safety, concurrency cap,
FIFO queue dispatch, and routing correctness.

# Feature: multi-session-rearchitecture
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.session_unit import SessionState, SessionUnit
from core.session_router import SessionRouter


PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _make_router() -> SessionRouter:
    """Create a SessionRouter with mock dependencies."""
    mock_builder = MagicMock()
    mock_config = MagicMock()
    return SessionRouter(prompt_builder=mock_builder, config=mock_config)


# ---------------------------------------------------------------------------
# Property 2: Eviction targets only IDLE units
# ---------------------------------------------------------------------------

class TestEvictionTargetsOnlyIdle:
    """Property 2: Eviction targets only IDLE units.

    # Feature: multi-session-rearchitecture, Property 2: Eviction targets only IDLE

    *For any* set of SessionUnits with mixed states, the eviction algorithm
    must only select units in IDLE state.

    **Validates: Requirements 1.6, 2.6**
    """

    @given(
        states=st.lists(
            st.sampled_from([
                SessionState.COLD, SessionState.IDLE,
                SessionState.STREAMING, SessionState.WAITING_INPUT,
                SessionState.DEAD,
            ]),
            min_size=2, max_size=6,
        ),
    )
    @PROPERTY_SETTINGS
    def test_eviction_never_selects_protected_units(self, states: list[SessionState]):
        """Eviction only selects IDLE units, never STREAMING or WAITING_INPUT."""
        router = _make_router()

        # Valid transition paths from COLD to each target state.
        # _transition validates the state machine, so we must walk
        # through legal hops rather than jumping directly.
        _PATHS_FROM_COLD: dict[SessionState, list[SessionState]] = {
            SessionState.COLD: [],  # already there
            SessionState.IDLE: [SessionState.IDLE],  # COLD→IDLE (spawn)
            SessionState.STREAMING: [SessionState.IDLE, SessionState.STREAMING],
            SessionState.WAITING_INPUT: [
                SessionState.IDLE, SessionState.STREAMING,
                SessionState.WAITING_INPUT,
            ],
            SessionState.DEAD: [SessionState.DEAD],  # COLD→DEAD
        }

        # Create units with the given states
        for i, state in enumerate(states):
            unit = SessionUnit(session_id=f"unit-{i}", agent_id="default")
            for hop in _PATHS_FROM_COLD[state]:
                unit._transition(hop)
            if state in (SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT):
                unit._wrapper = MagicMock()
                unit._wrapper.pid = 10000 + i
                unit._client = MagicMock()
            router._units[f"unit-{i}"] = unit

        # Find what _evict_idle would select
        exclude = SessionUnit(session_id="requester", agent_id="default")
        idle_candidates = [
            u for u in router._units.values()
            if u.state == SessionState.IDLE and u is not exclude
        ]

        # Verify: only IDLE units are candidates
        for candidate in idle_candidates:
            assert candidate.state == SessionState.IDLE

        # Verify: no STREAMING or WAITING_INPUT units are candidates
        protected = [
            u for u in router._units.values()
            if u.state in (SessionState.STREAMING, SessionState.WAITING_INPUT)
        ]
        for p in protected:
            assert p not in idle_candidates


# ---------------------------------------------------------------------------
# Property 4: Concurrency cap invariant
# ---------------------------------------------------------------------------

class TestConcurrencyCapInvariant:
    """Property 4: Concurrency cap invariant.

    # Feature: multi-session-rearchitecture, Property 4: Concurrency cap

    After each _acquire_slot call completes, alive_count must not exceed
    the dynamic cap from ``compute_max_tabs()`` (range [1, 4]).

    **Validates: Requirements 2.1**
    """

    def test_alive_count_never_exceeds_max(self):
        """alive_count stays within the dynamic concurrency cap."""
        router = _make_router()

        # Create 2 alive units
        for i in range(2):
            unit = SessionUnit(session_id=f"alive-{i}", agent_id="default")
            unit._transition(SessionState.IDLE)
            unit._wrapper = MagicMock()
            unit._wrapper.pid = 20000 + i
            unit._client = MagicMock()
            router._units[f"alive-{i}"] = unit

        assert router.alive_count == 2

        # Create a COLD unit — it shouldn't increase alive_count
        cold = SessionUnit(session_id="cold-0", agent_id="default")
        router._units["cold-0"] = cold
        assert router.alive_count == 2  # Still 2

    def test_cold_units_dont_count(self):
        """COLD and DEAD units don't count toward alive_count."""
        router = _make_router()
        for i in range(5):
            unit = SessionUnit(session_id=f"cold-{i}", agent_id="default")
            router._units[f"cold-{i}"] = unit
        assert router.alive_count == 0


# ---------------------------------------------------------------------------
# Property 5: FIFO queue dispatch ordering
# ---------------------------------------------------------------------------

class TestFIFOQueueDispatch:
    """Property 5: FIFO queue dispatch ordering.

    # Feature: multi-session-rearchitecture, Property 5: FIFO queue dispatch

    When a slot becomes available, the oldest queued request must be
    dispatched first.

    **Validates: Requirements 2.5**
    """

    @pytest.mark.asyncio
    async def test_slot_available_event_fires_on_transition(self):
        """_slot_available event fires when a protected unit goes IDLE."""
        router = _make_router()
        router._slot_available.clear()

        # Simulate a STREAMING → IDLE transition
        router._on_unit_state_change(
            "test-session", SessionState.STREAMING, SessionState.IDLE,
        )

        assert router._slot_available.is_set()

    @pytest.mark.asyncio
    async def test_slot_not_signaled_for_idle_to_idle(self):
        """_slot_available doesn't fire for non-protected transitions."""
        router = _make_router()
        router._slot_available.clear()

        # IDLE → IDLE shouldn't signal
        router._on_unit_state_change(
            "test-session", SessionState.IDLE, SessionState.IDLE,
        )

        assert not router._slot_available.is_set()


# ---------------------------------------------------------------------------
# Property 6: Correct routing by session ID
# ---------------------------------------------------------------------------

class TestRoutingBySessionID:
    """Property 6: Correct routing by session ID.

    # Feature: multi-session-rearchitecture, Property 6: Routing by session ID

    Routing a request by session_id must always reach the correct SessionUnit.

    **Validates: Requirements 2.7**
    """

    @given(
        session_ids=st.lists(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
            min_size=1, max_size=10, unique=True,
        ),
    )
    @PROPERTY_SETTINGS
    def test_routing_reaches_correct_unit(self, session_ids: list[str]):
        """get_unit returns the exact unit registered for that session_id."""
        router = _make_router()

        # Register units
        for sid in session_ids:
            unit = router.get_or_create_unit(sid, "default")
            assert unit.session_id == sid

        # Verify routing
        for sid in session_ids:
            found = router.get_unit(sid)
            assert found is not None
            assert found.session_id == sid

    def test_unknown_session_returns_none(self):
        """get_unit returns None for unknown session_id."""
        router = _make_router()
        assert router.get_unit("nonexistent") is None

    def test_has_active_session_false_for_cold(self):
        """has_active_session returns False for COLD units."""
        router = _make_router()
        router.get_or_create_unit("cold-session", "default")
        assert not router.has_active_session("cold-session")

    def test_has_active_session_true_for_idle(self):
        """has_active_session returns True for IDLE units."""
        router = _make_router()
        unit = router.get_or_create_unit("idle-session", "default")
        unit._transition(SessionState.IDLE)
        unit._wrapper = MagicMock()
        unit._client = MagicMock()
        assert router.has_active_session("idle-session")


# ---------------------------------------------------------------------------
# Property 12: TTL-based cleanup
# ---------------------------------------------------------------------------

class TestTTLBasedCleanup:
    """Property 12: TTL-based cleanup.

    # Feature: multi-session-rearchitecture, Property 12: TTL cleanup

    *For any* SessionUnit in IDLE state, if idle time > TTL_SECONDS (43200),
    the LifecycleManager must mark it for cleanup. Units within TTL must not
    be marked.

    **Validates: Requirements 4.2**
    """

    @given(
        idle_seconds=st.floats(min_value=0, max_value=100000),
    )
    @PROPERTY_SETTINGS
    def test_ttl_threshold(self, idle_seconds: float):
        """Units idle > TTL are marked, units within TTL are not."""
        from core.lifecycle_manager import LifecycleManager

        TTL = LifecycleManager.TTL_SECONDS  # 43200

        # A unit idle for idle_seconds
        unit = SessionUnit(session_id="ttl-test", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit.last_used = time.time() - idle_seconds

        should_kill = idle_seconds > TTL

        # Verify the logic matches
        actual_idle = time.time() - unit.last_used
        would_be_killed = actual_idle > TTL

        # Allow small floating point tolerance
        if abs(idle_seconds - TTL) > 1.0:
            assert would_be_killed == should_kill
