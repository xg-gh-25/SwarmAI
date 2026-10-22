"""main_dirty_scan — PostToolUse(Bash) reporter: main-checkout files a Bash call newly
changed while this session's run is isolated (run_3b9b1515).

The worktree write gate parses redirects, heredocs and sed-style targets only, so an
interpreter script (``python x.py``, ``node``) can still write into the main checkout of
an isolated run's repo. Such an edit has no owner: landing treats it as another
session's. After each Bash call this hook lists each of the run's main checkouts
(``run_worktree.main_dirty``) and tells the agent which files changed since the last
call, with the Pipeline v1 remedy (re-apply in the worktree; no claim command) — marked as not proven to be this session's
(another session may write main at the same time). It never moves anything.

State: ``<worktree ledger>/<run id>/main-dirty-seen.json`` = {main: {path: [status,
size, mtime_ns]}}, the dirty files of each main checkout as last seen. It lives in the
ledger, outside every repo: in the run dir (tracked in SwarmWS) the daemon would commit
other sessions' paths. Worktree creation writes it (``record_baseline``, the moment
those edits are reported) and the hook rewrites it after a scan finished within its
wait (never from a scan that timed out), synchronously right before it returns the
report, with no await in between: the chain's per-hook timeout cannot cancel the hook
after the save and before the report, so a change is never marked seen without its
report. A daemon restart or a new session process loses nothing: the next Bash call is
compared with the last state seen. Scans of all sessions queue on the reporter's own
single worker thread, at most one in flight per session; a slow scan delays the others,
whose 3s wait then times out without a report (the change stays unseen and is reported
by a later call). The remedy names no file: the agent acts only on those this
session wrote. A main with no entry
yet (a run created before this file existed) is baselined without a report. A run with
no active worktree (not isolated, landed, released) is not scanned; a session with no
live isolated run touched in the last 3 days returns after a stat-only scan
(worktree_write_gate._session_runs_light). Bounded by
``_SCAN_TIMEOUT`` and fail-open: on any error the Bash result is returned unchanged and
nothing is reported.

Public: ``create_main_dirty_reporter``, ``signatures``, ``signatures_of``,
``record_baseline``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCAN_TIMEOUT = 3.0
MAX_LISTED = 20
SEEN_FILE = "main-dirty-seen.json"


def _signature(main: str, row: dict) -> list:
    try:
        st = os.lstat(os.path.join(main, row["path"]))
        return [row["status"], st.st_size, st.st_mtime_ns]
    except OSError:
        return [row["status"], None, None]


def signatures_of(main: "str | Path", rows: "list[dict]") -> "dict[str, list]":
    """{path: [status, size, mtime_ns]} of run_worktree.main_dirty *rows* of *main*."""
    m = os.path.realpath(str(main))
    return {r["path"]: _signature(m, r) for r in rows}


def signatures(main: "str | Path") -> "dict[str, list]":
    """signatures_of every uncommitted change in *main* (git errors raise
    run_worktree.WorktreeError)."""
    from core import run_worktree as rw
    return signatures_of(main, rw.main_dirty(os.path.realpath(str(main))))


def _seen_path(run_dir: Path) -> Path:
    """The seen-file of the run whose run dir is *run_dir* (module docstring: ledger)."""
    from core import run_worktree as rw
    return rw.ledger_root() / Path(run_dir).name / SEEN_FILE


def _load_seen(run_dir: Path) -> dict:
    try:
        d = json.loads(_seen_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_seen(run_dir: Path, seen: dict) -> None:
    from core.run_lock import atomic_write_json
    p = _seen_path(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, seen)


def record_baseline(run_dir: "str | Path", main: "str | Path", rows: "list[dict]") -> None:
    """Set *main*'s entry in the run's seen-file to *rows* (run_worktree.main_dirty, the
    list just reported when a worktree was created). Best effort."""
    run_dir, m = Path(run_dir), os.path.realpath(str(main))
    try:
        seen = _load_seen(run_dir)
        seen[m] = signatures_of(m, rows)
        _save_seen(run_dir, seen)
    except Exception:  # noqa: BLE001 — a missing baseline only costs one silent scan
        logger.warning("main_dirty_scan: baseline not recorded for %s", m, exc_info=True)



_POOL: "list" = []


def _scan_pool():
    """The reporter's own one-thread executor (created on first use)."""
    if not _POOL:
        from concurrent.futures import ThreadPoolExecutor
        _POOL.append(ThreadPoolExecutor(max_workers=1, thread_name_prefix="main-dirty-scan"))
    return _POOL[0]


def create_main_dirty_reporter(session_context: "dict[str, Any]"):
    """Factory (module docstring). *session_context*: the mutable dict SessionUnit
    updates; ``sdk_session_id`` holds the app session id (read at call time)."""
    inflight: "list[asyncio.Future]" = []

    def _scan(sid: str) -> "tuple[str | None, Path | None, dict | None]":
        """(report, run dir, new seen-map to save — None: nothing to save). Reads only:
        the hook saves the seen-map once the report is delivered, so a scan that ran
        past the hook's wait never consumes a change unreported."""
        from core import run_worktree as rw
        from core.security_hooks import _pipeline_runs_root
        from core.worktree_write_gate import _session_runs_light
        # no-op path (every Bash call of every session): a stat-only prefilter, so the
        # full run scan below runs only for a session with a live isolated run
        light = {r["id"] for r in _session_runs_light(sid)
                 if r.get("worktree_mode") == "isolated"}
        if not light:
            return None, None, None
        root = _pipeline_runs_root()
        # only the runs the prefilter found: parsing every run.json in the history on
        # each Bash call would grow with history, not with current work
        run = rw.own_isolated_run(sid, root, with_empty=False, ids=light)
        if not run:
            return None, None, None
        run_dir = Path(root) / "Projects" / run["project"] / ".artifacts" / "runs" / run["id"]
        if not run_dir.is_dir():
            return None, None, None
        mains = {}
        for _name, path in run["worktrees"]:
            m = rw.main_of(path)
            if m is not None:
                mains[os.path.realpath(str(m))] = True
        seen = _load_seen(run_dir)
        dirty = False
        changed: "list[str]" = []
        for main in mains:
            now = signatures(main)
            before = seen.get(main)
            if before != now:
                seen[main], dirty = now, True
            if not isinstance(before, dict):
                continue   # no baseline yet: record one, report nothing
            for path, sig in sorted(now.items()):
                if before.get(path) != sig:
                    changed.append(os.path.join(main, path))
        save = seen if dirty else None
        if not changed:
            return None, run_dir, save
        shown = changed[:MAX_LISTED]
        more = (f" (+{len(changed) - len(shown)} more: `git status` in the main checkout)"
                if len(changed) > len(shown) else "")
        return ("Uncommitted files changed in the main checkout of this run's repo since the "
                f"last command (not proven to be this session's): {', '.join(shown)}{more}. "
                "This run's code belongs in its worktree, and landing refuses a main edit that "
                "overlaps the run. " + __import__(
                    "core.worktree_write_gate", fromlist=["V1_MAIN_EDIT_REMEDY"]
                ).V1_MAIN_EDIT_REMEDY), run_dir, save

    async def _hook(input_data: "dict[str, Any]", tool_use_id: "str | None",
                    context: Any) -> "dict[str, Any]":
        if input_data.get("tool_name") != "Bash":
            return {}
        sid = str(session_context.get("sdk_session_id") or "")
        if not sid:
            return {}
        if inflight and not inflight[0].done():
            return {}   # the last scan still runs (slow git): never stack scans
        # its own single worker, not a shared pool: 'subprocess' serves the fail-open
        # safety gates and 'io' the recall/prompt path; a slow scan (big run tree, slow
        # git) must starve neither, and scans of all sessions queue on this one thread
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(loop.run_in_executor(_scan_pool(), _scan, sid))
        inflight[:] = [task]
        try:
            msg, run_dir, save = await asyncio.wait_for(asyncio.shield(task),
                                                        timeout=_SCAN_TIMEOUT)
            if save is not None:
                _save_seen(run_dir, save)   # tiny atomic write, nothing awaited after it (docstring)
        except Exception:  # noqa: BLE001 — incl. TimeoutError: fail-open, report nothing
            logger.debug("main_dirty_scan: scan failed — no report", exc_info=True)
            return {}
        if not msg:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": msg}}

    return _hook
