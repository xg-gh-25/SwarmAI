"""The session's kill, watch and reap paths on Windows (forced on any host).

On Windows ``os.getpgid``, ``os.killpg`` and ``signal.SIGKILL`` do not exist,
``os.kill(pid, 0)`` is ``GenerateConsoleCtrlEvent(CTRL_C_EVENT)`` (an ``OSError`` for a
pid that is not a console process group, never ``ProcessLookupError``) and there is no
``pgrep``. Before the fix, closing a chat session raised AttributeError in
``_force_kill`` after the DEAD transition, so the CLI and its children kept running and
the wrapper never closed; the PID watchdog died on its first poll; and orphans were
never reaped.

Each Windows test sets ``sys.platform`` to ``win32``, deletes the POSIX-only attributes,
models ``os.kill`` the way Windows answers it, and replaces psutil's process table with
an in-memory one, so the real branch under test runs on a macOS/Linux CI host.
"""
from __future__ import annotations

import ast
import asyncio
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import psutil
import pytest

import core.session_unit as su
from core import process_compat, session_registry
from core.lifecycle_manager import LifecycleManager
from core.session_unit import SessionState, SessionUnit

CLI = 4242
MCP_A, MCP_B, GRANDCHILD = 4300, 4301, 4400
DEAD_OWNER = 999_001
WIN_CLI_ARGV = [r"C:\Users\me\AppData\Local\SwarmAI\_internal\claude_agent_sdk\_bundled\claude.exe",
                "--output-format", "stream-json"]


class _World:
    """An in-memory process table that psutil and os.kill answer from."""

    def __init__(self):
        self.alive: set[int] = {CLI, MCP_A, MCP_B, GRANDCHILD}
        self.children: dict[int, list[int]] = {CLI: [MCP_A, MCP_B], MCP_A: [GRANDCHILD]}
        self.parents: dict[int, int] = {}
        self.cmdlines: dict[int, list[str]] = {}
        self.environ: dict[int, dict[str, str]] = {}
        self.killed: list[int] = []
        self.signal_zero: list[int] = []
        self.deny_kill: set[int] = set()       # TerminateProcess -> access denied
        self.deny_environ: set[int] = set()    # environment unreadable

    def proc(self, pid: int) -> "_Proc":
        if pid not in self.alive:
            raise psutil.NoSuchProcess(pid)
        return _Proc(self, pid)


class _Proc:
    def __init__(self, world: _World, pid: int):
        self.world, self.pid = world, pid
        self.info = {"pid": pid, "cmdline": world.cmdlines.get(pid)}

    def children(self, recursive: bool = False):
        out, stack = [], list(self.world.children.get(self.pid, []))
        while stack:
            pid = stack.pop(0)
            out.append(_Proc(self.world, pid))
            if recursive:
                stack.extend(self.world.children.get(pid, []))
        return out

    def is_running(self) -> bool:
        return self.pid in self.world.alive

    def kill(self) -> None:
        if self.pid not in self.world.alive:
            raise psutil.NoSuchProcess(self.pid)
        if self.pid in self.world.deny_kill:
            raise psutil.AccessDenied(self.pid)
        self.world.alive.discard(self.pid)
        self.world.killed.append(self.pid)

    def parent(self):
        ppid = self.world.parents.get(self.pid)
        return _Proc(self.world, ppid) if ppid in self.world.alive else None

    def ppid(self) -> int:
        return self.world.parents.get(self.pid, 0)

    def environ(self) -> dict[str, str]:
        if self.pid in self.world.deny_environ:
            raise psutil.AccessDenied(self.pid)
        return self.world.environ.get(self.pid, {})


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))     # kill_audit's record


@pytest.fixture
def windows(monkeypatch):
    """Windows, as far as the code under test can tell."""
    world = _World()
    monkeypatch.setattr(sys, "platform", "win32")
    for name in ("getpgid", "killpg"):
        if hasattr(os, name):
            monkeypatch.delattr(os, name)
    if hasattr(signal, "SIGKILL"):
        monkeypatch.delattr(signal, "SIGKILL")

    def win_os_kill(pid, sig):
        # Windows: sig 0 is CTRL_C_EVENT -> GenerateConsoleCtrlEvent, which fails with
        # WinError 87 for a pid that is not a console process group
        if sig == 0:
            world.signal_zero.append(pid)
            raise OSError(22, "The parameter is incorrect")
        if pid not in world.alive:
            raise OSError(22, "The parameter is incorrect")
        world.alive.discard(pid)
        world.killed.append(pid)

    def process_iter(attrs=None):
        return [_Proc(world, pid) for pid in sorted(world.alive)]

    def no_pgrep(*_a, **_k):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(os, "kill", win_os_kill)
    monkeypatch.setattr(psutil, "Process", world.proc)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid in world.alive)
    monkeypatch.setattr(psutil, "process_iter", process_iter)
    monkeypatch.setattr("core.lifecycle_manager.subprocess.run", no_pgrep)
    monkeypatch.setattr("core.session_registry.subprocess.run", no_pgrep)
    return world


def _unit(state: SessionState = SessionState.STREAMING) -> tuple[SessionUnit, MagicMock]:
    unit = SessionUnit(session_id="win-kill", agent_id="default")
    wrapper = MagicMock()
    wrapper.pid = CLI
    wrapper.__aexit__ = AsyncMock()
    unit._wrapper = wrapper
    unit._transition(SessionState.IDLE)
    if state == SessionState.STREAMING:
        unit._transition(SessionState.STREAMING)
    return unit, wrapper


# ── R1: kill / graceful close / exit wait ─────────────────────────────────────


@pytest.mark.asyncio
async def test_kill_on_windows_kills_the_whole_tree_and_reaches_cold(windows):
    unit, wrapper = _unit()
    await unit.kill()
    assert set(windows.killed) == {CLI, MCP_A, MCP_B, GRANDCHILD}
    assert windows.killed[-1] == CLI                      # the CLI last
    wrapper.__aexit__.assert_awaited_once()
    assert unit.state == SessionState.COLD
    assert windows.signal_zero == []


@pytest.mark.asyncio
async def test_graceful_close_on_windows_touches_no_process_group(windows):
    unit, _ = _unit(SessionState.IDLE)
    transport = MagicMock()
    transport.end_input = AsyncMock()

    async def cli_exits():
        windows.alive.discard(CLI)          # the CLI exits by itself on stdin EOF
        return 0

    transport._process = MagicMock()
    transport._process.wait = cli_exits
    unit._wrapper.client._transport = transport

    assert await unit._close_cli_gracefully(CLI) is True
    assert set(windows.killed) == {MCP_A, MCP_B, GRANDCHILD}   # the survivors swept
    assert CLI not in windows.killed


@pytest.mark.asyncio
async def test_await_process_exit_on_windows_uses_psutil(windows):
    windows.alive.discard(CLI)
    await asyncio.wait_for(SessionUnit._await_process_exit(CLI, timeout=3.0), timeout=1.0)
    assert windows.signal_zero == []


def test_kill_pid_on_windows_maps_psutil_errors(windows, monkeypatch):
    process_compat.kill_pid(MCP_A)
    assert windows.killed == [MCP_A]
    with pytest.raises(ProcessLookupError):
        process_compat.kill_pid(MCP_A)                    # gone now

    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", denied)
    with pytest.raises(PermissionError):
        process_compat.kill_pid(MCP_B)


# ── R3: liveness checks of a live CLI ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pid_watchdog_on_windows_keeps_a_live_cli(windows):
    unit, _ = _unit()
    unit._PID_WATCHDOG_INTERVAL = 0.01
    unit._last_event_time = su.awake_time()
    task = asyncio.create_task(unit._pid_watchdog_loop(CLI))
    await asyncio.sleep(0.15)
    assert not task.done(), task.exception() if task.done() else None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert unit.state == SessionState.STREAMING
    assert windows.signal_zero == []


@pytest.mark.asyncio
async def test_pid_watchdog_on_windows_marks_an_exited_cli_dead(windows):
    unit, _ = _unit()
    unit._PID_WATCHDOG_INTERVAL = 0.01
    windows.alive.discard(CLI)
    await asyncio.wait_for(unit._pid_watchdog_loop(CLI), timeout=1.0)
    assert unit.state == SessionState.DEAD


@pytest.mark.asyncio
async def test_health_check_on_windows_reports_a_live_cli(windows, monkeypatch):
    unit, _ = _unit()
    from core import resource_monitor
    monkeypatch.setattr(resource_monitor.resource_monitor, "process_metrics",
                        lambda **_k: None)
    assert await unit.health_check() is True
    assert windows.signal_zero == []


_SESSION_PATH = ["core/session_unit.py", "core/streaming_orchestrator.py",
                 "core/lifecycle_manager.py", "core/session_registry.py"]


def test_session_path_never_probes_with_os_kill_zero():
    """Every liveness probe on the session path goes through process_compat.probe_pid:
    a direct os.kill(pid, 0) is Ctrl-C on Windows."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in _SESSION_PATH:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "kill"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                    and len(node.args) == 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == 0):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


def test_find_pids_by_cmdline_matches_backslash_paths(windows):
    windows.cmdlines = {CLI: WIN_CLI_ARGV, MCP_A: ["node.exe", "server.js"]}
    assert process_compat.find_pids_by_cmdline(
        "claude_agent_sdk/(_bundled/claude|-c.*from claude_agent_sdk)") == [CLI]
    assert process_compat.find_pids_by_cmdline("([unclosed") == []


# ── R2: orphan reaping ───────────────────────────────────────────────────────


def _manager() -> LifecycleManager:
    router = MagicMock()
    router.list_units.return_value = []
    return LifecycleManager(router=router)


@pytest.mark.asyncio
async def test_reap_by_pattern_on_windows_matches_cmdline_with_psutil(windows):
    windows.cmdlines = {CLI: WIN_CLI_ARGV, MCP_A: ["node.exe", "server.js"]}
    windows.environ = {CLI: {"SWARMAI_OWNER_PID": str(DEAD_OWNER)}}
    mgr = _manager()
    killed = await mgr._reap_by_pattern(
        "claude_agent_sdk/(_bundled/claude|-c.*from claude_agent_sdk)", "claude",
        mgr._snapshot_known_pids(), require_orphaned=True)
    assert killed == 1
    assert windows.killed == [CLI]


@pytest.mark.asyncio
async def test_owner_liveness_on_windows_uses_psutil(windows):
    mgr = _manager()
    windows.environ = {MCP_A: {"SWARMAI_OWNER_PID": str(DEAD_OWNER)}}
    assert await mgr._is_owned_orphan(MCP_A) is True            # its backend is gone
    windows.alive.add(DEAD_OWNER)
    assert await mgr._is_owned_orphan(MCP_A) is False           # its backend runs
    # this backend's own leaked child: an orphan once its parent is gone (Windows
    # never reparents to pid 1, the child keeps its dead parent's pid)
    windows.environ = {MCP_B: {"SWARMAI_OWNER_PID": str(os.getpid())}}
    windows.parents = {MCP_B: 5555}
    assert await mgr._is_owned_orphan(MCP_B) is True
    windows.alive.add(5555)
    assert await mgr._is_owned_orphan(MCP_B) is False
    assert windows.signal_zero == []


def test_startup_sweep_on_windows_kills_a_dead_owners_cli_tree(windows, monkeypatch):
    monkeypatch.setattr("core.session_registry.platform.system", lambda: "Windows")
    windows.cmdlines = {CLI: WIN_CLI_ARGV}
    windows.environ = {CLI: {"SWARMAI_OWNER_PID": str(DEAD_OWNER)}}
    assert session_registry.kill_all_claude_processes() == 1
    assert set(windows.killed) == {CLI, MCP_A, MCP_B, GRANDCHILD}
    assert windows.killed[-1] == CLI
    assert windows.signal_zero == []


# ── POSIX keeps its system calls ─────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX branch")
def test_posix_probe_and_kill_keep_os_kill(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    process_compat.probe_pid(CLI)
    process_compat.kill_pid(CLI)
    assert calls == [(CLI, 0), (CLI, signal.SIGKILL)]


# Fail closed: a matching process that is not provably a dead backend's is never killed.
_NOT_PROVABLY_OURS = {
    "untagged": lambda w: None,
    "environment unreadable": lambda w: w.deny_environ.add(CLI),
    "owner alive": lambda w: (w.environ.update({CLI: {"SWARMAI_OWNER_PID": "5555"}}),
                              w.alive.add(5555)),
}


@pytest.mark.parametrize("case", sorted(_NOT_PROVABLY_OURS))
def test_startup_sweep_on_windows_spares_a_cli_not_provably_ours(windows, monkeypatch, case):
    monkeypatch.setattr("core.session_registry.platform.system", lambda: "Windows")
    windows.cmdlines = {CLI: WIN_CLI_ARGV}
    _NOT_PROVABLY_OURS[case](windows)
    assert session_registry.kill_all_claude_processes() == 0
    assert windows.killed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_NOT_PROVABLY_OURS))
async def test_reap_by_pattern_on_windows_spares_a_cli_not_provably_ours(windows, case):
    windows.cmdlines = {CLI: WIN_CLI_ARGV}
    _NOT_PROVABLY_OURS[case](windows)
    mgr = _manager()
    killed = await mgr._reap_by_pattern(
        "claude_agent_sdk/(_bundled/claude|-c.*from claude_agent_sdk)", "claude",
        mgr._snapshot_known_pids(), require_orphaned=True)
    assert killed == 0
    assert windows.killed == []


def test_startup_sweep_on_windows_logs_descendants_when_the_cli_kill_fails(
        windows, monkeypatch, caplog):
    monkeypatch.setattr("core.session_registry.platform.system", lambda: "Windows")
    windows.cmdlines = {CLI: WIN_CLI_ARGV}
    windows.environ = {CLI: {"SWARMAI_OWNER_PID": str(DEAD_OWNER)}}
    windows.deny_kill.add(CLI)
    with caplog.at_level("INFO", logger="core.session_registry"):
        assert session_registry.kill_all_claude_processes() == 0
    assert set(windows.killed) == {MCP_A, MCP_B, GRANDCHILD}
    assert any("+3 descendants killed" in r.getMessage() for r in caplog.records)


def test_owner_tag_unreadable_on_windows_reads_no_host_fallback(windows, monkeypatch):
    """psutil is the only reader on Windows: an unreadable environment is None, never
    a ps / /proc read of whatever process holds that pid on the host."""
    from core import session_utils

    def no_fallback(*_a, **_k):
        raise AssertionError("ps fallback read on Windows")
    monkeypatch.setattr("core.session_utils.platform.system", lambda: "Darwin")
    monkeypatch.setattr("core.session_utils.subprocess.run", no_fallback)
    windows.deny_environ.add(CLI)
    assert session_utils.read_owner_pid(CLI) is None


# ── Children started in their own session (start_new_session=True) ─────────


def test_kill_process_group_on_windows_kills_the_tree(windows):
    process_compat.kill_process_group(CLI)
    assert set(windows.killed) == {CLI, MCP_A, MCP_B, GRANDCHILD}
    assert windows.killed[-1] == CLI
    with pytest.raises(ProcessLookupError):
        process_compat.kill_process_group(CLI)


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX branch")
def test_kill_process_group_on_posix_signals_the_group(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    process_compat.kill_process_group(CLI)
    assert calls == [(CLI, signal.SIGKILL)]


def test_aws_check_kill_on_windows_kills_the_profile_programs_tree(windows):
    from core import aws_detect
    proc = MagicMock()
    proc.pid = CLI
    aws_detect._kill(proc)                       # no AttributeError
    assert set(windows.killed) == {CLI, MCP_A, MCP_B, GRANDCHILD}


def test_new_session_children_are_never_killed_with_a_bare_killpg():
    """A start_new_session child's group is killed through process_compat:
    os.killpg does not exist on Windows."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("core/aws_detect.py", "core/project_agent_session.py",
                "core/expert_bench/cases.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "killpg"):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
