"""The Hive backend inside an AgentCore Runtime microVM: one run per microVM, its results
written to S3 as they are sealed (design docs/design/2026-11-20-swarm-out-multi-carrier.md
§6.3-§6.7, decision D3).

The mode is on when ``SWARMAI_CARRIER=agentcore`` and ``SWARMAI_MODE=hive`` (``active``).
The supervisor starts this backend only after the runtime's start call, with
``SWARMAI_AC_SID`` (the runtime session id, also the bridge prefix name),
``SWARMAI_AC_BUCKET``, ``HIVE_USER`` and ``HIVE_PASS_HASH``. Everything here is inert
outside the mode; the EC2 Hive and the desktop behave exactly as before.

What the mode changes, and where:

- ``middleware/hive_auth``: the Basic credential comes in ``X-Swarm-Hive-Auth``
  (``AUTH_HEADER``); AgentCore's session endpoint takes ``Authorization`` for SigV4.
- ``core/swarm_out_bridge``: the Hive name is the session id and the bucket comes from the
  environment (``identity``); IMDS is never read.
- ``core/hive_records``: after each append, ``note_append`` queues the write of the new
  entries, on one export thread of their own (never the caller's, never a shared pool), to
  ``bridge/<sid>/records/<file>`` and their manifest lines to
  ``bridge/<sid>/records/manifest/<seq:06d>.json`` (``record_key``, ``manifest_key``),
  both with ``IfNoneMatch="*"``. One session's record is exported: the first one that
  appends (a microVM runs one dispatched session). Where the export stopped is a cursor
  kept on disk (``_export_path``), so a write that failed, or one lost to a backend
  restart, is simply the next one ``sync_pass`` tries; until then it counts as busy.
- ``bridge/<sid>/heartbeat.json`` (``{"at", "every"}``), rewritten by ``sync_pass`` every
  ``HEARTBEAT_S`` while the backend is busy: the Host takes a round whose heartbeat is older
  than ``every`` + the runtime's idle timeout + its clock margin as ended
  (core/carriers/agentcore ``_ended_in_s3``). A heartbeat not written (a role that does not
  allow it yet) is logged and tried at the next due pass; it never counts as busy.
- Whole objects ``bridge/<sid>/state.json``, ``checks.json`` and ``usage.json``, rewritten
  by ``sync_pass`` (each bridge pass) when what they hold changed. ``checks.json`` is
  ``{"at", "orders": [<GET /api/hive/work/{id}/checks body>, ...]}``; ``usage.json`` is
  ``{"at", "rows", "last_id"}`` (``usage_accounting.rows_after`` over every row).
- ``GET /api/hive/busy`` (routers/hive_brains, ``busy``): busy exactly while a session
  streams or has background work, a permission or question waits, a pipeline run of a
  session here waits on a decision (``core.hive_review.decision_waits``: also ``waiting`` in
  state.json), a session review job is queued or running, or something is not yet written
  to S3 (a record entry, one being sealed, or a stale object). An open work order alone
  is not busy: the Host releases it only after it settled, and counting it would hold the
  microVM to its 8-hour limit.
- The review at the turn's end, with no ``s2_jobs.REVIEW_DELAY``, is core/hive_review's
  (every dispatched Hive session; here every session is the dispatched one); its end
  seals the lessons and ``closed`` (``hive_records.seal_end``), so the run ends in minutes.

Every boto3 client is created inside a function, on first use (``_client``): a client
made at import time would carry the credentials from before the microVM's snapshot,
without the session context key the IAM policy scopes ``bridge/<sid>/`` by.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

CARRIER = "agentcore"
AUTH_HEADER = b"x-swarm-hive-auth"     # lower-case, as ASGI scope headers are
#: an AgentCore runtime session id is 33..256 characters; only these go into S3 keys
SID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{32,255}")
BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
USAGE_PAGE = 2000
#: how often a busy backend rewrites heartbeat.json (core/carriers/agentcore reads it)
HEARTBEAT_S = 180

#: busy reasons, in the order they are reported
REASONS = ("streaming", "background_work", "waiting_input", "review_job", "s3_pending")


def active() -> bool:
    """True only in a Hive backend started by the AgentCore supervisor."""
    return os.environ.get("SWARMAI_CARRIER") == CARRIER and os.environ.get("SWARMAI_MODE") == "hive"


def identity() -> Optional[tuple[str, str, Optional[str]]]:
    """(session id, bucket, region or None) from the supervisor's environment, or None
    when a part is missing or has the wrong shape (they all go into S3 keys)."""
    sid = os.environ.get("SWARMAI_AC_SID", "")
    bucket = os.environ.get("SWARMAI_AC_BUCKET", "")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or None
    if not SID_RE.fullmatch(sid) or not BUCKET_RE.fullmatch(bucket) \
            or (region is not None and not REGION_RE.fullmatch(region)):
        return None
    return sid, bucket, region


def prefix(sid: str) -> str:
    return f"bridge/{sid}/"


def record_key(sid: str, file: str) -> str:
    return f"{prefix(sid)}records/{file}"


def manifest_key(sid: str, seq: int) -> str:
    return f"{prefix(sid)}records/manifest/{seq:06d}.json"


# ── S3 ─────────────────────────────────────────────────────────────────────────

_client_obj: Any = None
_client_lock = threading.Lock()


def _client() -> Any:
    """The S3 client, made on first use (never at import: see the module docstring). Short
    timeouts and few retries: a stalled write must not hold the export lane for minutes
    (what it did not write stays pending and the next pass tries again)."""
    global _client_obj
    with _client_lock:
        if _client_obj is None:
            ident = identity()
            region = ident[2] if ident else None
            from core import aws_account   # the microVM's execution role (the default chain)
            _client_obj = aws_account.boto_session(region).client(
                "s3", region_name=region,
                config=aws_account.client_config(connect_timeout=10, read_timeout=60,
                                                 retries={"max_attempts": 3}))
        return _client_obj


def _error_code(exc: BaseException) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def _put_once(key: str, body: Any) -> None:
    """Write ``key`` unless it exists. An object already there is this same write done
    before (a retry after a lost answer): not an error."""
    ident = identity()
    if ident is None:
        raise RuntimeError("no AgentCore identity")
    try:
        _client().put_object(Bucket=ident[1], Key=key, Body=body, IfNoneMatch="*",
                             ServerSideEncryption="AES256")
    except Exception as exc:
        if _error_code(exc) == "PreconditionFailed":
            return
        raise


def _put(key: str, body: bytes) -> None:
    ident = identity()
    if ident is None:
        raise RuntimeError("no AgentCore identity")
    _client().put_object(Bucket=ident[1], Key=key, Body=body, ContentType="application/json",
                         ServerSideEncryption="AES256")


# ── the export lane ────────────────────────────────────────────────────────────
# Every S3 write runs on this one thread, never in the caller's: an append made while a
# turn ends (a bundle sealed inside the turn's outcome) returns at once, and no pool the
# chat path or the busy probe waits on ever holds an S3 call.

_lane: Optional[ThreadPoolExecutor] = None
_lane_lock = threading.Lock()


def _lane_pool() -> ThreadPoolExecutor:
    global _lane
    with _lane_lock:
        if _lane is None:
            _lane = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hive-agentcore-s3")
        return _lane


def flush(timeout: float = 30.0) -> bool:
    """Wait until the exports queued so far ran (tests; a clean stop)."""
    try:
        _lane_pool().submit(lambda: None).result(timeout)
        return True
    except TimeoutError:                     # still running
        return False


# ── run record export (design §6.4) ────────────────────────────────────────────

_export_lock = threading.Lock()     # one export at a time, in manifest order
_holds = 0                          # record writes in progress (a seal, an export)
_holds_lock = threading.Lock()
_warned_other: set[str] = set()


def _take() -> None:
    global _holds
    with _holds_lock:
        _holds += 1


def _give() -> None:
    global _holds
    with _holds_lock:
        _holds -= 1


@contextlib.contextmanager
def hold() -> Iterator[None]:
    """Count a record write in progress as busy (an entry not yet in S3). A plain counter:
    harmless outside the mode, where nothing reads it."""
    _take()
    try:
        yield
    finally:
        _give()


def _export_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "hive" / "agentcore-export.json"


def _load_cursor() -> dict[str, Any]:
    try:
        got = json.loads(_export_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _save_cursor(cur: dict[str, Any]) -> None:
    p = _export_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".tmp-agentcore-{secrets.token_hex(4)}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(cur))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    from core.hive_records import _fsync_dir
    _fsync_dir(p.parent)


def _workspace() -> Path:
    from core.project_registry import get_swarmws
    return Path(get_swarmws())


def note_append(workspace: Path, session_id: str) -> None:
    """``hive_records.append`` sealed an entry of ``session_id``: queue its export on the
    export lane and return at once. Never raises (the record never fails for S3; what is
    not written stays pending, and busy, until a pass writes it)."""
    if not active():
        return
    _take()                       # busy from now until the queued export ran

    def run() -> None:
        try:
            _export(Path(workspace), session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("hive_agentcore: exporting the record of %s failed (%s)", session_id,
                           type(exc).__name__)
        finally:
            _give()
    try:
        _lane_pool().submit(run)
    except Exception as exc:  # noqa: BLE001 — a lane that cannot take it: the next pass does
        _give()
        logger.warning("hive_agentcore: export of %s not queued (%s)", session_id, type(exc).__name__)


def _record_sessions(workspace: Path) -> list[str]:
    from core import hive_records
    root = Path(workspace).joinpath(*hive_records.ROOT)
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                      and hive_records.SESSION_RE.fullmatch(p.name) and (p / hive_records.MANIFEST).is_file())
    except OSError:
        return []


def _cursor(workspace: Path, session_id: Optional[str] = None,
            save: bool = False) -> tuple[Optional[str], int, list[dict]]:
    """(the exported session, how far it is exported, its manifest entries). The session is
    the one the cursor names, else the one ``session_id`` names (the first that seals),
    else the only session with a record (a cursor lost in a crash). A cursor that does not
    read, or names a seq beyond the manifest, starts again from 0: a write already done is
    refused by ``IfNoneMatch`` and counts as done, so nothing is skipped or doubled."""
    from core import hive_records
    cur = _load_cursor()
    pinned = cur.get("session")
    if not (isinstance(pinned, str) and hive_records.SESSION_RE.fullmatch(pinned)):
        found = _record_sessions(workspace)
        pinned = session_id or (found[0] if len(found) == 1 else None)
        if pinned is None:
            return None, 0, []
        cur = {"session": pinned, "exported": 0}
        if save:
            _save_cursor(cur)
    try:
        entries = [e for e in hive_records._read_manifest(hive_records.session_dir(workspace, pinned))
                   if type(e.get("seq")) is int]
    except ValueError:
        return pinned, 0, []
    done = cur.get("exported")
    last = entries[-1]["seq"] if entries else 0
    if type(done) is not int or not 0 <= done <= last:
        done = 0
    return pinned, done, entries


def _entry_file(workspace: Path, session_id: str, seq: int):
    """The entry's file as the HTTP route serves it (``hive_records.open_entry``: no
    link, a regular file of the listed size, opened without blocking), or None."""
    from core import hive_records
    try:
        fd, _size = hive_records.open_entry(workspace, session_id, seq)
    except (OSError, ValueError):
        return None
    os.set_blocking(fd, True)
    return os.fdopen(fd, "rb")


def _export(workspace: Path, session_id: Optional[str]) -> int:
    """Write every entry after the cursor, file before manifest line, stopping at the
    first failure. Returns how many entries were written."""
    ident = identity()
    if ident is None:
        return 0
    sid = ident[0]
    with _export_lock:
        pinned, done, entries = _cursor(workspace, session_id, save=True)
        if pinned is None:
            return 0
        if session_id and session_id != pinned and session_id not in _warned_other:
            _warned_other.add(session_id)
            logger.warning("hive_agentcore: the record of %s is not exported (this microVM's "
                           "run is %s)", session_id, pinned)
        from core import hive_records
        written = 0
        for e in entries:
            if e["seq"] <= done:
                continue
            name = hive_records._entry_name(e)
            f = _entry_file(workspace, pinned, e["seq"]) if name else None
            if f is None:
                # its manifest line still goes: the Host names the entry it cannot take
                logger.error("hive_agentcore: entry %s of %s has no readable file", e["seq"], pinned)
            else:
                with f:
                    _put_once(record_key(sid, name), f)
            _put_once(manifest_key(sid, e["seq"]), json.dumps(e, sort_keys=True).encode("utf-8"))
            done = e["seq"]
            _save_cursor({"session": pinned, "exported": done})
            written += 1
        return written


def export_now(workspace: Optional[Path] = None) -> int:
    """Write every record entry not yet in S3, in this thread (core/hive_rescue: no
    backend runs, so no export lane is busy). How many were written; raises on S3."""
    with hold():
        return _export(Path(workspace or _workspace()), None)


def export_pending(workspace: Optional[Path] = None) -> bool:
    """A record entry is sealed but not in S3 yet, or a record write is in progress."""
    if _holds > 0:
        return True
    _pinned, done, entries = _cursor(workspace or _workspace())
    return bool(entries) and entries[-1]["seq"] > done


def _closed(workspace: Path, session_id: str) -> bool:
    """The newest entry of the session's record is ``closed``."""
    from core import hive_records
    try:
        entries = hive_records._read_manifest(hive_records.session_dir(workspace, session_id))
    except ValueError:
        return False
    return bool(entries) and entries[-1].get("kind") == "closed"


def _record_closed(workspace: Path) -> bool:
    pinned, _done, _entries = _cursor(workspace)
    return pinned is not None and _closed(workspace, pinned)


# ── whole objects: state, checks, usage ────────────────────────────────────────

#: what each object held when it was last written (a fingerprint, not the body)
_written: dict[str, Any] = {"state.json": None, "checks.json": (), "usage.json": (0, 0)}
#: when heartbeat.json was last written (time.time(); 0: never)
_beat: dict[str, float] = {"at": 0.0}


def _orders(workspace: Path) -> list[str]:
    from core import hive_workspace
    root = hive_workspace._state() / "orders"
    try:
        names = sorted(p.stem for p in root.glob("*.json"))
    except OSError:
        return []
    return [w for w in names if hive_workspace.WORK_ID_RE.fullmatch(w)
            and hive_workspace.load(workspace, w) is not None]


def _checked_orders(workspace: Path) -> list[tuple[str, int, int]]:
    """(work id, size, mtime) of each order's check record that exists: an order no
    check ran for has nothing to write."""
    from core import hive_workspace
    out = []
    for w in _orders(workspace):
        f = hive_workspace.checks_dir(workspace, w) / hive_workspace.CHECKS_FILE
        try:
            st = f.stat()
        except OSError:
            continue
        out.append((w, st.st_size, st.st_mtime_ns))
    return out


def _checks_fingerprint(workspace: Path) -> tuple:
    return tuple(_checked_orders(workspace))


def _checks_body(workspace: Path) -> bytes:
    from core import hive_workspace
    orders = [{"work_id": w, "checks": hive_workspace.checks_record(workspace, w)}
              for w, _, _ in _checked_orders(workspace)]
    return json.dumps({"at": time.time(), "orders": orders}, ensure_ascii=False).encode("utf-8")


def _usage_fingerprint() -> tuple:
    import database
    path = getattr(database.db, "db_path", None)
    if not path or not Path(path).is_file():
        return (0, 0)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        row = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM usage_ledger "
                           "WHERE machine = 'host'").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return (0, 0)
        raise
    finally:
        conn.close()
    return (int(row[0]), int(row[1]))


def _usage_body() -> bytes:
    from core import usage_accounting
    rows: list[dict] = []
    after = 0
    while True:
        page = usage_accounting.rows_after(after, USAGE_PAGE)
        rows += page["rows"]
        after = page["last_id"]
        if not page["more"]:
            break
    return json.dumps({"at": time.time(), "rows": rows, "last_id": after}).encode("utf-8")


# ── busy and state ─────────────────────────────────────────────────────────────

def _router(router: Any = None) -> Any:
    if router is not None:
        return router
    from core import session_registry
    return session_registry.session_router


def _pm(pm: Any = None) -> Any:
    if pm is not None:
        return pm
    from core.permission_manager import permission_manager
    return permission_manager


def live_conditions(router: Any = None, pm: Any = None) -> dict[str, Any]:
    """What the event loop knows (session units, the prompt board, turn seals running),
    and the ids of the sessions here (``sessions``, whose pipeline decisions the worker
    thread reads). Called on the loop: these objects are loop-side."""
    from core.session_unit import SessionState
    r = _router(router)
    units = list(r.list_units()) if r is not None else []
    board = _pm(pm)
    waiting = any(board.open_prompts(s) for s in board.prompt_session_ids()) \
        or any(u.state == SessionState.WAITING_INPUT for u in units)
    try:
        from core.session_router import _SEAL_TASKS
        sealing = any(not t.done() for t in list(_SEAL_TASKS))
    except ImportError:
        sealing = False
    # a turn still open counts: its unit is IDLE once the result arrived, while the turn
    # still writes its outcome, seals its bundles and spawns its record seal
    from core import session_events
    hub = session_events.hub()
    open_turn = any(u.session_id and hub.open_turn_id(u.session_id) is not None for u in units)
    return {"streaming": open_turn or any(u.state == SessionState.STREAMING for u in units),
            "background_work": any(getattr(u, "has_background_work", False) is True for u in units),
            "waiting_input": bool(waiting), "sealing": sealing,
            "sessions": tuple(u.session_id for u in units if u.session_id)}


def _review_open() -> bool:
    """A session review job is queued or running (requested within ``s2_jobs.MAX_WAIT``).
    A store that cannot be read counts as open: an unknown answer keeps the microVM (and
    its results) alive."""
    from core.ingestion import s2_jobs
    try:
        # a job postponed again and again (no model reachable) stops holding the microVM
        # after MAX_WAIT: it would otherwise run to the 8-hour limit
        return s2_jobs.open_jobs(younger_than=s2_jobs.MAX_WAIT) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("hive_agentcore: the session jobs are unreadable (%s)", type(exc).__name__)
        return True


def _state_value(live: dict[str, Any], review: bool, pending: bool, workspace: Path) -> str:
    if live["waiting_input"]:
        return "waiting"
    if live["streaming"] or live["background_work"] or live["sealing"] or review or pending:
        return "running"
    return "ended" if _record_closed(workspace) else "running"


def _decision_waits(live: dict[str, Any], workspace: Path) -> bool:
    """A pipeline run of a session here (a live unit's, or the exported record's after a
    restart) waits on a decision: a wait like a question, so the microVM stays and the Host
    keeps showing it (core/hive_review; an unreadable run store counts as waiting). A
    session whose record is closed waits for nothing here: only a deletion closes it with
    a decision open, and nothing can answer a deleted session."""
    from core import hive_review
    sessions = set(live.get("sessions") or ())
    pinned = _cursor(workspace)[0]
    if pinned:
        sessions.add(pinned)
    return any(hive_review.decision_waits(s) for s in sorted(sessions)
               if not _closed(workspace, s))


def _fingerprints(live: dict[str, Any], workspace: Path) -> dict[str, Any]:
    review = _review_open()
    pending = export_pending(workspace)
    waiting = bool(live["waiting_input"]) or _decision_waits(live, workspace)
    return {"_review": review, "_pending": pending, "_waiting": waiting,
            "state.json": _state_value({**live, "waiting_input": waiting}, review, pending, workspace),
            "checks.json": _checks_fingerprint(workspace),
            "usage.json": _usage_fingerprint()}


def _thread_conditions(live: dict[str, Any], workspace: Path) -> dict[str, bool]:
    try:
        fp = _fingerprints(live, workspace)
    except Exception as exc:  # noqa: BLE001 — unknown: busy, results kept
        logger.warning("hive_agentcore: busy check failed (%s)", type(exc).__name__)
        return {"review_job": True, "s3_pending": True}
    stale = any(fp[k] != _written.get(k) for k in ("state.json", "checks.json", "usage.json"))
    return {"review_job": fp["_review"], "s3_pending": fp["_pending"] or stale or live["sealing"],
            "waiting_input": fp["_waiting"]}


async def busy(router: Any = None, pm: Any = None, workspace: Optional[Path] = None) -> dict[str, Any]:
    """``{"busy", "reasons"}``: busy exactly when one of the four conditions holds (module
    docstring). The file and database reads run in a worker thread."""
    live = live_conditions(router, pm)
    got = await asyncio.to_thread(_thread_conditions, live, workspace or _workspace())
    facts = {**live, **got}
    reasons = [r for r in REASONS if facts.get(r)]
    return {"busy": bool(reasons), "reasons": reasons}


def _heartbeat(live: dict[str, Any], workspace: Path, sid: str) -> None:
    """Rewrite heartbeat.json when it is due and the backend is busy. A failed write is
    logged and never counts as busy (the next due pass tries again)."""
    now = time.time()
    if now - _beat["at"] < HEARTBEAT_S:
        return
    try:
        # a pipeline decision that waits is busy like a question (_decision_waits): without
        # a beat the Host would read the wait as a silent end
        busy_now = any(live.get(k) for k in ("streaming", "background_work", "waiting_input", "sealing")) \
            or _review_open() or export_pending(workspace) or _decision_waits(live, workspace)
    except Exception as exc:  # noqa: BLE001 — unknown: busy (as the busy check counts it)
        logger.warning("hive_agentcore: busy check for the heartbeat failed (%s)", type(exc).__name__)
        busy_now = True
    if not busy_now:
        return
    try:
        _put(prefix(sid) + "heartbeat.json", json.dumps({"at": now, "every": HEARTBEAT_S}).encode())
        _beat["at"] = now
    except Exception as exc:  # noqa: BLE001
        logger.warning("hive_agentcore: writing heartbeat.json failed (%s)", type(exc).__name__)


def _sync(live: dict[str, Any], workspace: Path) -> None:
    """Retry the record export, then write each object whose content changed."""
    ident = identity()
    if ident is None:
        return
    _heartbeat(live, workspace, ident[0])     # first: a long export never delays it
    try:
        with hold():
            _export(workspace, None)
    except Exception as exc:  # noqa: BLE001 — stays pending, the next pass retries
        logger.warning("hive_agentcore: record export failed (%s)", type(exc).__name__)
    sid = ident[0]
    fp = _fingerprints(live, workspace)
    bodies = {"state.json": lambda: json.dumps({"state": fp["state.json"], "at": time.time()}).encode(),
              "checks.json": lambda: _checks_body(workspace),
              "usage.json": _usage_body}
    for name, make in bodies.items():
        if fp[name] == _written.get(name):
            continue
        try:
            _put(prefix(sid) + name, make())
            _written[name] = fp[name]
        except Exception as exc:  # noqa: BLE001 — stays stale (busy), the next pass retries
            logger.warning("hive_agentcore: writing %s failed (%s)", name, type(exc).__name__)


async def sync_pass(router: Any = None, pm: Any = None, workspace: Optional[Path] = None) -> None:
    """One pass of the bridge loop in this mode, its S3 writes on the export lane. Never
    raises."""
    try:
        live = live_conditions(router, pm)
        await asyncio.wrap_future(_lane_pool().submit(_sync, live, workspace or _workspace()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("hive_agentcore: sync pass failed (%s)", type(exc).__name__)


def _reset_for_tests() -> None:
    global _client_obj, _holds
    flush()
    _client_obj = None
    _holds = 0
    _warned_other.clear()
    _written.update({"state.json": None, "checks.json": (), "usage.json": (0, 0)})
    _beat["at"] = 0.0
