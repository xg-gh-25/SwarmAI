"""Main-red registry: tests that fail on main (HEAD) before a run touched anything.

A run's selected test that also fails at the HEAD baseline is classified
``pre-existing`` and does not block that run's landing (XG 2026-10-21: it is not the
run's responsibility, fixing it would grow the run without bound). It is recorded here
instead, so the red on main gets its own bug-fixing run rather than being carried
silently by every run that sees it.

Storage: ``<app data>/state/pipeline_agent_audit/main_red.jsonl``, append-only, one event
per line, written under a file lock. The directory is the protected agent-audit store
(``audit_marker_write_gate`` denies agent writes there), so only code writes events:
the test runner (``seen``) and landing (``closed``).

Events::

    {"event": "seen", "nodeid", "head", "run_id", "at", "error"}
    {"event": "closed", "nodeid", "head", "by", "at"}

Public API:

- ``registry_path()``                         — the jsonl path
- ``record_seen(failures, run_id, head)``     — append ``seen`` for pre-existing failures
- ``record_closed(nodeids, head, by)``        — append ``closed`` events
- ``open_reds()``                             — nodeid -> aggregated open entry
- ``close_passing(results, head, by)``        — close open reds that passed in *results*
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "main_red.jsonl"
_ERROR_CHARS = 300


def registry_path() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "state" / "pipeline_agent_audit" / FILE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(events: list[dict]) -> int:
    if not events:
        return 0
    from utils.file_lock import flock_exclusive, flock_unlock
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events)
    fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        flock_exclusive(fd)
        try:
            size = os.fstat(fd).st_size
            if size:
                os.lseek(fd, size - 1, os.SEEK_SET)  # O_APPEND still writes at the end
                if os.read(fd, 1) != b"\n":
                    data = "\n" + data  # a torn last line must not swallow this event
            os.write(fd, data.encode("utf-8"))
        finally:
            flock_unlock(fd)
    finally:
        os.close(fd)
    return len(events)


def _events() -> list[dict]:
    try:
        text = registry_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # a torn line never hides the rest
        if isinstance(e, dict) and isinstance(e.get("nodeid"), str):
            out.append(e)
    return out


def open_reds() -> dict[str, dict]:
    """nodeid -> {nodeid, first_seen, last_seen, head, error, runs}, for every test with
    a ``seen`` event after its last ``closed`` event."""
    reds: dict[str, dict] = {}
    for e in _events():
        n = e["nodeid"]
        if e.get("event") == "closed":
            reds.pop(n, None)
        elif e.get("event") == "seen":
            r = reds.setdefault(n, {"nodeid": n, "first_seen": e.get("at"), "runs": [],
                                    "head": e.get("head"), "error": e.get("error")})
            r["last_seen"] = e.get("at")
            if e.get("run_id") and e["run_id"] not in r["runs"]:
                r["runs"].append(e["run_id"])
    return reds


def record_seen(failures, run_id: "str | None", head: "str | None") -> int:
    """Append a ``seen`` event for every backend ``pre-existing`` failure in *failures*
    (the runner's failure records). Returns how many were written."""
    now, events = _now(), []
    for f in failures or ():
        if not isinstance(f, dict) or f.get("classification") != "pre-existing":
            continue
        n = str(f.get("nodeid") or "")
        if not n.startswith("backend/") or f.get("outcome") == "timeout":
            continue
        lines = str(f.get("summary") or "").strip().splitlines()
        err = next((x[1:].strip() for x in lines if x.startswith("E ")),
                   lines[-1].strip() if lines else "")
        events.append({"event": "seen", "nodeid": n, "head": head, "run_id": run_id,
                       "at": now, "error": err[:_ERROR_CHARS]})
    return _append(events)


def record_closed(nodeids, head: "str | None", by: "str | None") -> int:
    now = _now()
    return _append([{"event": "closed", "nodeid": n, "head": head, "by": by, "at": now}
                    for n in dict.fromkeys(nodeids)])


def close_passing(results, head: "str | None", by: "str | None") -> list[str]:
    """Close every open red that *results* (runner result records of a tree that is now
    main) show as passed. Returns the closed nodeids."""
    reds = open_reds()
    if not reds:
        return []
    passed = [r.get("nodeid") for r in results or () if isinstance(r, dict)
              and r.get("outcome") == "passed" and r.get("nodeid") in reds]
    record_closed(passed, head, by)
    return list(dict.fromkeys(passed))
