"""Every SIGKILL SwarmAI sends to a CLI process tree is recorded (F6 of the
2026-10-21 system-stability design).

- ``record(pids, reason)`` — append one JSON line per pid to
  ``~/.swarm-ai/state/kills.jsonl``: ``{ts, pid, reason, caller, by}`` where
  *caller* is the first frames above the kill helpers (so the 20+ ``unit.kill()``
  call sites are told apart without each passing a reason) and *by* is this
  process's pid. Also kept in memory for ``killed_by_us``.
- ``killed_session_recently(session_id, window_s)`` — True when this process
  recorded a kill of that session's CLI in the last *window_s* seconds (the
  stream reader then logs the CLI's exit -9 at INFO: it is our own doing).
- ``killed_by_us(window_s)`` — True when this process recorded a kill in the last
  *window_s* seconds: a SIGKILL'd CLI with no such record was killed by someone
  else (7 clusters 10-19 → 10-21 of 3-5 CLIs dying within 230 ms, 27 unexplained
  -9 exits, none of them found by reading the logs backwards).

The file is bounded (moved to kills.jsonl.1 at 1 MB). Recording never raises.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_MAX_BYTES = 1024 * 1024
_SKIP_FUNCS = frozenset({"record", "_kill_pids", "_force_kill", "kill", "_kill_tracked_pids"})
_lock = threading.Lock()
_recent: "deque[float]" = deque(maxlen=256)
_recent_reasons: "deque[tuple[float, str]]" = deque(maxlen=256)


def _path() -> Path:
    try:
        from config import get_app_data_dir
        base = Path(get_app_data_dir())
    except Exception:  # noqa: BLE001 — fall back to the default location
        base = Path.home() / ".swarm-ai"
    return base / "state" / "kills.jsonl"


def _caller(depth: int = 3) -> str:
    """'file.py:func <- file.py:func …' of the frames above the kill helpers."""
    out = []
    f = sys._getframe(1)
    while f is not None and len(out) < depth:
        name = f.f_code.co_name
        fname = os.path.basename(f.f_code.co_filename)
        if fname != "kill_audit.py" and name not in _SKIP_FUNCS:
            out.append(f"{fname}:{name}")
        f = f.f_back
    return " <- ".join(out)


def record(pids: "Iterable[int]", reason: str) -> None:
    pids = [int(p) for p in pids if p]
    if not pids:
        return
    now = time.time()
    with _lock:
        _recent.append(time.monotonic())
        _recent_reasons.append((time.monotonic(), reason))
    try:
        caller = _caller()
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            try:
                if path.stat().st_size >= _MAX_BYTES:
                    os.replace(path, path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as fh:
                for pid in pids:
                    fh.write(json.dumps({"ts": round(now, 3), "pid": pid, "reason": reason,
                                         "caller": caller, "by": os.getpid()}) + "\n")
    except Exception:  # noqa: BLE001 — auditing must never break a kill
        logger.debug("kill_audit.record failed", exc_info=True)


def killed_by_us(window_s: float = 30.0) -> bool:
    now = time.monotonic()
    with _lock:
        return any(now - t <= window_s for t in _recent)


def killed_session_recently(session_id: str, window_s: float = 10.0) -> bool:
    if not session_id:
        return False
    tag, now = f"session {session_id}", time.monotonic()
    with _lock:
        return any(now - t <= window_s and r == tag for t, r in _recent_reasons)


def _reset_for_tests() -> None:
    with _lock:
        _recent.clear()
        _recent_reasons.clear()
