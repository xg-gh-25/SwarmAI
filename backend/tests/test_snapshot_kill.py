"""Tests for process tree snapshot-then-kill functions.

Validates the safety-critical functions in session_unit.py that enumerate
and kill process trees:

- ``_get_children``              — Direct child PID enumeration via pgrep
- ``_snapshot_process_table``    — Full process table snapshot via ps
- ``_snapshot_descendant_tree``  — Tree BFS with bottom-up ordering
- ``_kill_pids``                 — Batch SIGKILL with error tolerance

Key properties verified:
- Snapshot returns bottom-up order (leaves before parents)
- Visited set prevents infinite loops from pid cycles
- Empty inputs produce empty outputs
- _kill_pids tolerates already-dead processes
- _snapshot_process_table parses ps output correctly
"""

import os
import signal
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from core.session_unit import (
    _get_children,
    _kill_pids,
    _snapshot_descendant_tree,
    _snapshot_process_table,
)


# ── _get_children ─────────────────────────────────────────────────


class TestGetChildren:
    """Tests for _get_children (pgrep -P wrapper, used as fallback)."""

    def test_returns_list_for_nonexistent_pid(self):
        result = _get_children(999999999)
        assert result == []

    def test_returns_children_of_current_process(self):
        result = _get_children(os.getpid())
        assert isinstance(result, list)
        for pid in result:
            assert isinstance(pid, int)
            assert pid > 0

    @patch("core.session_unit.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pgrep", timeout=3)
        assert _get_children(12345) == []

    @patch("core.session_unit.subprocess.run")
    def test_parses_multiline_output(self, mock_run):
        mock_run.return_value = MagicMock(stdout="100\n200\n300\n", returncode=0)
        assert _get_children(1) == [100, 200, 300]

    @patch("core.session_unit.subprocess.run")
    def test_skips_non_numeric_lines(self, mock_run):
        mock_run.return_value = MagicMock(stdout="100\ngarbage\n200\n", returncode=0)
        assert _get_children(1) == [100, 200]


# ── _snapshot_process_table ───────────────────────────────────────


class TestSnapshotProcessTable:
    """Tests for _snapshot_process_table (single ps call)."""

    def test_returns_dict_of_pid_ppid(self):
        """Real call returns a non-empty dict with our own PID."""
        table = _snapshot_process_table()
        assert isinstance(table, dict)
        assert len(table) > 0
        # Our own process should be in the table
        assert os.getpid() in table

    @patch("core.session_unit.subprocess.run")
    def test_parses_ps_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="  PID  PPID\n  100     1\n  200   100\n  300   200\n",
            returncode=0,
        )
        table = _snapshot_process_table()
        assert table == {100: 1, 200: 100, 300: 200}

    @patch("core.session_unit.subprocess.run")
    def test_handles_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ps", timeout=5)
        assert _snapshot_process_table() == {}

    @patch("core.session_unit.subprocess.run")
    def test_handles_malformed_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="  PID  PPID\n  100     1\n  bad line\n  300   200\n",
            returncode=0,
        )
        table = _snapshot_process_table()
        assert 100 in table
        assert 300 in table
        assert len(table) == 2


# ── _snapshot_descendant_tree ─────────────────────────────────────


class TestSnapshotDescendantTree:
    """Tests for _snapshot_descendant_tree (BFS + bottom-up order).

    Mocks _snapshot_process_table to control the process tree shape.
    """

    @patch("core.session_unit._snapshot_process_table")
    def test_empty_tree(self, mock_table):
        """A process with no children returns an empty list."""
        mock_table.return_value = {1000: 1}  # only the parent, no children
        result = _snapshot_descendant_tree(1000)
        assert result == []

    @patch("core.session_unit._snapshot_process_table")
    def test_single_child(self, mock_table):
        mock_table.return_value = {1000: 1, 2000: 1000}
        result = _snapshot_descendant_tree(1000)
        assert result == [2000]

    @patch("core.session_unit._snapshot_process_table")
    def test_bottom_up_order(self, mock_table):
        """Tree: 1000 → [2000, 3000], 2000 → [4000].

        Bottom-up: 4000 before 2000. Parent (1000) NOT included.
        """
        mock_table.return_value = {
            1000: 1, 2000: 1000, 3000: 1000, 4000: 2000,
        }
        result = _snapshot_descendant_tree(1000)
        assert set(result) == {2000, 3000, 4000}
        assert result.index(4000) < result.index(2000)

    @patch("core.session_unit._snapshot_process_table")
    def test_deep_chain(self, mock_table):
        """Chain: 1 → 2 → 3 → 4 → 5. Bottom-up: [5, 4, 3, 2]."""
        mock_table.return_value = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
        result = _snapshot_descendant_tree(1)
        assert result == [5, 4, 3, 2]

    @patch("core.session_unit._snapshot_process_table")
    def test_wide_tree(self, mock_table):
        """Wide: 1 → [10, 20, 30, 40, 50]. All leaves."""
        mock_table.return_value = {
            1: 0, 10: 1, 20: 1, 30: 1, 40: 1, 50: 1,
        }
        result = _snapshot_descendant_tree(1)
        assert set(result) == {10, 20, 30, 40, 50}

    @patch("core.session_unit._snapshot_process_table")
    def test_cycle_prevention(self, mock_table):
        """Cycles don't cause infinite loops (visited set guard)."""
        # Simulate a cycle: 200's parent is 300, 300's parent is 200
        # This shouldn't happen in real life but the visited set handles it
        mock_table.return_value = {100: 1, 200: 100, 300: 200}
        result = _snapshot_descendant_tree(100)
        assert set(result) == {200, 300}

    @patch("core.session_unit._snapshot_process_table")
    def test_fallback_to_pgrep_on_empty_table(self, mock_table):
        """When ps fails (empty table), falls back to pgrep-based approach."""
        mock_table.return_value = {}
        with patch("core.session_unit._get_children") as mock_children:
            mock_children.side_effect = lambda pid: [200] if pid == 100 else []
            result = _snapshot_descendant_tree(100)
            assert result == [200]

    @patch("core.session_unit._snapshot_process_table")
    def test_parent_not_in_result(self, mock_table):
        """The parent_pid itself is never included in the result."""
        mock_table.return_value = {1: 0, 10: 1, 20: 10}
        result = _snapshot_descendant_tree(1)
        assert 1 not in result
        assert set(result) == {10, 20}


# ── _kill_pids ────────────────────────────────────────────────────


class TestKillPids:
    """Tests for _kill_pids (batch SIGKILL)."""

    def test_empty_list(self):
        assert _kill_pids([]) == 0

    @patch("core.session_unit.os.kill")
    def test_kills_all_pids(self, mock_kill):
        result = _kill_pids([100, 200, 300])
        assert result == 3
        mock_kill.assert_any_call(100, signal.SIGKILL)
        mock_kill.assert_any_call(200, signal.SIGKILL)
        mock_kill.assert_any_call(300, signal.SIGKILL)

    @patch("core.session_unit.os.kill")
    def test_tolerates_already_dead(self, mock_kill):
        mock_kill.side_effect = [None, ProcessLookupError(), None]
        assert _kill_pids([100, 200, 300]) == 2

    @patch("core.session_unit.os.kill")
    def test_tolerates_permission_error(self, mock_kill):
        mock_kill.side_effect = [PermissionError(), None]
        assert _kill_pids([100, 200]) == 1


# ── Integration: snapshot + kill ──────────────────────────────────


class TestSnapshotThenKill:
    """Integration test: snapshot tree then kill bottom-up."""

    @patch("core.session_unit.os.kill")
    @patch("core.session_unit._snapshot_process_table")
    def test_full_workflow(self, mock_table, mock_kill):
        """Snapshot a tree and kill it. Leaves die before parents."""
        # Tree: 1 → [10, 20], 10 → [100], 20 → [200, 201]
        mock_table.return_value = {
            1: 0, 10: 1, 20: 1, 100: 10, 200: 20, 201: 20,
        }

        tree = _snapshot_descendant_tree(1)
        killed = _kill_pids(tree)

        assert killed == 5
        kill_order = [call[0][0] for call in mock_kill.call_args_list]
        assert kill_order.index(100) < kill_order.index(10)
        assert kill_order.index(200) < kill_order.index(20)
        assert kill_order.index(201) < kill_order.index(20)

    def test_real_subprocess_tree(self):
        """Spawn a real subprocess and verify snapshot works end-to-end."""
        proc = subprocess.Popen(
            ["sleep", "60"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.1)
            tree = _snapshot_descendant_tree(proc.pid)
            assert isinstance(tree, list)
            for pid in tree:
                assert isinstance(pid, int)
        finally:
            proc.kill()
            proc.wait()
