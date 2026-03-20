"""Tests for process-group kill in SessionUnit._force_kill().

Verifies Fix 2: _force_kill() uses os.killpg(pgid, SIGKILL) to kill
the entire process tree when the child has its own process group,
falling back to plain os.kill when pgid lookup fails or when the
child shares the backend's pgid (safety guard — prevents self-kill).
"""

from __future__ import annotations

import os
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


def _make_unit(pid: int = 1234) -> SessionUnit:
    """Create a SessionUnit with a fake PID via _wrapper (matches real pid property)."""
    unit = SessionUnit(session_id="test-pgid", agent_id="default")
    unit._wrapper = MagicMock()
    unit._wrapper.pid = pid
    unit._wrapper.__aexit__ = AsyncMock()
    unit._transition(SessionState.STREAMING)
    return unit


class TestForceKillProcessGroup:
    """Verify _force_kill uses process group kill with safety guard."""

    @pytest.mark.asyncio
    async def test_kills_process_group_when_pgid_differs(self):
        """Happy path: child has different pgid → killpg called."""
        unit = _make_unit(pid=1234)

        # Child pgid=5678, our pgid=9999 → different → killpg is safe
        def mock_getpgid(pid):
            if pid == 1234:
                return 5678  # child's pgid
            return 9999  # our pgid

        with patch("core.session_unit.os.getpgid", side_effect=mock_getpgid), \
             patch("core.session_unit.os.getpid", return_value=42), \
             patch("core.session_unit.os.killpg") as mock_killpg:
            await unit._force_kill()

        mock_killpg.assert_called_once_with(5678, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_falls_back_to_kill_when_pgid_shared(self):
        """Safety guard: child shares our pgid → plain os.kill, NOT killpg."""
        unit = _make_unit(pid=1234)

        # Both return same pgid → shared → killpg would kill us
        with patch("core.session_unit.os.getpgid", return_value=5678), \
             patch("core.session_unit.os.getpid", return_value=42), \
             patch("core.session_unit.os.killpg") as mock_killpg, \
             patch("core.session_unit.os.kill") as mock_kill:
            await unit._force_kill()

        mock_killpg.assert_not_called()
        mock_kill.assert_called_once_with(1234, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_handles_already_dead_process_group(self):
        """killpg raises ProcessLookupError — silently handled."""
        unit = _make_unit(pid=1234)

        def mock_getpgid(pid):
            if pid == 1234:
                return 5678
            return 9999

        with patch("core.session_unit.os.getpgid", side_effect=mock_getpgid), \
             patch("core.session_unit.os.getpid", return_value=42), \
             patch("core.session_unit.os.killpg", side_effect=ProcessLookupError):
            await unit._force_kill()  # Should not raise

    @pytest.mark.asyncio
    async def test_handles_permission_error(self):
        """killpg raises PermissionError — silently handled."""
        unit = _make_unit(pid=1234)

        def mock_getpgid(pid):
            if pid == 1234:
                return 5678
            return 9999

        with patch("core.session_unit.os.getpgid", side_effect=mock_getpgid), \
             patch("core.session_unit.os.getpid", return_value=42), \
             patch("core.session_unit.os.killpg", side_effect=PermissionError):
            await unit._force_kill()  # Should not raise

    @pytest.mark.asyncio
    async def test_falls_back_to_plain_kill_on_pgid_failure(self):
        """getpgid raises OSError — falls back to os.kill(pid)."""
        unit = _make_unit(pid=1234)

        with patch("core.session_unit.os.getpgid", side_effect=OSError("no pgid")), \
             patch("core.session_unit.os.kill") as mock_kill:
            await unit._force_kill()

        mock_kill.assert_called_once_with(1234, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_fallback_handles_dead_process(self):
        """Fallback os.kill raises ProcessLookupError — silently handled."""
        unit = _make_unit(pid=1234)

        with patch("core.session_unit.os.getpgid", side_effect=OSError("no pgid")), \
             patch("core.session_unit.os.kill", side_effect=ProcessLookupError):
            await unit._force_kill()  # Should not raise

    @pytest.mark.asyncio
    async def test_noop_when_no_pid(self):
        """No PID → nothing happens."""
        unit = SessionUnit(session_id="test-no-pid", agent_id="default")

        with patch("core.session_unit.os.getpgid") as mock_pgid, \
             patch("core.session_unit.os.killpg") as mock_killpg, \
             patch("core.session_unit.os.kill") as mock_kill:
            await unit._force_kill()

        mock_pgid.assert_not_called()
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()
