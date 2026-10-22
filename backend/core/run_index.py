"""One shared, incremental read of every pipeline run.json (F2 of the 2026-10-21
system-stability design).

- ``runs(workspace)`` — ``[RunFile(path, mtime, data)]`` for every
  ``Projects/*/.artifacts/runs/*/run.json`` under *workspace*. A file is parsed
  again only when its (mtime_ns, size) changed since the last call, so a call costs
  one directory walk + one stat per run instead of parsing the whole history
  (1027 files / 7.4 MB measured 2026-10-21: ~200 ms to parse, ~20 ms to stat).
- ``live_runs(workspace)`` — the same, only status running / paused.

Every reader that used to glob + json.load the whole history on its own (the
worktree write gate, own_isolated_run, the commit / run-state gates, the pipelines
list) reads this instead. No writer has to cooperate: a run.json written by any
process is picked up by its new mtime, so the index cannot go stale.

``data`` is a SHALLOW COPY per call: a caller may add or replace top-level keys,
but must not mutate nested values (lists of stages / worktrees are shared with
the cache). A half-written file (invalid JSON) is skipped and not cached, so the
next call reads it again. Thread-safe; blocking (stat + read): call it off the
event loop.

Memory: the parsed history is kept (~18 MB of objects for the 1027 runs above).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

# legacy running/paused plus every non-terminal Pipeline v1 status
# (core.run_protocol.lifecycle.STATUSES - TERMINAL): sweeper and gates must never
# treat a live v1 run as finished
LIVE_STATUSES = ("running", "paused", "active", "awaiting_human", "reflecting")

# run.json path -> ((mtime_ns, size), mtime seconds, parsed dict)
_CACHE: "dict[str, tuple[tuple[int, int], float, dict]]" = {}
_LOCK = threading.Lock()


class RunFile(NamedTuple):
    path: Path
    mtime: float
    data: dict


def _run_files(workspace: str) -> "list[str]":
    """Paths of every run.json under *workspace*'s projects (scandir, no glob)."""
    out: "list[str]" = []
    projects = os.path.join(workspace, "Projects")
    try:
        project_dirs = list(os.scandir(projects))
    except OSError:
        return out
    for pd in project_dirs:
        runs_dir = os.path.join(pd.path, ".artifacts", "runs")
        try:
            entries = list(os.scandir(runs_dir))
        except OSError:
            continue
        for rd in entries:
            if rd.is_dir(follow_symlinks=False):
                out.append(os.path.join(rd.path, "run.json"))
    return out


def runs(workspace: "str | Path") -> "list[RunFile]":
    """Every run.json under *workspace* (module docstring)."""
    ws = str(workspace)
    prefix = os.path.join(ws, "")
    out: "list[RunFile]" = []
    seen: "set[str]" = set()
    for f in _run_files(ws):
        try:
            st = os.stat(f)
        except OSError:
            continue
        seen.add(f)
        key = (st.st_mtime_ns, st.st_size)
        with _LOCK:
            hit = _CACHE.get(f)
        if hit is not None and hit[0] == key:
            out.append(RunFile(Path(f), hit[1], dict(hit[2])))
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue   # half-written or unreadable: not cached, read again next call
        if not isinstance(data, dict):
            continue
        with _LOCK:
            _CACHE[f] = (key, st.st_mtime, data)
        out.append(RunFile(Path(f), st.st_mtime, dict(data)))
    with _LOCK:
        for gone in [k for k in _CACHE if k.startswith(prefix) and k not in seen]:
            del _CACHE[gone]
    return out


def live_runs(workspace: "str | Path") -> "list[RunFile]":
    """The running / paused runs under *workspace*."""
    return [r for r in runs(workspace) if r.data.get("status") in LIVE_STATUSES]


def _clear_for_tests() -> None:
    with _LOCK:
        _CACHE.clear()
