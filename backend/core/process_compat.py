"""Portable process primitives for the session's kill, watch and reap paths.

macOS and Linux keep the exact system calls these paths always used: ``os.kill(pid, 0)``
as the liveness probe, ``os.kill(pid, SIGKILL)`` as the kill and ``pgrep -f`` as the
command-line match (that one stays with its callers). Windows has none of them in a
usable form: ``signal.SIGKILL`` does not exist, ``os.kill(pid, 0)`` is
``GenerateConsoleCtrlEvent(CTRL_C_EVENT)`` (Ctrl-C to a console process group, or an
``OSError`` for any other pid, never ``ProcessLookupError``), and there is no ``pgrep``.
There ``psutil`` does the work.

``probe_pid`` and ``kill_pid`` keep ``os.kill``'s exception contract on every platform:
``ProcessLookupError`` when the process is gone, ``PermissionError`` when it exists but
may not be signalled. A caller's ``except`` clauses therefore read the same on both.

The platform is read per call (``sys.platform``), never cached at import.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    return sys.platform == "win32"


def probe_pid(pid: int) -> None:
    """Return when *pid* is a running process; raise ``ProcessLookupError`` when not.

    POSIX: ``os.kill(pid, 0)`` (its ``PermissionError`` for another user's process
    propagates as before). Windows: ``psutil.pid_exists``, which reads the process's
    exit code, so an exited process whose handle is still open counts as gone.
    """
    if not is_windows():
        os.kill(pid, 0)
        return
    import psutil
    if not psutil.pid_exists(pid):
        raise ProcessLookupError(f"no process {pid}")


def kill_pid(pid: int) -> None:
    """Kill *pid* outright: ``SIGKILL`` on POSIX, ``TerminateProcess`` on Windows.

    Windows goes through ``psutil.Process(pid).kill()``: ``os.kill`` there raises a
    plain ``OSError`` (WinError 87) for a pid that has just exited. psutil's
    ``NoSuchProcess`` becomes ``ProcessLookupError`` and ``AccessDenied``
    ``PermissionError``.
    """
    if not is_windows():
        os.kill(pid, signal.SIGKILL)
        return
    import psutil
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess as exc:      # ZombieProcess is a NoSuchProcess
        raise ProcessLookupError(f"no process {pid}") from exc
    except psutil.AccessDenied as exc:
        raise PermissionError(f"may not kill process {pid}") from exc


def find_pids_by_cmdline(pattern: str) -> list[int]:
    """The pids whose command line matches the regex *pattern*, as ``pgrep -f`` would.

    Reads every process's command line through ``psutil.process_iter``, joins its
    arguments with spaces and searches it, with Windows backslash paths written as
    ``/`` so one pattern matches on every platform. This process is left out, like
    ``pgrep`` leaves itself out. A process whose command line cannot be read is
    skipped; an invalid pattern matches nothing (``pgrep`` exits 2 on one). Blocking:
    call it off the event loop.
    """
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        logger.warning("process_compat: invalid process pattern %r: %s", pattern, exc)
        return []
    import psutil
    me = os.getpid()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        info = proc.info
        pid, argv = info.get("pid"), info.get("cmdline")
        if pid is None or pid == me or not argv:
            continue
        line = " ".join(argv).replace("\\", "/")
        if regex.search(line):
            pids.append(int(pid))
    return pids


def kill_process_group(pid: int) -> None:
    """Kill the child *pid* started with ``start_new_session=True`` and everything it
    started: on POSIX its own process group (pgid == pid) with ``SIGKILL``; on Windows,
    which has no process groups, *pid* and its psutil descendants, the descendants
    first. ``ProcessLookupError`` when *pid* is gone (on Windows: before the snapshot).
    """
    if not is_windows():
        os.killpg(pid, signal.SIGKILL)
        return
    import psutil
    try:
        root = psutil.Process(pid)
        tree = root.children(recursive=True)
    except psutil.NoSuchProcess as exc:
        raise ProcessLookupError(f"no process {pid}") from exc
    except psutil.AccessDenied as exc:
        raise PermissionError(f"may not read process {pid}") from exc
    for proc in [*reversed(tree), root]:
        try:
            proc.kill()
        except psutil.Error:        # exited meanwhile, or not ours to kill
            continue
