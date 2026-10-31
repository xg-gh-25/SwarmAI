"""Detached backend children (core.code_intel.build.spawn_child) left by a dead backend.

A code-graph build child runs in its own session so it survives the job or request
that started it. Before this, the daemon's orphan reaper only knew claude, the dev
backend, pytest and MCP servers, so a build child of a previous daemon that hung (a
regex that backtracked forever) kept a core at full CPU for days. These tests drive
real processes through the reaper and the health probe's runaway-child check.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

from core.code_intel.build import DETACHED_CHILD_PATTERN, DETACHED_MODULES, spawn_child
from core.lifecycle_manager import LifecycleManager
from core.session_health_probe import Check, runaway_children, session_health_probe

# python -c ignores the rest of argv, so this is an idle process whose command line
# reads like a build child
_FAKE_CHILD = [sys.executable, "-c", "import time; time.sleep(60)", "-m", "core.code_intel.build",
               "--project=Probe"]


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _reparented_child(owner: int) -> int:
    """A fake build child owned by *owner* whose parent has exited (ppid 1), as when
    the job that spawned it is done. Returns its pid."""
    env = {**os.environ, "SWARMAI_OWNER_PID": str(owner)}
    launcher = subprocess.run(
        [sys.executable, "-c",
         "import subprocess, sys; p = subprocess.Popen(sys.argv[1:], start_new_session=True, "
         "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid)",
         *_FAKE_CHILD], env=env, capture_output=True, text=True, timeout=20, check=True)
    pid = int(launcher.stdout.strip())
    for _ in range(50):
        if subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                          text=True).stdout.strip() == "1":
            return pid
        time.sleep(0.1)
    return pid


def _alive(pid: int) -> bool:
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return out.returncode == 0 and not out.stdout.strip().startswith("Z")


def _manager() -> LifecycleManager:
    router = MagicMock()
    router.list_units.return_value = []
    return LifecycleManager(router=router)


def _wait_gone(pid: int) -> bool:
    for _ in range(50):
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))     # kill_audit's record


@pytest.mark.asyncio
async def test_a_dead_backends_build_child_is_reaped_and_a_live_ones_is_kept():
    orphan = _reparented_child(_dead_pid())
    mine = _reparented_child(os.getpid())     # this backend's, its job long exited
    try:
        mgr = _manager()
        killed = await mgr._reap_by_pattern(
            DETACHED_CHILD_PATTERN, "detached_child", mgr._snapshot_known_pids(),
            require_orphaned=True, previous_instance_only=True)
        assert killed == 1
        assert _wait_gone(orphan)
        assert _alive(mine)                    # still doing this backend's work
    finally:
        for pid in (orphan, mine):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
async def test_the_reaper_pass_includes_detached_children():
    """The periodic and startup pass (_reap_orphans_impl) asks for them, with the
    previous-instance rule."""
    mgr = _manager()
    calls = []

    async def spy(pattern, label, known, require_orphaned=False, previous_instance_only=False):
        calls.append((pattern, require_orphaned, previous_instance_only))
        return 0
    mgr._reap_by_pattern = spy
    mgr._get_mcp_server_patterns = lambda: []
    await mgr._reap_orphans_impl()
    assert (DETACHED_CHILD_PATTERN, True, True) in calls


def test_spawn_child_runs_only_a_listed_module():
    with pytest.raises(ValueError):
        spawn_child(["-m", "core.code_intel.impact", "x"])
    assert "core.code_intel.build" in DETACHED_MODULES and "core.library_mounts" in DETACHED_MODULES


def test_a_child_over_the_cpu_limit_degrades_the_probe_and_a_normal_one_does_not():
    kids = [{"pid": 7, "cmd": "py -m core.code_intel.build --project=A", "cpu_s": 160_000,
             "age_s": 160_000},
            {"pid": 8, "cmd": "py -m core.code_intel.build --project=B", "cpu_s": 35, "age_s": 40}]
    lines = runaway_children(kids)
    assert len(lines) == 1 and lines[0].startswith("pid 7 ") and "--project=A" in lines[0]

    def probe(children):
        return session_health_probe(
            health_fetcher=lambda: {"database": {"healthy": True},
                                    "channel_gateway": {"startup_state": "started"}},
            streaming_fetcher=lambda: [], rss_fetcher=lambda: 100.0,
            cpu_sampler=lambda pid: 0.0, log_reader=lambda: "",
            children_fetcher=lambda: children, sleep_fn=lambda s: None)
    red = probe(kids)
    assert red.red and "no_runaway_children" in red.summary
    assert not probe(kids[1:]).red
