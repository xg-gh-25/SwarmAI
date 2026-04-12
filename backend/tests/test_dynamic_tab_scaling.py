"""Property-based and unit tests for dynamic tab scaling.

Tests the ``ResourceMonitor.compute_max_tabs()`` method from
``core/resource_monitor.py`` using Hypothesis-generated inputs to verify
formula correctness against a reference implementation, plus boundary
value unit tests from the design doc formula reference table.

Also tests ``SessionRouter._acquire_slot()`` respects the dynamic limit
from ``compute_max_tabs()`` (Property 2), and verifies API-method
consistency between the ``GET /api/system/max-tabs`` endpoint and
``compute_max_tabs()`` (Property 4).

# Feature: dynamic-tab-scaling
"""
from __future__ import annotations

import asyncio
import math
from unittest.mock import patch, PropertyMock, MagicMock, AsyncMock

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.resource_monitor import ResourceMonitor, SystemMemory, SpawnBudget
from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit
from tests.helpers import PROPERTY_SETTINGS






def _reference_formula(available_mb: float) -> int:
    """Reference implementation of the dynamic tab limit formula.

    Mirrors ``ResourceMonitor.compute_max_tabs()``:
    headroom = total_mb * 0.90 - used_mb
    raw = floor(headroom / cost)
    result = max(2, min(raw, 4))

    Cost: 1500MB per session (actual CLI tree RSS from lifecycle logs).
    Ceiling: 4 (3 chat + 1 channel). Dynamic formula auto-gates on smaller machines.
    _make_system_memory sets total=16GB, so headroom = 16384*0.90 - (16384 - available_mb).
    Simplified: headroom = available_mb - 16384*0.10 = available_mb - 1638.4
    """
    headroom_mb = available_mb - 16384 * 0.10  # 16GB * 10% overhead
    raw = int(headroom_mb / 1500)
    return max(2, min(raw, 4))


def _make_system_memory(available_mb: float) -> SystemMemory:
    """Create a SystemMemory snapshot with the given available MB."""
    available_bytes = int(available_mb * 1024 * 1024)
    total_bytes = 16 * 1024**3  # 16 GB total (arbitrary)
    used_bytes = total_bytes - available_bytes
    percent = (used_bytes / total_bytes) * 100 if total_bytes else 0.0
    return SystemMemory(
        total=total_bytes,
        available=available_bytes,
        used=used_bytes,
        percent_used=round(percent, 1),
    )


# ---------------------------------------------------------------------------
# Property 1: Formula correctness (model-based)
# ---------------------------------------------------------------------------

class TestComputeMaxTabsFormulaCorrectness:
    """Property 1: Formula correctness (model-based).

    # Feature: dynamic-tab-scaling, Property 1: Formula correctness

    *For any* non-negative available RAM value in megabytes,
    ``compute_max_tabs()`` should return the same integer as the reference
    formula ``max(2, min(floor(headroom_to_90pct / 500), 4))``.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.6**
    """

    @given(
        available_mb=st.floats(min_value=0, max_value=65536),
    )
    @PROPERTY_SETTINGS
    def test_compute_max_tabs_matches_formula(self, available_mb: float):
        """compute_max_tabs() matches the reference formula for all inputs."""
        monitor = ResourceMonitor()
        mock_mem = _make_system_memory(available_mb)

        with patch.object(monitor, "system_memory", return_value=mock_mem):
            actual = monitor.compute_max_tabs()

        expected = _reference_formula(available_mb)
        assert actual == expected, (
            f"available_mb={available_mb}: "
            f"compute_max_tabs()={actual}, reference={expected}"
        )


# ---------------------------------------------------------------------------
# Boundary value unit tests (from design doc formula reference table)
# ---------------------------------------------------------------------------

class TestComputeMaxTabsBoundaryValues:
    """Boundary value unit tests for compute_max_tabs().

    Verifies exact outputs at the values from the design doc formula
    reference table: 512, 1024, 1524, 1525, 1600, 2024, 2524, 3024,
    8192, 16384 MB.

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4**
    """

    @pytest.mark.parametrize(
        "available_mb, expected_tabs",
        [
            # headroom = available_mb - 16384 * 0.10 = available_mb - 1638.4
            # raw = floor(headroom / 1500), result = max(2, min(raw, 4))
            (512, 2),      # headroom=-1126.4 → raw=-1 → max(2,...)=2
            (1024, 2),     # headroom=-614.4 → raw=-1 → 2
            (1639, 2),     # headroom=0.6 → raw=0 → 2
            (3139, 2),     # headroom=1500.6 → raw=1 → 2
            (4639, 2),     # headroom=3000.6 → raw=2 → 2
            (6139, 3),     # headroom=4500.6 → raw=3 → 3
            (7639, 4),     # headroom=6000.6 → raw=4 → 4
            (8192, 4),     # headroom=6553.6 → raw=4 → 4
            (16384, 4),    # headroom=14745.6 → raw=9 → min(...,4)=4
        ],
        ids=[
            "512MB→2", "1024MB→2", "1639MB→2",
            "3139MB→2", "4639MB→2", "6139MB→3", "7639MB→4", "8192MB→4", "16384MB→4",
        ],
    )
    def test_compute_max_tabs_boundary_values(
        self, available_mb: float, expected_tabs: int
    ):
        """Verify exact outputs at design doc reference table values."""
        monitor = ResourceMonitor()
        mock_mem = _make_system_memory(available_mb)

        with patch.object(monitor, "system_memory", return_value=mock_mem):
            actual = monitor.compute_max_tabs()

        assert actual == expected_tabs, (
            f"available_mb={available_mb}: "
            f"compute_max_tabs()={actual}, expected={expected_tabs}"
        )


# ---------------------------------------------------------------------------
# Pessimistic fallback test
# ---------------------------------------------------------------------------

class TestComputeMaxTabsPessimisticFallback:
    """Test pessimistic fallback when system_memory() fails.

    When system_memory() fails, it returns a pessimistic fallback with
    total=16GB, used=14.4GB. Headroom = 16384*0.90 - 14745.6 = 0.0MB.
    raw = floor(0.0/1500) = 0. Result = max(2, 0) = 2.

    Even under pessimistic fallback, min_tabs=2 guarantees 1 chat + 1 channel.

    **Validates: Requirement 1.6**
    """

    def test_compute_max_tabs_pessimistic_fallback(self):
        """system_memory() failure → compute_max_tabs() = 2 (min guarantee)."""
        monitor = ResourceMonitor()

        # Force system_memory() to raise, triggering the pessimistic fallback
        with patch.object(
            monitor, "_read_system_memory", side_effect=RuntimeError("simulated failure")
        ):
            # Invalidate cache so it actually calls _read_system_memory
            monitor.invalidate_cache()
            result = monitor.compute_max_tabs()

        assert result == 2, (
            f"Expected 2 from pessimistic fallback (min guarantee: 1 chat + 1 channel), got {result}"
        )


# ---------------------------------------------------------------------------
# Property 2: Router respects dynamic limit
# ---------------------------------------------------------------------------

class TestRouterRespectsDynamicLimit:
    """Property 2: Router respects dynamic limit.

    # Feature: dynamic-tab-scaling, Property 2: Router respects dynamic limit

    *For any* value N returned by ``compute_max_tabs()`` and any
    ``alive_count`` < chat_max (= N - 1), ``_acquire_chat_slot()`` should
    grant the slot without queueing (assuming ``spawn_budget().can_spawn``
    is true).  Conversely, for any ``alive_count`` >= chat_max with no
    IDLE sessions to evict, it should queue and timeout.

    **Validates: Requirements 2.1, 2.3**
    """

    @given(
        max_tabs=st.integers(min_value=2, max_value=4),
        alive_count=st.integers(min_value=0, max_value=6),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_acquire_slot_respects_dynamic_limit(
        self, max_tabs: int, alive_count: int,
    ):
        """_acquire_slot() grants/denies based on chat_max (max_tabs - 1) vs alive_count."""
        # Build a SessionRouter with a mocked PromptBuilder
        mock_prompt_builder = MagicMock()
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        # Create alive units (STREAMING state — protected, not evictable)
        for i in range(alive_count):
            unit = SessionUnit(
                session_id=f"alive-{i}",
                agent_id="test-agent",
                on_state_change=router._on_unit_state_change,
            )
            # Manually set state to STREAMING (protected, alive, not IDLE)
            # Use object.__setattr__ to bypass enum validation in _transition
            unit.state = SessionState.STREAMING
            router._units[f"alive-{i}"] = unit

        # Create the requesting unit (COLD — not alive, needs a slot)
        requesting_unit = SessionUnit(
            session_id="requesting",
            agent_id="test-agent",
            on_state_change=router._on_unit_state_change,
        )
        router._units["requesting"] = requesting_unit

        # Mock resource_monitor.compute_max_tabs() and spawn_budget()
        mock_budget = SpawnBudget(
            can_spawn=True,
            reason="ok",
            available_mb=8000.0,
            estimated_cost_mb=500.0,
        )

        with patch(
            "core.resource_monitor.resource_monitor"
        ) as mock_rm:
            mock_rm.compute_max_tabs.return_value = max_tabs
            mock_rm.spawn_budget.return_value = mock_budget

            # Chat slots = max_tabs - 1 (1 reserved for channel)
            chat_max = max_tabs - 1

            if alive_count < chat_max:
                # Slot should be granted immediately
                result = await router._acquire_slot(requesting_unit)
                assert result == "ready", (
                    f"Expected 'ready' when alive_count={alive_count} < "
                    f"chat_max={chat_max} (max_tabs={max_tabs}), got '{result}'"
                )
            else:
                # All chat slots occupied by protected (STREAMING) units,
                # no IDLE units to evict → should queue and timeout.
                # Use a very short timeout to avoid slow tests.
                original_timeout = router.QUEUE_TIMEOUT
                router.QUEUE_TIMEOUT = 0.01  # 10ms timeout

                result = await router._acquire_slot(requesting_unit)
                assert result == "timeout", (
                    f"Expected 'timeout' when alive_count={alive_count} >= "
                    f"chat_max={chat_max} (max_tabs={max_tabs}) with no IDLE units, got '{result}'"
                )

                router.QUEUE_TIMEOUT = original_timeout


# ---------------------------------------------------------------------------
# Property 3: No eviction on budget shrinkage
# ---------------------------------------------------------------------------

class TestNoEvictionOnBudgetShrinkage:
    """Property 3: No eviction on budget shrinkage.

    # Feature: dynamic-tab-scaling, Property 3: No eviction on budget shrinkage

    *For any* set of alive sessions in STREAMING or WAITING_INPUT state,
    decreasing the value returned by ``compute_max_tabs()`` should never
    cause any of those sessions to be killed or evicted.  The eviction
    path should only trigger when a *new* session requests a slot and an
    IDLE session exists.

    **Validates: Requirements 2.3, 7.1, 7.3**
    """

    @given(
        session_states=st.lists(
            st.sampled_from([SessionState.STREAMING, SessionState.WAITING_INPUT]),
            min_size=1,
            max_size=4,
        ),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_no_eviction_on_budget_shrinkage(
        self, session_states: list[SessionState],
    ):
        """Decreasing max_tabs never kills protected (STREAMING/WAITING_INPUT) sessions.

        Strategy:
        1. Create N sessions (1-4) in random protected states.
        2. Set initial max_tabs = N (all slots used).
        3. Decrease max_tabs to a lower value (simulate budget shrinkage).
        4. Call _evict_idle() — should return False (nothing to evict).
        5. Verify all sessions remain in their original state — no kills.
        """
        session_count = len(session_states)

        # Build a SessionRouter with a mocked PromptBuilder
        mock_prompt_builder = MagicMock()
        router = SessionRouter(prompt_builder=mock_prompt_builder)

        # Create sessions in protected states (STREAMING or WAITING_INPUT)
        original_states: dict[str, SessionState] = {}
        for i, state in enumerate(session_states):
            unit = SessionUnit(
                session_id=f"protected-{i}",
                agent_id="test-agent",
                on_state_change=router._on_unit_state_change,
            )
            unit.state = state
            router._units[f"protected-{i}"] = unit
            original_states[f"protected-{i}"] = state

        # Sanity: all sessions are alive
        assert router.alive_count == session_count

        # Simulate budget shrinkage: max_tabs drops below session_count.
        # The new max_tabs is in [1, session_count-1] when session_count > 1,
        # or stays at 1 when session_count == 1 (shrinkage to same value).
        shrunk_max_tabs = max(2, session_count - 1)

        with patch(
            "core.resource_monitor.resource_monitor"
        ) as mock_rm:
            mock_rm.compute_max_tabs.return_value = shrunk_max_tabs

            # Create a dummy exclude unit (not in the router's units)
            exclude_unit = SessionUnit(
                session_id="exclude-dummy",
                agent_id="test-agent",
                on_state_change=router._on_unit_state_change,
            )

            # _evict_idle should find nothing to evict — all are protected
            evicted = await router._evict_idle(exclude=exclude_unit)
            assert evicted is False, (
                f"_evict_idle() returned True with {session_count} protected "
                f"sessions and shrunk max_tabs={shrunk_max_tabs}"
            )

        # Verify all sessions remain in their original state — no kills
        for sid, expected_state in original_states.items():
            unit = router._units[sid]
            assert unit.state == expected_state, (
                f"Session {sid} changed state from {expected_state} to "
                f"{unit.state} after budget shrinkage"
            )
            assert unit.is_alive, (
                f"Session {sid} is no longer alive after budget shrinkage"
            )


# ---------------------------------------------------------------------------
# Property 4: API-method consistency
# ---------------------------------------------------------------------------

class TestAPIMethodConsistency:
    """Property 4: API-method consistency.

    # Feature: dynamic-tab-scaling, Property 4: API-method consistency

    *For any* system memory state, the ``max_tabs`` field returned by
    ``GET /api/system/max-tabs`` should equal the value returned by
    ``ResourceMonitor.compute_max_tabs()`` when called with the same
    memory snapshot.  Similarly, ``memory_pressure`` should match
    ``SystemMemory.pressure_level``.

    **Validates: Requirements 3.1**
    """

    @given(
        available_mb=st.floats(min_value=0, max_value=65536),
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_endpoint_max_tabs_equals_compute_max_tabs(
        self, available_mb: float,
    ):
        """get_max_tabs() endpoint returns same max_tabs as compute_max_tabs()."""
        from routers.system import get_max_tabs

        mock_mem = _make_system_memory(available_mb)
        expected_max_tabs = _reference_formula(available_mb)
        expected_pressure = mock_mem.pressure_level

        with patch(
            "core.resource_monitor.resource_monitor"
        ) as mock_rm:
            mock_rm.invalidate_cache = MagicMock()
            mock_rm.system_memory.return_value = mock_mem
            mock_rm.compute_max_tabs.return_value = expected_max_tabs

            response = await get_max_tabs()

        assert response.max_tabs == expected_max_tabs, (
            f"available_mb={available_mb}: endpoint max_tabs={response.max_tabs}, "
            f"expected={expected_max_tabs}"
        )
        expected_chat_max = max(1, expected_max_tabs - 1)
        assert response.chat_max == expected_chat_max, (
            f"available_mb={available_mb}: endpoint chat_max={response.chat_max}, "
            f"expected={expected_chat_max}"
        )
        assert response.memory_pressure == expected_pressure, (
            f"available_mb={available_mb}: endpoint memory_pressure="
            f"{response.memory_pressure}, expected={expected_pressure}"
        )


# ---------------------------------------------------------------------------
# Unit test: Endpoint fallback on system_memory() failure
# ---------------------------------------------------------------------------

class TestMaxTabsEndpointFallback:
    """Unit test for endpoint fallback when system_memory() raises.

    When ``resource_monitor.system_memory()`` raises an exception inside
    the ``get_max_tabs()`` endpoint handler, the endpoint should return
    ``max_tabs=1`` and ``memory_pressure="critical"`` as a safe fallback.

    **Validates: Requirements 3.4**
    """

    @pytest.mark.asyncio
    async def test_endpoint_returns_fallback_on_failure(self):
        """get_max_tabs() returns max_tabs=1, memory_pressure='critical' on error."""
        from routers.system import get_max_tabs

        with patch(
            "core.resource_monitor.resource_monitor"
        ) as mock_rm:
            mock_rm.invalidate_cache = MagicMock()
            mock_rm.system_memory.side_effect = RuntimeError("simulated failure")

            response = await get_max_tabs()

        assert response.max_tabs == 1, (
            f"Expected max_tabs=1 on failure, got {response.max_tabs}"
        )
        assert response.chat_max == 1, (
            f"Expected chat_max=1 on failure, got {response.chat_max}"
        )
        assert response.memory_pressure == "critical", (
            f"Expected memory_pressure='critical' on failure, "
            f"got {response.memory_pressure}"
        )
