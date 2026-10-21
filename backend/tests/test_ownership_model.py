"""Tests for the ownership-based orphan detection model.

Validates that the reaper only kills processes it owns (via SWARMAI_OWNER_PID
env tag) and never touches untagged or foreign-owned processes.

Key properties:
- Processes with SWARMAI_OWNER_PID matching current PID + dead parent = orphan
- Processes with SWARMAI_OWNER_PID matching a live PID = not orphan
- Processes without SWARMAI_OWNER_PID = not orphan (never touch)
- Ownership tag is set at startup in os.environ
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.lifecycle_manager import LifecycleManager


class TestOwnershipTagSetup:
    """Verify SWARMAI_OWNER_PID is set correctly."""

    def test_env_var_set_after_configure(self):
        """_configure_claude_environment sets SWARMAI_OWNER_PID."""
        from core.claude_environment import _configure_claude_environment
        from core.app_config_manager import AppConfigManager

        config = AppConfigManager.instance()
        config.load()

        # Ensure it's set (may already be from a previous test)
        old_val = os.environ.get("SWARMAI_OWNER_PID")
        try:
            os.environ.pop("SWARMAI_OWNER_PID", None)
            _configure_claude_environment(config)
            assert os.environ.get("SWARMAI_OWNER_PID") == str(os.getpid())
        finally:
            if old_val:
                os.environ["SWARMAI_OWNER_PID"] = old_val

    def test_child_inherits_owner_pid(self):
        """Child processes inherit SWARMAI_OWNER_PID from parent."""
        os.environ["SWARMAI_OWNER_PID"] = str(os.getpid())
        try:
            result = subprocess.run(
                ["python3", "-c", "import os; print(os.environ.get('SWARMAI_OWNER_PID', ''))"],
                capture_output=True, text=True, timeout=5,
            )
            assert result.stdout.strip() == str(os.getpid())
        finally:
            pass  # Leave it set — other tests may need it


class TestIsOwnedOrphan:
    """Test _is_owned_orphan logic."""

    @pytest.mark.asyncio
    async def test_no_tag_returns_false(self):
        """Process without SWARMAI_OWNER_PID is never an orphan."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)

        with patch.object(manager, "_read_process_owner_pid", return_value=None):
            result = await manager._is_owned_orphan(99999)
            assert result is False

    @pytest.mark.asyncio
    async def test_live_owner_returns_false(self):
        """Process owned by a live backend is not an orphan."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)

        # Owner PID is our own PID (alive)
        with patch.object(manager, "_read_process_owner_pid",
                          return_value=os.getpid()):
            # Parent is our PID (not reparented); read via psutil, no `ps` fork
            proc = MagicMock()
            proc.ppid.return_value = os.getpid()
            with patch("psutil.Process", return_value=proc):
                result = await manager._is_owned_orphan(99999)
                assert result is False

    @pytest.mark.asyncio
    async def test_dead_owner_returns_true(self):
        """Process owned by a dead backend IS an orphan."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)

        # Owner PID is 999999 (definitely dead)
        with patch.object(manager, "_read_process_owner_pid",
                          return_value=999999):
            result = await manager._is_owned_orphan(99999)
            assert result is True

    @pytest.mark.asyncio
    async def test_own_child_reparented_is_orphan(self):
        """Our own child that got reparented to launchd (ppid=1) is orphan."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)

        with patch.object(manager, "_read_process_owner_pid",
                          return_value=os.getpid()):
            # ppid=1 (reparented to launchd); read via psutil, no `ps` fork
            proc = MagicMock()
            proc.ppid.return_value = 1
            with patch("psutil.Process", return_value=proc):
                result = await manager._is_owned_orphan(99999)
                assert result is True


class TestSnapshotKnownPids:
    """Verify _snapshot_known_pids includes self-protection."""

    def test_includes_own_pid(self):
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)
        pids = manager._snapshot_known_pids()
        assert os.getpid() in pids

    def test_includes_parent_pid(self):
        mock_router = MagicMock()
        mock_router.list_units.return_value = []
        manager = LifecycleManager(router=mock_router)
        pids = manager._snapshot_known_pids()
        ppid = os.getppid()
        if ppid > 1:
            assert ppid in pids



def test_read_owner_pid_reads_the_environment_without_ps():
    """The orphan reaper ran past its 30s budget 11 times on 10-21: one `ps eww`
    fork per pgrep match. read_owner_pid now asks psutil (sysctl, no fork)."""
    import subprocess as sp
    import sys
    from core.session_utils import read_owner_pid
    p = sp.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                 env={"SWARMAI_OWNER_PID": "4242", "PATH": "/usr/bin:/bin"})
    try:
        import time as _t
        _t.sleep(0.3)
        with patch("core.session_utils.subprocess.run",
                   side_effect=AssertionError("no ps fork")):
            assert read_owner_pid(p.pid) == 4242
    finally:
        p.kill()
        p.wait()
    assert read_owner_pid(p.pid) is None   # gone
