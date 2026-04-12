"""Tests for RSS-based proactive restart-with-resume (方案 D).

Covers 5 acceptance criteria:
- AC1: IDLE session tree RSS > 1.8GB triggers compact → kill
- AC2: _ensure_spawned injects --resume when _sdk_session_id exists in COLD state
- AC3: 3-minute cooldown between proactive restarts per session
- AC4: No immediate respawn — lazy restart on next send()
- AC5: Zero regressions (covered by full suite, not this file)

Testing methodology: unit tests with mocked psutil/resource_monitor,
verifying state transitions and method calls without real subprocesses.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_unit(session_id: str = "test-session") -> SessionUnit:
    """Build a SessionUnit in COLD state with minimal mocks."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    return unit


def _set_idle_with_pid(unit: SessionUnit, pid: int = 12345) -> None:
    """Put unit into IDLE state with a fake PID and client."""
    unit.state = SessionState.IDLE
    unit._client = MagicMock()
    unit._wrapper = MagicMock()
    # Simulate PID via wrapper
    unit._wrapper.process = MagicMock()
    unit._wrapper.process.pid = pid


# ── AC1: IDLE + RSS > 1.2GB → compact → kill ───────────────────────────


class TestAC1_ProactiveRestart:
    """When IDLE session tree RSS exceeds threshold, compact then kill."""

    @pytest.mark.asyncio
    async def test_rss_above_threshold_triggers_compact_and_kill(self):
        """AC1: RSS > 1.8GB in IDLE state → compact() then kill()."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        unit.compact = AsyncMock(return_value={"success": True})
        unit.kill = AsyncMock()

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            await unit._check_rss_and_proactive_restart()

        unit.compact.assert_awaited_once()
        unit.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rss_below_threshold_does_nothing(self):
        """RSS below 1.8GB → no action."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        unit.compact = AsyncMock()
        unit.kill = AsyncMock()

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=1_000_000_000,  # 1.0GB < 1.8GB
        ):
            await unit._check_rss_and_proactive_restart()

        unit.compact.assert_not_awaited()
        unit.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_failure_still_kills(self):
        """Even if compact() fails, kill() must proceed."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        unit.compact = AsyncMock(return_value={"success": False, "message": "error"})
        unit.kill = AsyncMock()

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            await unit._check_rss_and_proactive_restart()

        unit.compact.assert_awaited_once()
        unit.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_pid_skips_check(self):
        """If unit has no PID (already COLD-like), skip RSS check."""
        unit = _make_unit()
        unit.state = SessionState.IDLE
        # No _wrapper → pid is None

        unit.compact = AsyncMock()
        unit.kill = AsyncMock()

        await unit._check_rss_and_proactive_restart()

        unit.compact.assert_not_awaited()
        unit.kill.assert_not_awaited()


# ── AC2: _ensure_spawned + _sdk_session_id → resume ────────────────────


class TestAC2_EnsureSpawnedResume:
    """COLD + _sdk_session_id → _spawn called with resume option."""

    @pytest.mark.asyncio
    async def test_cold_with_sdk_session_id_injects_resume(self):
        """AC2: _ensure_spawned adds resume when _sdk_session_id exists."""
        from claude_agent_sdk import ClaudeAgentOptions

        unit = _make_unit()
        unit._sdk_session_id = "previous-sdk-session"

        original_options = ClaudeAgentOptions(
            system_prompt="test prompt",
            max_turns=10,
        )

        # Mock _spawn to succeed and capture the options it receives
        spawned_options = []

        async def capture_spawn(opts, config=None):
            spawned_options.append(opts)
            unit.state = SessionState.IDLE
            unit._client = MagicMock()

        unit._spawn = capture_spawn

        # Drain the async generator
        events = []
        async for event in unit._ensure_spawned(original_options, None):
            events.append(event)

        assert len(spawned_options) == 1
        # The options passed to _spawn should have resume set
        assert spawned_options[0].resume == "previous-sdk-session"
        # Original system prompt preserved
        assert spawned_options[0].system_prompt == "test prompt"

    @pytest.mark.asyncio
    async def test_cold_without_sdk_session_id_no_resume(self):
        """No _sdk_session_id → spawn with original options (no resume)."""
        from claude_agent_sdk import ClaudeAgentOptions

        unit = _make_unit()
        unit._sdk_session_id = None

        original_options = ClaudeAgentOptions(
            system_prompt="test prompt",
            max_turns=10,
        )

        spawned_options = []

        async def capture_spawn(opts, config=None):
            spawned_options.append(opts)
            unit.state = SessionState.IDLE
            unit._client = MagicMock()

        unit._spawn = capture_spawn

        events = []
        async for event in unit._ensure_spawned(original_options, None):
            events.append(event)

        assert len(spawned_options) == 1
        # Original options passed through unchanged (no copy needed)
        assert spawned_options[0] is original_options
        assert spawned_options[0].resume is None


# ── AC3: Cooldown between proactive restarts ───────────────────────────


class TestAC3_Cooldown:
    """3-minute cooldown prevents repeated proactive restarts."""

    @pytest.mark.asyncio
    async def test_cooldown_prevents_immediate_second_restart(self):
        """AC3: After a proactive restart, second attempt within 3min is skipped."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        unit.compact = AsyncMock(return_value={"success": True})
        unit.kill = AsyncMock()

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            # First call — should trigger
            await unit._check_rss_and_proactive_restart()
            assert unit.compact.await_count == 1
            assert unit.kill.await_count == 1

            # Reset mocks but simulate still being IDLE (re-spawned)
            unit.compact.reset_mock()
            unit.kill.reset_mock()
            _set_idle_with_pid(unit)

            # Second call immediately — should be blocked by cooldown
            await unit._check_rss_and_proactive_restart()
            unit.compact.assert_not_awaited()
            unit.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cooldown_expires_allows_restart(self):
        """AC3: After cooldown period expires, proactive restart fires again."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        unit.compact = AsyncMock(return_value={"success": True})
        unit.kill = AsyncMock()

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            # First call
            await unit._check_rss_and_proactive_restart()
            assert unit.kill.await_count == 1

            # Simulate cooldown expiry
            unit._last_proactive_restart = time.monotonic() - 200  # 200s > 180s

            unit.compact.reset_mock()
            unit.kill.reset_mock()
            _set_idle_with_pid(unit)

            # Should trigger again
            await unit._check_rss_and_proactive_restart()
            unit.compact.assert_awaited_once()
            unit.kill.assert_awaited_once()


# ── AC4: No immediate respawn — lazy restart ───────────────────────────


class TestAC4_LazyRestart:
    """Proactive kill leaves unit in COLD — no immediate respawn."""

    @pytest.mark.asyncio
    async def test_proactive_kill_leaves_cold_state(self):
        """AC4: After proactive restart, state is COLD (not respawned)."""
        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-123"

        # Make compact and kill actually transition state
        async def mock_compact(instructions=None):
            return {"success": True}

        async def mock_kill():
            unit._cleanup_internal()
            unit.state = SessionState.COLD

        unit.compact = mock_compact
        unit.kill = mock_kill

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            await unit._check_rss_and_proactive_restart()

        assert unit.state == SessionState.COLD
        # sdk_session_id is preserved (for resume on next send)
        assert unit._sdk_session_id == "sdk-session-123"


# ── Lifecycle Manager: 60s fallback ────────────────────────────────────


class TestLifecycleProactiveRestart:
    """LifecycleManager's 60s fallback RSS check for IDLE sessions."""

    @pytest.mark.asyncio
    async def test_lifecycle_checks_idle_sessions_rss(self):
        """60s maintenance loop checks IDLE sessions and triggers restart."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-456"
        unit.compact = AsyncMock(return_value={"success": True})
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            await mgr._proactive_rss_restart()

        unit.compact.assert_awaited_once()
        unit.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lifecycle_skips_streaming_sessions(self):
        """Only IDLE sessions are candidates — STREAMING is untouched."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._client = MagicMock()
        unit.compact = AsyncMock()
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        await mgr._proactive_rss_restart()

        unit.compact.assert_not_awaited()
        unit.kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lifecycle_respects_cooldown(self):
        """Lifecycle manager respects per-unit cooldown."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit()
        _set_idle_with_pid(unit)
        unit._sdk_session_id = "sdk-session-789"
        unit._last_proactive_restart = time.monotonic()  # Just restarted
        unit.compact = AsyncMock()
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        with patch(
            "core.resource_monitor.resource_monitor.process_tree_rss",
            return_value=2_000_000_000,  # 2.0GB > 1.8GB
        ):
            await mgr._proactive_rss_restart()

        unit.compact.assert_not_awaited()
        unit.kill.assert_not_awaited()
