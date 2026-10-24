"""One operation at a time on one run (r6 §2.2, commit 3).

The store's own lock covers a single append. A command reads state, decides, and then
appends — two CLI calls in parallel (or a CLI call and the daemon writing a reviewer's
answer) could both decide on the same old state. The operation lock serializes those
read-decide-write transactions per run; whoever gets it second re-reads and is judged
on the new state.

- ``operation(audit_root, run_id, timeout=...)`` — a context manager holding an
  exclusive flock on ``<audit>/runs/<run_id>/operation.lock``. Re-entrant within one
  thread. Raises ``RunBusy`` when another holder keeps it past *timeout*.

Not under this lock: read-only calls (``status``).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from utils.file_lock import flock_exclusive_nb, flock_unlock

_HELD = threading.local()


class RunBusy(RuntimeError):
    """Another operation on this run kept the operation lock past the timeout."""


@contextmanager
def operation(audit_root: str | Path, run_id: str, *, timeout: float = 120.0,
              poll: float = 0.05) -> Iterator[None]:
    from core.run_protocol.identifiers import validate_run_id
    validate_run_id(run_id)        # before any path is built from it
    key = (str(Path(audit_root).resolve()), run_id)
    held = getattr(_HELD, "keys", None)
    if held is None:
        held = _HELD.keys = {}
    if held.get(key):                       # re-entrant: this thread already holds it
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    directory = Path(audit_root) / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(directory / "operation.lock", "a+", encoding="utf-8")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                flock_exclusive_nb(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RunBusy(f"run {run_id} is busy with another operation; "
                                  "retry when it finishes") from None
                time.sleep(poll)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
            flock_unlock(handle)
    finally:
        handle.close()


__all__ = ["RunBusy", "operation"]
