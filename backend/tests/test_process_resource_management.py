"""Bug condition exploration tests for process resource management.

These tests encode the EXPECTED (correct) behavior for each bug condition
identified in the bugfix spec. They are designed to FAIL on the current
unfixed code, confirming that the bugs exist.

Bug conditions tested:
- C1: vm_stat memory inflation (inactive pages included in available)
- C1b: compute_max_tabs accuracy (inflated available → wrong tab count)
- C2: wrapper FD leak on crash path (_crash_to_cold skips __aexit__)
- C3: slot race condition (concurrent _acquire_slot exceeds max_tabs)

Testing methodology:
- Each test mocks the minimal dependencies needed to isolate the bug
- Assertions encode the CORRECT behavior (what the fix should produce)
- Tests FAIL on unfixed code → confirms the bug exists
- Tests PASS after fix → confirms the bug is resolved

Validates: Requirements 1.1, 1.2, 1.4, 1.5, 1.6, 1.7
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# C1 — vm_stat memory inflation
# ---------------------------------------------------------------------------

VM_STAT_OUTPUT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12800.
Pages active:                            500000.
Pages inactive:                          576000.
Pages speculative:                         3200.
Pages throttled:                              0.
Pages wired down:                        200000.
Pages purgeable:                          10000.
Pages stored in compressor:              100000.
"""

TOTAL_MEM = 36 * 1024**3  # 36 GB


def _make_subprocess_side_effect():
    """Return a side_effect function that handles both vm_stat and sysctl calls."""
    def side_effect(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        if cmd == ["vm_stat"]:
            mock_result.stdout = VM_STAT_OUTPUT
        elif cmd == ["sysctl", "-n", "hw.memsize"]:
            mock_result.stdout = str(TOTAL_MEM)
        else:
            mock_result.stdout = ""
        return mock_result
    return side_effect


class TestC1VmStatMemoryInflation:
    """C1: vm_stat available memory should exclude inactive pages.

    Bug: _read_memory_macos_fallback() includes inactive pages in available,
    inflating the reading by ~9GB. Correct behavior: available = free + speculative.

    Validates: Requirements 1.1, 2.1
    """

    @patch("core.resource_monitor._HAS_PSUTIL", False)
    @patch("core.resource_monitor.subprocess.run")
    def test_available_memory_excludes_inactive(self, mock_run):
        """Available memory should be ~250MB (free + speculative), not ~9250MB."""
        mock_run.side_effect = _make_subprocess_side_effect()

        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        mem = monitor._read_memory_macos_fallback()

        page_size = 16384
        expected_free = 12800 * page_size       # 200 MB
        expected_spec = 3200 * page_size        # 50 MB
        expected_available = expected_free + expected_spec  # 250 MB

        # The correct available should be ~250MB (free + speculative only)
        # On unfixed code, this will be ~9250MB (includes inactive)
        assert abs(mem.available - expected_available) < 10 * 1024 * 1024, (
            f"Available memory should be ~{expected_available / (1024**2):.0f}MB "
            f"(free + speculative), got {mem.available / (1024**2):.0f}MB. "
            f"Bug: inactive pages ({576000 * page_size / (1024**2):.0f}MB) "
            f"are incorrectly included."
        )


# ---------------------------------------------------------------------------
# C1b — compute_max_tabs accuracy
# ---------------------------------------------------------------------------

class TestC1bComputeMaxTabsAccuracy:
    """C1b: compute_max_tabs should return 1 with ~250MB available, not 4.

    Bug: Inflated available memory (9250MB) causes compute_max_tabs to return 4.
    Correct: With 250MB available, formula gives max(1, min(floor((250-1024)/500), 4)) = 1.

    Validates: Requirements 1.2, 2.2
    """

    @patch("core.resource_monitor._HAS_PSUTIL", False)
    @patch("core.resource_monitor.subprocess.run")
    def test_max_tabs_with_accurate_memory(self, mock_run):
        """compute_max_tabs should return 1 when real available is ~250MB."""
        mock_run.side_effect = _make_subprocess_side_effect()

        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        max_tabs = monitor.compute_max_tabs()

        # With 250MB available: max(1, min(floor((250 - 1024) / 500), 4)) = 1
        # On unfixed code: max(1, min(floor((9250 - 1024) / 500), 4)) = 4
        assert max_tabs == 1, (
            f"compute_max_tabs() should return 1 with ~250MB available, "
            f"got {max_tabs}. Bug: inflated available memory from inactive pages."
        )


# ---------------------------------------------------------------------------
# C2 — wrapper FD leak on crash path
# ---------------------------------------------------------------------------

class TestC2WrapperFDLeakOnCrashPath:
    """C2: _crash_to_cold_async should call wrapper.__aexit__() before clearing it.

    After fix: _crash_to_cold_async() calls _force_kill() which calls
    wrapper.__aexit__(), properly closing file descriptors.

    Validates: Requirements 1.4, 1.5, 2.4, 2.5
    """

    @pytest.mark.asyncio
    async def test_crash_to_cold_async_calls_wrapper_aexit(self):
        """wrapper.__aexit__() should be called during _crash_to_cold_async()."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-c2", agent_id="default")
        unit.state = SessionState.STREAMING

        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=None)
        mock_wrapper.pid = 12345
        unit._wrapper = mock_wrapper

        # Mock os.getpgid to avoid real process operations
        with patch("core.session_unit.os.getpgid", side_effect=ProcessLookupError):
            await unit._crash_to_cold_async(clear_identity=False)

        assert mock_wrapper.__aexit__.call_count > 0, (
            "wrapper.__aexit__() was NOT called during _crash_to_cold_async(). "
            "The async version should call _force_kill() which calls __aexit__()."
        )
        assert unit.state == SessionState.COLD


# ---------------------------------------------------------------------------
# C3 — slot race condition
# ---------------------------------------------------------------------------

class TestC3SlotRaceCondition:
    """C3: _acquire_slot() uses asyncio.Lock to prevent concurrent over-allocation.

    The fix adds an asyncio.Lock that serializes the check-then-act section
    of _acquire_slot(). This test verifies:
    1. The lock attribute exists on SessionRouter (asyncio.Lock)
    2. When all slots are occupied by protected (STREAMING) units, the 3rd
       request correctly times out instead of bypassing the limit

    Validates: Requirements 1.6, 1.7, 2.6, 2.7
    """

    def test_slot_lock_exists(self):
        """SessionRouter should have an asyncio.Lock for slot acquisition."""
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock())

        assert hasattr(router, "_slot_lock"), (
            "SessionRouter should have _slot_lock attribute. "
            "Bug: no mutual exclusion in _acquire_slot()."
        )
        assert isinstance(router._slot_lock, asyncio.Lock), (
            "_slot_lock should be an asyncio.Lock instance"
        )

    @pytest.mark.asyncio
    async def test_acquire_slot_respects_max_tabs_with_protected_units(self):
        """When max_tabs slots are occupied by STREAMING units, new requests timeout.

        This verifies the lock + re-check loop works: the 3rd request cannot
        get a slot because all slots are occupied by protected (STREAMING)
        units that cannot be evicted.
        """
        from core.session_unit import SessionUnit, SessionState
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock())
        router.QUEUE_TIMEOUT = 0.3  # short timeout for test

        max_tabs_value = 2

        # Create 2 STREAMING units (protected, can't evict)
        for i in range(2):
            u = SessionUnit(
                session_id=f"test-c3-streaming-{i}",
                agent_id="default",
                on_state_change=router._on_unit_state_change,
            )
            u.state = SessionState.IDLE
            u._transition(SessionState.STREAMING)
            router._units[u.session_id] = u

        # Create a COLD unit that wants a slot
        cold_unit = SessionUnit(
            session_id="test-c3-cold",
            agent_id="default",
            on_state_change=router._on_unit_state_change,
        )
        router._units[cold_unit.session_id] = cold_unit

        mock_budget = MagicMock()
        mock_budget.can_spawn = True
        mock_budget.reason = "ok"
        mock_budget.available_mb = 8000.0
        mock_budget.estimated_cost_mb = 500.0
        mock_budget.headroom_mb = 512.0

        with patch(
            "core.resource_monitor.resource_monitor"
        ) as mock_rm:
            mock_rm.compute_max_tabs.return_value = max_tabs_value
            mock_rm.spawn_budget.return_value = mock_budget
            mock_rm.invalidate_cache = MagicMock()

            result = await router._acquire_slot(cold_unit)

        # The 3rd request should timeout since all slots are protected
        assert result == "timeout", (
            f"Expected 'timeout' when all {max_tabs_value} slots are occupied "
            f"by STREAMING units, got '{result}'. The lock should prevent "
            f"over-allocation beyond max_tabs."
        )


# ===========================================================================
# PRESERVATION PROPERTY TESTS (Task 2)
#
# These tests capture EXISTING correct behavior that must remain unchanged
# after the bugfixes. They should PASS on the current unfixed code.
#
# Validates: Requirements 3.1–3.14
# ===========================================================================

import os
import signal
from unittest.mock import call

from hypothesis import given, strategies as st, settings


# ---------------------------------------------------------------------------
# P1 — psutil path preservation (Req 3.1)
# ---------------------------------------------------------------------------

class TestP1PsutilPathPreservation:
    """P1: psutil code path returns psutil.virtual_memory() values exactly.

    Validates: Requirements 3.1
    """

    @patch("core.resource_monitor._HAS_PSUTIL", True)
    @patch("core.resource_monitor.psutil")
    def test_psutil_path_returns_exact_values(self, mock_psutil):
        """system_memory() should return psutil values when psutil is available."""
        mock_vm = MagicMock()
        mock_vm.total = 34_359_738_368       # 32 GB
        mock_vm.available = 8_589_934_592    # 8 GB
        mock_vm.used = 25_769_803_776        # 24 GB
        mock_vm.percent = 75.0
        mock_psutil.virtual_memory.return_value = mock_vm

        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        mem = monitor.system_memory()

        assert mem.total == 34_359_738_368
        assert mem.available == 8_589_934_592
        assert mem.used == 25_769_803_776
        assert mem.percent_used == 75.0


# ---------------------------------------------------------------------------
# P2 — fallback failure preservation (Req 3.2)
# ---------------------------------------------------------------------------

class TestP2FallbackFailurePreservation:
    """P2: When vm_stat fails, pessimistic fallback is returned.

    Validates: Requirements 3.2
    """

    @patch("core.resource_monitor._HAS_PSUTIL", False)
    @patch("core.resource_monitor.subprocess.run")
    def test_fallback_returns_pessimistic_values(self, mock_run):
        """system_memory() should return 16GB/1600MB/90% on vm_stat failure."""
        mock_run.side_effect = Exception("vm_stat not found")

        from core.resource_monitor import ResourceMonitor
        monitor = ResourceMonitor()
        mem = monitor.system_memory()

        assert mem.total == 16 * 1024**3, f"Expected 16GB total, got {mem.total}"
        assert mem.available == 1600 * 1024**2, f"Expected 1600MB available, got {mem.available}"
        assert mem.percent_used == 90.0, f"Expected 90.0% used, got {mem.percent_used}"


# ---------------------------------------------------------------------------
# P3 — compute_max_tabs formula preservation (Req 3.3)
# ---------------------------------------------------------------------------

class TestP3ComputeMaxTabsFormulaPreservation:
    """P3: compute_max_tabs formula is max(1, min(floor((avail-1024)/500), 4)).

    Uses Hypothesis to verify the formula across random available_mb values.

    **Validates: Requirements 3.3**
    """

    @given(available_mb=st.floats(min_value=0, max_value=100000, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50, deadline=None)
    def test_formula_matches_expected(self, available_mb):
        """compute_max_tabs output matches the formula exactly for any available_mb."""
        from core.resource_monitor import ResourceMonitor, SystemMemory

        expected = max(1, min(int((available_mb - 1024) // 500), 4))

        monitor = ResourceMonitor()
        # Inject a fake cached memory so system_memory() returns our value
        available_bytes = int(available_mb * 1024 * 1024)
        total_bytes = 36 * 1024**3
        used_bytes = total_bytes - available_bytes
        pct = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 90.0

        monitor._cached_memory = SystemMemory(
            total=total_bytes,
            available=available_bytes,
            used=used_bytes,
            percent_used=pct,
        )
        monitor._cache_time = __import__("time").time()  # fresh cache

        result = monitor.compute_max_tabs()
        assert result == expected, (
            f"For available_mb={available_mb:.1f}: "
            f"expected {expected}, got {result}"
        )


# ---------------------------------------------------------------------------
# P4 — explicit kill() path preservation (Req 3.4, 3.5)
# ---------------------------------------------------------------------------

class TestP4ExplicitKillPathPreservation:
    """P4: kill() calls _force_kill(), wrapper.__aexit__(), then COLD.

    Validates: Requirements 3.4, 3.5
    """

    @pytest.mark.asyncio
    async def test_kill_calls_force_kill_and_cleans_up(self):
        """kill() should call _force_kill, wrapper.__aexit__, reach COLD, preserve _sdk_session_id."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-p4", agent_id="default")
        unit.state = SessionState.IDLE
        unit._sdk_session_id = "sdk-session-123"

        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=None)
        mock_wrapper.pid = 99999
        unit._wrapper = mock_wrapper

        # Mock os.getpgid and os.kill to avoid real process operations
        with patch("core.session_unit.os.getpgid", side_effect=ProcessLookupError):
            await unit.kill()

        # Verify wrapper.__aexit__ was called (via _force_kill)
        assert mock_wrapper.__aexit__.call_count > 0, (
            "wrapper.__aexit__() should be called during kill()"
        )
        # State should be COLD
        assert unit.state == SessionState.COLD
        # _sdk_session_id should be preserved (not cleared)
        assert unit._sdk_session_id == "sdk-session-123"
        # Transient fields should be cleared
        assert unit._client is None
        assert unit._wrapper is None
        assert unit._interrupted is False
        assert unit._retry_count == 0
        assert unit._model_name is None


# ---------------------------------------------------------------------------
# P5 — alive unit fast path (Req 3.6)
# ---------------------------------------------------------------------------

class TestP5AliveUnitFastPath:
    """P5: Already-alive units bypass slot acquisition entirely.

    Validates: Requirements 3.6
    """

    @pytest.mark.asyncio
    async def test_alive_unit_returns_ready_immediately(self):
        """_acquire_slot() returns 'ready' immediately for alive units."""
        from core.session_unit import SessionUnit, SessionState
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock())
        unit = SessionUnit(
            session_id="test-p5",
            agent_id="default",
            on_state_change=router._on_unit_state_change,
        )
        # Set unit to IDLE (alive)
        unit.state = SessionState.IDLE
        router._units[unit.session_id] = unit

        result = await router._acquire_slot(unit)
        assert result == "ready", (
            f"Alive unit should get 'ready' immediately, got '{result}'"
        )


# ---------------------------------------------------------------------------
# P6 — queue timeout (Req 3.7)
# ---------------------------------------------------------------------------

class TestP6QueueTimeout:
    """P6: Queue timeout returns 'timeout' when all slots are occupied.

    Validates: Requirements 3.7
    """

    @pytest.mark.asyncio
    async def test_queue_timeout_returns_timeout(self):
        """_acquire_slot() returns 'timeout' when all slots are STREAMING."""
        from core.session_unit import SessionUnit, SessionState
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock())

        # Create 2 STREAMING units (protected, can't evict)
        for i in range(2):
            u = SessionUnit(
                session_id=f"test-p6-streaming-{i}",
                agent_id="default",
                on_state_change=router._on_unit_state_change,
            )
            u.state = SessionState.STREAMING
            router._units[u.session_id] = u

        # Create a COLD unit that wants a slot
        cold_unit = SessionUnit(
            session_id="test-p6-cold",
            agent_id="default",
            on_state_change=router._on_unit_state_change,
        )
        cold_unit.state = SessionState.COLD
        router._units[cold_unit.session_id] = cold_unit

        # Mock compute_max_tabs to return 2 (all slots occupied)
        mock_budget = MagicMock()
        mock_budget.can_spawn = True

        with patch("core.resource_monitor.resource_monitor") as mock_rm:
            mock_rm.compute_max_tabs.return_value = 2
            mock_rm.spawn_budget.return_value = mock_budget
            mock_rm.invalidate_cache = MagicMock()

            # Use a very short timeout to avoid slow test
            original_timeout = router.QUEUE_TIMEOUT
            router.QUEUE_TIMEOUT = 0.1
            try:
                result = await router._acquire_slot(cold_unit)
            finally:
                router.QUEUE_TIMEOUT = original_timeout

        assert result == "timeout", (
            f"Expected 'timeout' when all slots occupied, got '{result}'"
        )



# ---------------------------------------------------------------------------
# P7 — state change signaling (Req 3.8)
# ---------------------------------------------------------------------------

class TestP7StateChangeSignaling:
    """P7: _on_unit_state_change signals _slot_available on protected→unprotected.

    Validates: Requirements 3.8
    """

    def test_state_change_signals_slot_available(self):
        """_on_unit_state_change sets _slot_available when STREAMING→IDLE."""
        from core.session_unit import SessionState
        from core.session_router import SessionRouter

        router = SessionRouter(prompt_builder=MagicMock())
        # Clear the event first
        router._slot_available.clear()
        assert not router._slot_available.is_set()

        # Simulate STREAMING → IDLE transition
        router._on_unit_state_change("test-p7", SessionState.STREAMING, SessionState.IDLE)

        assert router._slot_available.is_set(), (
            "_slot_available should be set after STREAMING → IDLE transition"
        )


# ---------------------------------------------------------------------------
# P8 — PGID-based killpg (Req 3.9)
# ---------------------------------------------------------------------------

class TestP8PGIDBasedKillpg:
    """P8: _force_kill uses os.killpg when child has its own PGID.

    Validates: Requirements 3.9
    """

    @pytest.mark.asyncio
    async def test_killpg_used_for_different_pgid(self):
        """_force_kill() should use os.killpg() when child PGID differs from ours."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-p8", agent_id="default")
        unit.state = SessionState.IDLE

        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=None)
        mock_wrapper.pid = 12345
        unit._wrapper = mock_wrapper

        child_pgid = 12345   # child's own PGID
        my_pgid = 99999      # our PGID (different)

        with patch("core.session_unit.os.getpgid") as mock_getpgid, \
             patch("core.session_unit.os.killpg") as mock_killpg, \
             patch("core.session_unit.os.getpid", return_value=99999):
            mock_getpgid.side_effect = lambda pid: child_pgid if pid == 12345 else my_pgid
            await unit._force_kill()

        mock_killpg.assert_called_once_with(child_pgid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# P9 — error handling on dead process (Req 3.10)
# ---------------------------------------------------------------------------

class TestP9ErrorHandlingOnDeadProcess:
    """P9: _force_kill handles ProcessLookupError silently.

    Validates: Requirements 3.10
    """

    @pytest.mark.asyncio
    async def test_no_exception_on_dead_process(self):
        """_force_kill() should not propagate ProcessLookupError."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-p9", agent_id="default")
        unit.state = SessionState.IDLE

        mock_wrapper = MagicMock()
        mock_wrapper.__aexit__ = AsyncMock(return_value=None)
        mock_wrapper.pid = 12345
        unit._wrapper = mock_wrapper

        with patch("core.session_unit.os.getpgid", side_effect=ProcessLookupError), \
             patch("core.session_unit.os.kill", side_effect=ProcessLookupError):
            # Should NOT raise
            await unit._force_kill()

        # If we get here, no exception propagated — test passes


# ---------------------------------------------------------------------------
# P10 — existing reaper patterns (Req 3.11, 3.12)
# ---------------------------------------------------------------------------

class TestP10ExistingReaperPatterns:
    """P10: _reap_orphans calls correct patterns with correct require_orphaned flags.

    Validates: Requirements 3.11, 3.12
    """

    @pytest.mark.asyncio
    async def test_reaper_patterns_and_flags(self):
        """_reap_orphans calls claude without require_orphaned, others with it."""
        from core.lifecycle_manager import LifecycleManager

        mock_router = MagicMock()
        mock_router.list_units.return_value = []

        manager = LifecycleManager(router=mock_router)

        calls_log = []

        async def mock_reap_by_pattern(pattern, label, known_pids, require_orphaned=False):
            calls_log.append({
                "pattern": pattern,
                "label": label,
                "require_orphaned": require_orphaned,
            })
            return 0

        with patch.object(manager, "_reap_by_pattern", side_effect=mock_reap_by_pattern):
            await manager._reap_orphans()

        # Find the claude pattern call
        claude_calls = [c for c in calls_log if "claude" in c["pattern"]]
        assert len(claude_calls) >= 1, "Should have at least one 'claude' pattern call"
        assert claude_calls[0]["require_orphaned"] is False, (
            "claude pattern should NOT require orphaned (ppid==1)"
        )

        # Find the python main.py pattern call
        python_calls = [c for c in calls_log if "python main.py" in c["pattern"]]
        assert len(python_calls) >= 1, "Should have at least one 'python main.py' pattern call"
        assert python_calls[0]["require_orphaned"] is True, (
            "python main.py pattern should require orphaned (ppid==1)"
        )

        # Find the pytest pattern call
        pytest_calls = [c for c in calls_log if c["pattern"] == "pytest"]
        assert len(pytest_calls) >= 1, "Should have at least one 'pytest' pattern call"
        assert pytest_calls[0]["require_orphaned"] is True, (
            "pytest pattern should require orphaned (ppid==1)"
        )
