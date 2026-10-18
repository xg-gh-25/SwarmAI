"""run_lock — one lock per pipeline run, and atomic run.json writes.

Every writer of ``runs/<id>/run.json`` (artifact_cli commands, the two reapers,
the router's cancel, the session-link stamper, takeover) serializes on the
sidecar ``runs/<id>/.lock`` (docs/design/pipeline-run-worktree.md §4.1). Before
this, ``run-update`` read the file at its start and wrote the whole dict at its
end, so a cancel landing in between was silently undone.

- ``run_lock(run_dir, timeout)``: context manager; RE-ENTRANT within a process
  (a held lock is counted, not re-acquired — flock on a second fd of the same
  file in the same process would block on itself). Raises ``RunLockTimeout``.
- ``try_run_lock(run_dir)``: non-blocking variant; yields False when busy.
- ``atomic_write_json(path, data)``: unique same-dir temp + ``os.replace``.
- ``update_run(run_file, mutate, timeout)``: lock → read → ``mutate(dict)`` →
  atomic write → the written dict (``mutate`` returning False skips the write).

Never runs git or tests; a lock holder only does read-modify-write (plus, in a
CLI process, whatever the command does before it exits — the lock is released
by process exit at the latest).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from utils.file_lock import flock_exclusive_nb, flock_unlock

LOCK_NAME = ".lock"
DEFAULT_TIMEOUT = 30.0
_POLL = 0.05

_held: dict[str, list] = {}   # realpath of lock file -> [fd, count]
_held_mutex = threading.Lock()


class RunLockTimeout(TimeoutError):
    """The run lock stayed held by another process past the timeout."""


def _lock_path(run_dir: "str | Path") -> str:
    return os.path.realpath(os.path.join(str(run_dir), LOCK_NAME))


def _key(path: str) -> tuple[str, int]:
    # Per thread: flock is per open file description, so another thread's fd on the
    # same file conflicts with ours — re-entrancy must never span threads.
    return (path, threading.get_ident())


def _acquire(path: str, timeout: "float | None") -> bool:
    """Take (or re-enter) the lock at *path*. timeout None = try once."""
    k = _key(path)
    with _held_mutex:
        if k in _held:
            _held[k][1] += 1
            return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = open(path, "a+")  # noqa: SIM115 — released in _release
    deadline = time.monotonic() + (timeout or 0.0)
    while True:
        try:
            flock_exclusive_nb(fd)
            break
        except (BlockingIOError, OSError):
            if timeout is None or time.monotonic() >= deadline:
                fd.close()
                return False
            time.sleep(_POLL)
    with _held_mutex:
        _held[k] = [fd, 1]
    return True


def _release(path: str) -> None:
    k = _key(path)
    with _held_mutex:
        entry = _held.get(k)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] > 0:
            return
        del _held[k]
    try:
        flock_unlock(entry[0])
    finally:
        entry[0].close()


def is_held(run_dir: "str | Path") -> bool:
    """True iff THIS thread holds the run's lock."""
    with _held_mutex:
        return _key(_lock_path(run_dir)) in _held


@contextmanager
def run_lock(run_dir: "str | Path", timeout: float = DEFAULT_TIMEOUT) -> Iterator[None]:
    """Hold the run's lock for the block (re-entrant in this thread)."""
    path = _lock_path(run_dir)
    if not _acquire(path, timeout):
        raise RunLockTimeout(f"run lock busy for {timeout:.0f}s: {path}")
    try:
        yield
    finally:
        _release(path)


@contextmanager
def try_run_lock(run_dir: "str | Path") -> Iterator[bool]:
    """Non-blocking: yields True while held, False when another holder has it."""
    path = _lock_path(run_dir)
    got = _acquire(path, None)
    try:
        yield got
    finally:
        if got:
            _release(path)


def acquire_for_process(run_dir: "str | Path", timeout: float = DEFAULT_TIMEOUT) -> None:
    """Take the run lock until this (CLI) process exits. Used by artifact_cli's
    dispatcher so a whole command is one critical section; flock is released by
    the kernel at exit, including sys.exit paths."""
    path = _lock_path(run_dir)
    if not _acquire(path, timeout):
        raise RunLockTimeout(f"run lock busy for {timeout:.0f}s: {path}")


def atomic_write_json(path: "str | Path", data) -> None:
    """Write JSON via a unique same-dir temp file + os.replace (no torn file, no
    temp-name collision between threads or processes)."""
    p = Path(path)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


def update_run(run_file: "str | Path", mutate: Callable[[dict], "bool | None"],
               timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Locked read-modify-write of one run.json. ``mutate`` edits the dict in
    place; returning ``False`` skips the write. Returns the (possibly new) dict.
    Raises RunLockTimeout, OSError, ValueError (unreadable / not an object)."""
    p = Path(run_file)
    with run_lock(p.parent, timeout):
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"run.json is not an object: {p}")
        if mutate(data) is not False:
            atomic_write_json(p, data)
        return data
