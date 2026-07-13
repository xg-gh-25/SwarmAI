"""Cross-platform file locking utilities.

Provides ``flock_exclusive``, ``flock_shared``, ``flock_exclusive_nb``, and
``flock_unlock`` as drop-in replacements for ``fcntl.flock`` that work on
both Unix (fcntl.flock) and Windows (msvcrt.locking).

Also provides ``_IS_WINDOWS`` for callers that need platform checks.

Usage (replacing ``fcntl.flock`` calls)::

    from utils.file_lock import flock_exclusive, flock_unlock

    fd = open(lock_path, "w")
    try:
        flock_exclusive(fd)
        # ... critical section ...
    finally:
        flock_unlock(fd)
        fd.close()

For shared (reader) locks that allow concurrent readers on Unix::

    from utils.file_lock import flock_shared, flock_unlock
    # Falls back to exclusive on Windows (msvcrt has no shared mode).

For non-blocking (try-lock) semantics::

    from utils.file_lock import flock_exclusive_nb, flock_unlock

    fd = open(lock_path, "w")
    try:
        flock_exclusive_nb(fd)  # raises BlockingIOError if already locked
    except (BlockingIOError, OSError):
        fd.close()
        return  # lock held by another process
"""
from __future__ import annotations

import platform
from contextlib import contextmanager
from pathlib import Path

_IS_WINDOWS = platform.system() == "Windows"

if not _IS_WINDOWS:
    import fcntl
else:
    import msvcrt


def flock_exclusive(fd) -> None:
    """Acquire an exclusive (blocking) file lock.

    On Windows, ``msvcrt.locking`` locks a byte range (not the whole file).
    We seek to 0 first so all callers lock the same byte — achieving mutual
    exclusion equivalent to Unix ``flock``.  Only use with dedicated ``.lock``
    sidecar files, not with files you're actively reading/writing.
    """
    if _IS_WINDOWS:
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)


def flock_shared(fd) -> None:
    """Acquire a shared (blocking) file lock.

    On Unix, multiple processes may hold a shared lock simultaneously
    (e.g., concurrent readers of a discovery file).  On Windows,
    ``msvcrt.locking`` has no shared-lock concept, so this falls back
    to an exclusive lock.  Callers should assume "at most one reader
    at a time" on Windows, which is safe but slower under read contention.
    """
    if _IS_WINDOWS:
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_SH)


def flock_exclusive_nb(fd) -> None:
    """Acquire an exclusive non-blocking file lock.

    Raises ``BlockingIOError`` or ``OSError`` if the lock is already held.
    """
    if _IS_WINDOWS:
        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def flock_unlock(fd) -> None:
    """Release a file lock. Silently ignores errors on Windows."""
    if _IS_WINDOWS:
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def md_lock(md_path: "Path | str", *, blocking: bool = True):
    """Serialize read-modify-write of a markdown context file across ALL its
    writers on a single ``<md>.md.lock`` sidecar (the sibling-fd flock pattern
    MEMORY.md uses across _refresh_memory_index + _run_memory_lifecycle).

    A file lock only excludes OTHER lock-holders — so it protects a doc ONLY if
    EVERY writer of that doc wraps its read→write in this same lock. Locking one
    writer while others write unlocked is theater (Gate-1, run_a1ec08e7).

    Yields ``True`` when the lock is held, ``False`` when ``blocking=False`` and
    the lock was already held by someone else (caller MUST check and skip). A
    blocking acquire only ever yields ``True`` (or waits). The lock fd is always
    released + closed in ``finally``.

        # blocking (section refreshers — mirror _refresh_memory_index):
        with md_lock(path) as _got:            # always True
            content = path.read_text(); path.write_text(mutate(content))

        # non-blocking (destructive lifecycle strip — mirror _run_memory_lifecycle):
        with md_lock(path, blocking=False) as got:
            if not got:
                return                          # someone else is writing; skip
            ...read-modify-write...
    """
    md_path = Path(md_path)
    lock_path = md_path.with_suffix(md_path.suffix + ".lock")
    fd = None
    got = False
    try:
        fd = open(lock_path, "w")  # noqa: SIM115 — released in finally
        if blocking:
            flock_exclusive(fd)
            got = True
        else:
            try:
                flock_exclusive_nb(fd)
                got = True
            except (BlockingIOError, OSError):
                got = False
        yield got
    finally:
        if fd is not None:
            if got:
                flock_unlock(fd)
            fd.close()
