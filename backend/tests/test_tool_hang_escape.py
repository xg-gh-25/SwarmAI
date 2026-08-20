"""Tests for the tool-aware liveness tier v2 (CPU-probe) in SessionUnit.

v1 gated the warm interrupt on open-tool-use + event-silence. That was UNSOUND:
_last_event_time refreshes only per-message, so a single long tool (Bash build,
Agent sub-agent) is event-silent whether wedged OR healthy. v2 adds a SECONDARY
liveness signal — tree CPU delta — and only interrupts a tool whose entire
process tree burns ~0 CPU over a sample interval (a genuine deadlock). A busy
tool (incl. a healthy Agent sub-agent running its own CLI child) shows CPU >
epsilon and is NEVER interrupted.

Key properties:
- AC2: open tool past window AND dead tree-CPU → interrupt(autonomous=True), NOT force_kill
- AC3: live tree-CPU (busy tool) → NEVER interrupt, even past the window
- AC4: repeated wedged episodes → dedicated counter → force-kill escalation
- AC5: resource_monitor.tree_cpu_seconds classifies a real busy vs idle tree
- AC6: autonomous interrupt does NOT set _user_stopped_current_turn
- fail-safe: tree_cpu_seconds None → never interrupt
"""
from __future__ import annotations

import asyncio
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_unit(session_id: str = "test-session", pid: int | None = 12345) -> SessionUnit:
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = session_id
    unit.state = SessionState.COLD
    unit._sdk_session_id = None
    unit._client = None
    if pid is not None:
        wrapper = MagicMock()
        wrapper.pid = pid
        unit._wrapper = wrapper
    else:
        unit._wrapper = None
    unit._hooks_enqueued = False
    unit._streaming_start_time = None
    unit._last_event_time = None
    unit._peak_tree_rss_bytes = 0
    unit._last_proactive_restart = 0
    unit._pid_watchdog_task = None
    unit._last_known_context_tokens = 0
    unit._PID_WATCHDOG_INTERVAL = 0.05
    unit.last_used = time.time()
    unit.is_channel_session = False
    unit._retry_count = 0
    unit._max_retries = 3
    unit._on_state_change = None
    unit._stop_event = asyncio.Event()
    # v2 fields
    unit._open_tool_uses = {}
    unit._tool_hang_interrupted = False
    unit._tool_hang_interrupt_at = None
    unit._tool_hang_episodes = 0
    unit._consecutive_unstick_timeouts = 0
    unit._force_kill = AsyncMock()
    unit.interrupt = AsyncMock(return_value=True)
    # R3e (M4): tool-hang escalation (INTERRUPT vs force-kill) now routes through
    # the unit's RecoveryCoordinator. Give it a real one + a not-stopped turn.
    unit._user_stopped_current_turn = False
    from core.session_healing import HealingLoop, RecoveryCoordinator
    unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
    # Shorten the CPU probe sleep so tests run fast.
    unit.CPU_PROBE_INTERVAL_S = 0.02
    return unit


def _open_tool(name: str = "Bash", age: float = 0.0) -> dict:
    """Build an _open_tool_uses dict with one tool emitted `age` seconds ago."""
    return {"toolu_x": (time.time() - age, name)}


# ── AC5: the CPU-delta discriminator itself (real subprocesses) ─────────


class TestTreeCpuSeconds:
    def test_busy_tree_shows_positive_delta(self):
        from core.resource_monitor import resource_monitor
        busy = subprocess.Popen(
            ["sh", "-c", 'python3 -c "x=0\nwhile x<10**9: x+=1"']
        )
        try:
            time.sleep(0.2)
            c0 = resource_monitor.tree_cpu_seconds(busy.pid)
            time.sleep(0.6)
            c1 = resource_monitor.tree_cpu_seconds(busy.pid)
            assert c0 is not None and c1 is not None
            assert (c1 - c0) > 0.05, f"busy delta {c1 - c0} should be > epsilon"
        finally:
            busy.terminate()
            busy.wait()

    def test_idle_tree_shows_zero_delta(self):
        from core.resource_monitor import resource_monitor
        idle = subprocess.Popen(["sh", "-c", "sleep 30"])
        try:
            time.sleep(0.2)
            c0 = resource_monitor.tree_cpu_seconds(idle.pid)
            time.sleep(0.6)
            c1 = resource_monitor.tree_cpu_seconds(idle.pid)
            assert c0 is not None and c1 is not None
            assert (c1 - c0) < 0.05, f"idle delta {c1 - c0} should be < epsilon"
        finally:
            idle.terminate()
            idle.wait()

    def test_dead_pid_returns_none(self):
        from core.resource_monitor import resource_monitor
        # PID 999999 almost certainly doesn't exist.
        assert resource_monitor.tree_cpu_seconds(999999) is None


# ── AC2: wedged tool (dead CPU) → warm interrupt ────────────────────────


class TestWedgedToolEscape:
    @pytest.mark.asyncio
    async def test_dead_cpu_tool_interrupts_not_kills(self):
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)

        # CPU probe returns NO delta (same value twice) → wedged.
        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=5.0):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_awaited()
        # autonomous=True must be passed
        assert unit.interrupt.await_args.kwargs.get("autonomous") is True
        unit._force_kill.assert_not_called()
        assert unit._tool_hang_episodes == 1

    @pytest.mark.asyncio
    async def test_grace_armed_only_after_successful_interrupt(self):
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)
        unit.interrupt = AsyncMock(return_value=True)

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=5.0):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert unit._tool_hang_interrupt_at is not None

    @pytest.mark.asyncio
    async def test_grace_not_armed_when_interrupt_fails(self):
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)
        unit.interrupt = AsyncMock(return_value=False)  # interrupt failed

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=5.0):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Grace must NOT be armed → backstop stays able to fire.
        assert unit._tool_hang_interrupt_at is None


# ── AC3: live tool (busy CPU) → NEVER interrupt ─────────────────────────


class TestLiveToolNeverInterrupted:
    @pytest.mark.asyncio
    async def test_live_cpu_tool_not_interrupted(self):
        """The v1-killer case: a long tool past its window but BURNING CPU."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700  # event-silent
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)

        # CPU probe returns a rising value → tool is working.
        cpu_vals = iter([10.0, 11.5, 13.0, 14.5, 16.0, 18.0])

        def _rising(_pid):
            try:
                return next(cpu_vals)
            except StopIteration:
                return 99.0

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   side_effect=_rising):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_subagent_long_window_not_probed_early(self):
        """Agent tools get the LONG window — not probed at the short window."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        # Agent open for 700s: past short window (600) but under long (1800).
        unit._open_tool_uses = _open_tool("Agent", age=700)

        probe = MagicMock(return_value=5.0)
        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   probe):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Under the long window → no probe, no interrupt.
        probe.assert_not_called()
        unit.interrupt.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail_safe_when_cpu_unreadable(self):
        """tree_cpu_seconds None → cannot prove dead → never interrupt."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit.interrupt.assert_not_called()
        unit._force_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_open_tool_no_interrupt(self):
        """No open tool → the tool-OPEN warm-interrupt tier (_maybe_escape_wedged_tool)
        must NOT fire. (Renamed from test_no_open_tool_no_probe: since
        run_dcd668a6 the tool-FREE backstop legitimately probes CPU on silence
        even with no tool open, so 'no probe at all' is no longer the invariant —
        'no tool-open INTERRUPT' is.) We stub the tool-free verdict to 'working'
        so the backstop spares the process and the loop keeps running, isolating
        the assertion to the tool-open interrupt path."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = {}
        unit._tool_free_hang_verdict = AsyncMock(return_value="working")

        with patch("os.kill", return_value=None):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # The tool-OPEN interrupt tier never fires without an open tool.
        unit.interrupt.assert_not_called()
        # And with a 'working' verdict the tool-free backstop spares it (no kill).
        unit._force_kill.assert_not_called()
        unit.interrupt.assert_not_called()


# ── AC4: repeated wedged episodes escalate to force-kill ────────────────


class TestEscalation:
    @pytest.mark.asyncio
    async def test_episodes_over_limit_force_kills(self):
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)
        # Already at the limit → next wedged detection escalates.
        unit._tool_hang_episodes = unit._TOOL_HANG_EPISODE_LIMIT

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=5.0):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit._force_kill.assert_called_once()
        unit.interrupt.assert_not_called()
        assert unit.state == SessionState.DEAD


# ── AC6: autonomous flag on interrupt() ─────────────────────────────────


class TestAutonomousFlag:
    @pytest.mark.asyncio
    async def test_autonomous_interrupt_does_not_set_user_stopped(self):
        """interrupt(autonomous=True) must NOT mark the turn user-stopped."""
        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "s"
        unit.state = SessionState.STREAMING
        unit._send_generation = 0
        unit._stop_event = asyncio.Event()
        unit._interrupted = False
        unit._user_stopped_current_turn = False
        unit._active_agent_tools = {}
        unit._open_tool_uses = {}
        unit._client = None  # no-client path → transitions DEAD→COLD, returns False
        unit._transition = MagicMock()
        unit._cleanup_internal = MagicMock()

        await unit.interrupt(autonomous=True)
        assert unit._user_stopped_current_turn is False

    @pytest.mark.asyncio
    async def test_user_interrupt_sets_user_stopped(self):
        """Default interrupt() (user Stop) still marks the turn user-stopped."""
        unit = SessionUnit.__new__(SessionUnit)
        unit.session_id = "s"
        unit.state = SessionState.STREAMING
        unit._send_generation = 0
        unit._stop_event = asyncio.Event()
        unit._interrupted = False
        unit._user_stopped_current_turn = False
        unit._active_agent_tools = {}
        unit._open_tool_uses = {}
        unit._client = None
        unit._transition = MagicMock()
        unit._cleanup_internal = MagicMock()

        await unit.interrupt()  # autonomous defaults False
        assert unit._user_stopped_current_turn is True


# ── helpers: _oldest_open_tool / _tool_open_window ──────────────────────


class TestHelpers:
    def test_oldest_open_tool_none_when_empty(self):
        unit = _make_unit()
        assert unit._oldest_open_tool() is None

    def test_oldest_open_tool_returns_age_and_name(self):
        unit = _make_unit()
        now = time.time()
        unit._open_tool_uses = {
            "a": (now - 10, "Read"),
            "b": (now - 100, "Bash"),
            "c": (now - 50, "Edit"),
        }
        age, name, tool_id = unit._oldest_open_tool()
        assert name == "Bash"
        assert tool_id == "b"
        assert 95 < age < 105

    def test_tool_open_window_long_for_agent_bash(self):
        unit = _make_unit()
        assert unit._tool_open_window("Agent") == unit.TOOL_HANG_OPEN_S_LONG
        assert unit._tool_open_window("Bash") == unit.TOOL_HANG_OPEN_S_LONG
        assert unit._tool_open_window("Read") == unit.TOOL_HANG_OPEN_S

    def test_io_wait_tools_get_long_window(self):
        """I/O-wait tools (MCP, WebFetch) sit at CPU=0 while waiting on the
        network — CPU cannot prove they're alive, so they MUST get the long
        window to avoid premature interrupt (option A: tolerate the CPU blind
        spot by widening the window for known network tools)."""
        unit = _make_unit()
        # MCP tools have dynamic names: mcp__<server>__<tool>
        assert unit._tool_open_window("mcp__slack__post_message") == unit.TOOL_HANG_OPEN_S_LONG
        assert unit._tool_open_window("mcp__aws-outlook-mcp__email_send") == unit.TOOL_HANG_OPEN_S_LONG
        assert unit._tool_open_window("WebFetch") == unit.TOOL_HANG_OPEN_S_LONG
        # Pure-compute / fast local tools keep the short window.
        assert unit._tool_open_window("Read") == unit.TOOL_HANG_OPEN_S
        assert unit._tool_open_window("Edit") == unit.TOOL_HANG_OPEN_S


# ── v2 adversarial fixes: episode reset, hard ceiling, stale-tool ───────


class TestEpisodeReset:
    @pytest.mark.asyncio
    async def test_episode_counter_resets_on_tool_result(self):
        """A completed tool (ToolResultBlock) resets _tool_hang_episodes so the
        limit counts CONSECUTIVE unrecovered wedges, not lifetime-separate ones.

        Mirrors the orchestrator's ToolResultBlock handler behavior; verified
        here at the unit level since that's where the reset must land.
        """
        unit = _make_unit()
        unit._tool_hang_episodes = 2
        # Simulate the orchestrator ToolResultBlock clear sequence.
        unit._open_tool_uses.pop("none", None)
        unit._tool_hang_interrupted = False
        unit._tool_hang_interrupt_at = None
        unit._tool_hang_episodes = 0
        assert unit._tool_hang_episodes == 0


class TestHardCeiling:
    @pytest.mark.asyncio
    async def test_cpu_busy_tool_force_killed_at_ceiling(self):
        """A tool open past the absolute ceiling is force-killed even if CPU is
        live (the infinite-CPU-loop case the warm tier never escapes)."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 4000
        unit._open_tool_uses = _open_tool(
            "Read", age=unit.TOOL_HANG_HARD_CEILING_S + 60
        )

        # CPU probe would say "live" (rising), but ceiling overrides.
        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   side_effect=[10.0, 20.0]):
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        unit._force_kill.assert_called_once()
        unit.interrupt.assert_not_called()
        assert unit.state == SessionState.DEAD


class TestStaleToolDuringProbe:
    @pytest.mark.asyncio
    async def test_no_interrupt_if_tool_completed_during_probe(self):
        """If the sampled tool completes during the CPU probe sleep, do not
        interrupt (it would be attributed to a now-gone tool)."""
        unit = _make_unit()
        unit.state = SessionState.STREAMING
        unit._last_event_time = time.time() - 700
        unit._open_tool_uses = _open_tool("Read", age=unit.TOOL_HANG_OPEN_S + 60)

        # During the probe sleep, simulate the tool completing by clearing it.
        async def _clear_after(_pid):
            unit._open_tool_uses.clear()

        with patch("os.kill", return_value=None), \
             patch("core.resource_monitor.resource_monitor.tree_cpu_seconds",
                   return_value=5.0), \
             patch.object(unit, "_oldest_open_tool",
                          wraps=unit._oldest_open_tool):
            # Pop the tool mid-sleep via a background task.
            async def popper():
                await asyncio.sleep(0.01)
                unit._open_tool_uses.clear()
            task = asyncio.create_task(unit._pid_watchdog_loop(12345))
            pop = asyncio.create_task(popper())
            await asyncio.sleep(0.2)
            task.cancel()
            pop.cancel()
            for t in (task, pop):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # Tool gone during probe → re-verify bails → no interrupt.
        unit.interrupt.assert_not_called()
