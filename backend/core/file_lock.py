"""Cross-platform file locking utilities.

Provides ``flock_exclusive``, ``flock_shared``, ``flock_exclusive_nb``, and
``flock_unlock`` as drop-in replacements for ``fcntl.flock`` that work on
both Unix (fcntl.flock) and Windows (msvcrt.locking).

Also provides ``_IS_WINDOWS`` for callers that need platform checks.

Usage (replacing ``fcntl.flock`` calls)::

    from core.file_lock import flock_exclusive, flock_unlock

    fd = open(lock_path, "w")
    try:
        flock_exclusive(fd)
        # ... critical section ...
    finally:
        flock_unlock(fd)
        fd.close()

For shared (reader) locks that allow concurrent readers on Unix::

    from core.file_lock import flock_shared, flock_unlock
    # Falls back to exclusive on Windows (msvcrt has no shared mode).

For non-blocking (try-lock) semantics::

    from core.file_lock import flock_exclusive_nb, flock_unlock

    fd = open(lock_path, "w")
    try:
        flock_exclusive_nb(fd)  # raises BlockingIOError if already locked
    except (BlockingIOError, OSError):
        fd.close()
        return  # lock held by another process
"""
from __future__ import annotations

import platform

_IS_WINDOWS = platform.system() == "Windows"

if not _IS_WINDOWS:
    import fcntl
else:
    import msvcrt


def flock_exclusive(fd) -> None:
    """Acquire an exclusive (blocking) file lock."""
    if _IS_WINDOWS:
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
        msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_SH)


def flock_exclusive_nb(fd) -> None:
    """Acquire an exclusive non-blocking file lock.

    Raises ``BlockingIOError`` or ``OSError`` if the lock is already held.
    """
    if _IS_WINDOWS:
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
