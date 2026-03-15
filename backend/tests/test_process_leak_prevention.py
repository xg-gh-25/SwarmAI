"""Tests for claude CLI process leak prevention (COE 2026-03-15).

Verifies the multi-layer defense against zombie claude processes:
1. PID captured at spawn time on wrapper
2. PID registered in global _tracked_pids
3. PID stored in _active_sessions entries
4. kill_tracked_leaks detects and kills leaked PIDs
5. disconnect_wrapper always unregisters PIDs
6. kill_all_claude_processes kills everything at startup
"""

import asyncio
import os
import signal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend imports work
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.claude_environment import _ClaudeClientWrapper
from core.agent_manager import AgentManager


class TestWrapperPIDCapture:
    """Test that _ClaudeClientWrapper captures PID at spawn time."""

    def test_wrapper_init_has_pid_none(self):
        """PID is None before __aenter__."""
        options = MagicMock()
        wrapper = _ClaudeClientWrapper(options)
        assert wrapper.pid is None

    def test_extract_pid_with_mock_client(self):
        """_extract_pid walks the SDK internal chain correctly."""
        options = MagicMock()
        wrapper = _ClaudeClientWrapper(options)

        # Mock the internal chain: client._query._transport._process.pid
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_transport = MagicMock()
        mock_transport._process = mock_process
        mock_query = MagicMock()
        mock_query._transport = mock_transport
        mock_client = MagicMock()
        mock_client._query = mock_query

        wrapper.client = mock_client
        assert wrapper._extract_pid() == 12345

    def test_extract_pid_returns_none_on_missing_chain(self):
        """_extract_pid returns None if any link in the chain is missing."""
        options = MagicMock()
        wrapper = _ClaudeClientWrapper(options)
        wrapper.client = MagicMock(spec=[])  # No _query attribute
        assert wrapper._extract_pid() is None


class TestAgentManagerPIDTracking:
    """Test AgentManager PID registration and tracking."""

    def setup_method(self):
        self.manager = AgentManager()

    def test_tracked_pids_starts_empty(self):
        assert len(self.manager._tracked_pids) == 0

    def test_register_wrapper_pid(self):
        """_register_wrapper_pid adds to global tracker and session info."""
        wrapper = MagicMock()
        wrapper.pid = 42

        session_info = {}
        pid = self.manager._register_wrapper_pid(wrapper, session_info)

        assert pid == 42
        assert 42 in self.manager._tracked_pids
        assert session_info["pid"] == 42

    def test_register_wrapper_pid_none(self):
        """_register_wrapper_pid handles None PID gracefully."""
        wrapper = MagicMock()
        wrapper.pid = None

        session_info = {}
        pid = self.manager._register_wrapper_pid(wrapper, session_info)

        assert pid is None
        assert len(self.manager._tracked_pids) == 0
        assert "pid" not in session_info

    def test_unregister_pid(self):
        """_unregister_pid removes from global tracker."""
        self.manager._tracked_pids.add(42)
        self.manager._unregister_pid(42)
        assert 42 not in self.manager._tracked_pids

    def test_unregister_pid_none(self):
        """_unregister_pid handles None gracefully."""
        self.manager._unregister_pid(None)  # Should not raise

    def test_unregister_pid_not_tracked(self):
        """_unregister_pid handles untracked PID gracefully."""
        self.manager._unregister_pid(99999)  # Should not raise


class TestKillTrackedLeaks:
    """Test kill_tracked_leaks detects and kills leaked processes."""

    def setup_method(self):
        self.manager = AgentManager()

    @patch("os.kill")
    def test_kills_leaked_pid(self, mock_kill):
        """Kills PIDs that are tracked but not in any active session."""
        # Track a PID that's not in any active session
        self.manager._tracked_pids.add(12345)
        # No active sessions

        killed = self.manager.kill_tracked_leaks()

        assert killed == 1
        # Should have called os.kill(12345, 0) for existence check
        # then _force_kill_pid
        assert 12345 not in self.manager._tracked_pids

    @patch("os.kill")
    def test_skips_active_session_pids(self, mock_kill):
        """Does not kill PIDs that are in active sessions."""
        self.manager._tracked_pids.add(12345)
        self.manager._active_sessions["test-session"] = {"pid": 12345}

        killed = self.manager.kill_tracked_leaks()

        assert killed == 0
        assert 12345 in self.manager._tracked_pids

    @patch("os.kill", side_effect=ProcessLookupError)
    def test_cleans_up_dead_pids(self, mock_kill):
        """Cleans up PIDs of already-dead processes from tracker."""
        self.manager._tracked_pids.add(99999)

        killed = self.manager.kill_tracked_leaks()

        assert killed == 0  # Not counted as "killed" since it was already dead
        assert 99999 not in self.manager._tracked_pids


class TestDisconnectWrapperPIDCleanup:
    """Test that _disconnect_wrapper always unregisters PIDs."""

    def setup_method(self):
        self.manager = AgentManager()

    @pytest.mark.asyncio
    async def test_disconnect_unregisters_pid_on_success(self):
        """PID is removed from tracker after successful disconnect."""
        wrapper = MagicMock()
        wrapper.pid = 42
        wrapper.__aexit__ = AsyncMock(return_value=False)
        self.manager._tracked_pids.add(42)

        await self.manager._disconnect_wrapper(wrapper, "test")

        assert 42 not in self.manager._tracked_pids

    @pytest.mark.asyncio
    async def test_disconnect_unregisters_pid_on_timeout(self):
        """PID is removed from tracker even if disconnect times out."""
        wrapper = MagicMock()
        wrapper.pid = 42

        async def slow_exit(*args):
            await asyncio.sleep(100)

        wrapper.__aexit__ = slow_exit
        self.manager._tracked_pids.add(42)

        with patch.object(AgentManager, '_force_kill_pid'):
            await self.manager._disconnect_wrapper(wrapper, "test", timeout=0.01)

        assert 42 not in self.manager._tracked_pids

    @pytest.mark.asyncio
    async def test_disconnect_unregisters_pid_on_error(self):
        """PID is removed from tracker even if disconnect raises."""
        wrapper = MagicMock()
        wrapper.pid = 42
        wrapper.__aexit__ = AsyncMock(side_effect=RuntimeError("boom"))
        self.manager._tracked_pids.add(42)

        with patch.object(AgentManager, '_force_kill_pid'):
            await self.manager._disconnect_wrapper(wrapper, "test")

        assert 42 not in self.manager._tracked_pids


class TestKillAllClaudeProcesses:
    """Test the startup kill-all-claude function."""

    @patch("subprocess.run")
    def test_kills_all_on_darwin(self, mock_run):
        """On macOS, kills every claude process found."""
        mock_run.side_effect = [
            # pgrep -x claude
            MagicMock(returncode=0, stdout="100\n200\n"),
            # pkill -9 -P 100
            MagicMock(returncode=0),
            # pkill -9 -P 200
            MagicMock(returncode=0),
        ]

        with patch("os.kill") as mock_kill, \
             patch("platform.system", return_value="Darwin"):
            killed = AgentManager.kill_all_claude_processes()

        assert killed == 2

    @patch("subprocess.run")
    def test_returns_zero_when_no_processes(self, mock_run):
        """Returns 0 when no claude processes found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        with patch("platform.system", return_value="Darwin"):
            killed = AgentManager.kill_all_claude_processes()

        assert killed == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
