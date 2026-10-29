"""A Hive run's record: what one dispatched session produced, sealed once in order on
the Hive and taken back once by the host (design
docs/design/2026-10-29-hive-run-record-and-worker-role.md §4).

**On the Hive** (``Hive/records/<session>/``): ``manifest.jsonl`` holds one line per entry
``{seq, kind, file, sha256, size, at[, key]}``, appended only. An entry's file is written
to a temporary name, fsynced and renamed before its manifest line is appended and
fsynced, all under the directory's lock, so a listed entry is complete and a ``seq`` is
never written twice; a line torn by a crash is cut before the next append. File names
are made of the seq, the kind and (for a bundle) the key the host can recompute
(core/hive_bundle.key), and are recomputed from those whenever a file is opened: nothing
the agent wrote, not even the manifest's ``file``, is a path. ``Hive``, ``Hive/records``
and a session's directory may not be links.

- ``bundle``: one repository's commits of a turn, sealed by the turn's coroutine once the
  bundling thread returned (core/hive_bundle.make leaves a temporary file; a result that
  arrives after the turn gave up on it is never sealed). A turn that did not move
  ``base..head`` seals none (``sealed_bundle``).
- ``turn``: the rows of the turn that just ended (``turn_slice``: after the last row an
  earlier turn entry holds, up to the turn's end time), as a projection (``turn_projection``): only the
  blocks ``swarm_out_results.summarize`` reads, string fields capped. ``first_turn`` says
  the slice starts at the session's beginning. Sealed after every turn end, in a task of
  its own (core/session_router), in turn order; a turn whose rows could not be read
  seals ``{"error": ...}`` so the gap is visible.
- ``lessons``: the session's lesson units not sealed before (core/hive_lessons.collect),
  sealed when its session job ends (done or failed, also failed by a restart), when a
  pipeline it created lands, and when the session is deleted.
- ``closed``: right after the lessons of a session job's end or a deletion: what the
  session produced so far is all here. A later turn appends after it.

Served read-only by ``GET /api/hive/records/{session}`` (with ``last``, the newest seq)
and ``.../{seq}`` (routers/hive_brains). ``sweep`` removes records untouched for
``RETENTION_DAYS``.

**On the host** (``swarm-out/records/<dispatch>/``): ``pull`` asks for the entries after
its cursor (the listing read up to ``LISTING_MAX``), checks each one once (the next seq,
a known kind, its size cap, sha256 of the bytes against the manifest line; a file is read
up to the size listed) and keeps it before moving the cursor; it then deletes the kept
bundles the newest outcome no longer refers to (``_prune_bundles``); a failed check stops the pull, keeps what is kept, and the next
check retries. The manifest is not trusted either: it can leave entries out, but an entry
it adds still has to pass, and a run keeps at most ``MAX_KEPT_ENTRIES`` entries and
``MAX_KEPT_BYTES`` bytes on disk. The time kept is this machine's. After a pull every reader
reads the local copy only, within its own read cap: ``turn_rows`` (the result summary;
``complete`` only when the record starts at the session's first turn), ``bundle_path``
(core/hive_return), ``lesson_units`` (core/hive_lessons). ``sweep_host`` removes kept
records untouched for ``RETENTION_DAYS``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ROOT = ("Hive", "records")
MANIFEST = "manifest.jsonl"
KINDS = ("bundle", "turn", "lessons", "closed")
MAX_BYTES = {"bundle": 100 * 1024 * 1024, "turn": 1024 * 1024, "lessons": 512 * 1024, "closed": 4096}
PAGE = 100
RETENTION_DAYS = 30
TMP_STALE_S = 24 * 3600
TURN_ROWS = 400                   # rows read back to find a turn's start
TEXT_MAX = 200_000                # an assistant text block or a command, in a turn projection
FIELD_MAX = 2_000                 # any other string field, in a turn projection
SEALED_LESSONS = "sealed-lessons.json"
SEALED_TURNS = "sealed-turns.json"         # {"last_id": the last row a turn entry holds}
SEALED_BUNDLES = "sealed-bundles.json"     # {key: {base, head, seq, sha256, size}}
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{8,80}")
KEY_RE = re.compile(r"[0-9a-f]{16}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_READ_BLOCKS = frozenset({"turn_outcome", "turn_stop", "turn_notice", "text", "turn_deliverables",
                          "turn_changes", "tool_use", "tool_result", "cmd_permission_request",
                          "ask_user_question", "pipeline_summary"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name(seq: int, kind: str, key: Optional[str] = None) -> str:
    return f"{seq:06d}-{kind}" + (f"-{key}.bundle" if kind == "bundle" else ".json")


def _entry_name(e: dict) -> Optional[str]:
    """The file name of a listed entry, recomputed from its seq, kind and key."""
    seq, kind, key = e.get("seq"), e.get("kind"), e.get("key")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or kind not in KINDS:
        return None
    if kind == "bundle" and not (isinstance(key, str) and KEY_RE.fullmatch(key)):
        return None
    return _name(seq, kind, key if kind == "bundle" else None)


def _repair_tail(d: Path) -> None:
    """Cut a torn last line (a crash mid-append) before the next append, so it never
    joins the next line and hides every entry after it."""
    p = d / MANIFEST
    try:
        with open(p, "rb+") as f:
            data = f.read()
            if data and not data.endswith(b"\n"):
                f.truncate(data.rfind(b"\n") + 1)
                f.flush()
                os.fsync(f.fileno())
    except FileNotFoundError:
        return


def _read_manifest(d: Path) -> list[dict]:
    try:
        text = (d / MANIFEST).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            break                  # a torn line ends the list
        if isinstance(e, dict) and isinstance(e.get("seq"), int):
            out.append(e)
    return out


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _append_line(d: Path, entry: dict) -> None:
    _repair_tail(d)
    with open(d / MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


class _Lock:
    def __init__(self, d: Path, name: str = ".lock") -> None:
        self.path = d / name

    def __enter__(self):
        from utils.file_lock import flock_exclusive
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")  # noqa: SIM115
        flock_exclusive(self.f)
        return self

    def __exit__(self, *exc):
        from utils.file_lock import flock_unlock
        try:
            flock_unlock(self.f)
        finally:
            self.f.close()


# ── Hive side ──────────────────────────────────────────────────────────────────

def session_dir(workspace: Path, session_id: str) -> Path:
    """A session's record directory; ValueError for a bad id or a linked path."""
    if not SESSION_RE.fullmatch(session_id or ""):
        raise ValueError("not a session id")
    ws = Path(workspace)
    d = ws.joinpath(*ROOT, session_id)
    for p in (ws / ROOT[0], ws.joinpath(*ROOT), d):
        if p.is_symlink():
            raise ValueError("a record path is a link")
    return d


def tmp_path(workspace: Path, session_id: str, key: str) -> Path:
    """Where a bundle is written before the turn seals it."""
    d = session_dir(workspace, session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f".tmp-{key}-{secrets.token_hex(6)}"


def append(workspace: Path, session_id: str, kind: str, *, data: Optional[bytes] = None,
           src: Optional[Path] = None, key: Optional[str] = None) -> dict:
    """Seal one entry: ``data`` bytes, or the temporary file ``src`` (moved, not copied)."""
    if kind not in KINDS or (kind == "bundle") != (key is not None):
        raise ValueError("not an entry kind")
    if key is not None and not KEY_RE.fullmatch(key):
        raise ValueError("not a bundle key")
    d = session_dir(workspace, session_id)
    with _Lock(d):
        _repair_tail(d)
        entries = _read_manifest(d)
        seq = (entries[-1]["seq"] if entries else 0) + 1
        name = _name(seq, kind, key)
        target = d / name
        if src is not None:
            src = Path(src)
            if src.parent.resolve() != d.resolve() or src.is_symlink() or not src.is_file():
                raise ValueError("a sealed file must be a temporary file of this record")
            h = hashlib.sha256()
            with open(src, "rb") as f:
                os.fsync(f.fileno())
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            size = src.stat().st_size
            os.replace(src, target)
        else:
            body = data or b""
            tmp = d / f".tmp-entry-{secrets.token_hex(6)}"
            with open(tmp, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            h = hashlib.sha256(body)
            size = len(body)
            os.replace(tmp, target)
        if size > MAX_BYTES[kind]:
            target.unlink(missing_ok=True)
            raise ValueError(f"the {kind} entry is larger than a record holds")
        _fsync_dir(d)
        entry = {"seq": seq, "kind": kind, "file": name, "sha256": h.hexdigest(), "size": size,
                 "at": _now(), **({"key": key} if key else {})}
        _append_line(d, entry)
        return entry


def entries(workspace: Path, session_id: str, after: int = 0, limit: int = PAGE) -> dict:
    d = session_dir(workspace, session_id)
    every = _read_manifest(d)
    listed = [e for e in every if e["seq"] > after]
    return {"entries": listed[:limit], "more": len(listed) > limit,
            "last": every[-1]["seq"] if every else 0}


def _entry_path(d: Path, e: Optional[dict]) -> Optional[Path]:
    name = _entry_name(e) if isinstance(e, dict) else None
    return d / name if name else None


def open_entry(workspace: Path, session_id: str, seq: int) -> tuple[int, int]:
    """(fd, size) of one entry's file, opened without following a link; FileNotFoundError
    for anything else."""
    d = session_dir(workspace, session_id)
    e = next((x for x in _read_manifest(d) if x["seq"] == seq), None)
    path = _entry_path(d, e)
    if path is None or e is None:
        raise FileNotFoundError(seq)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size != e.get("size"):
        os.close(fd)
        raise FileNotFoundError(seq)
    return fd, st.st_size


def latest_bundle(workspace: Path, session_id: str, key: str) -> Optional[Path]:
    """The newest sealed bundle of a key (the bundles endpoint an older host still uses)."""
    d = session_dir(workspace, session_id)
    found = [e for e in _read_manifest(d) if e.get("kind") == "bundle" and e.get("key") == key]
    return _entry_path(d, found[-1]) if found else None


def sealed_bundle(workspace: Path, session_id: str, key: str, base: str, head: str) -> Optional[dict]:
    """``{key, seq, sha256, size}`` of the bundle already sealed for exactly ``base..head``
    of repository ``key``, when its file is still there: a turn that made no new commit
    seals no second copy (each bundle holds the whole branch)."""
    d = session_dir(workspace, session_id)
    try:
        got = json.loads((d / SEALED_BUNDLES).read_text(encoding="utf-8")).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(got, dict) or got.get("base") != base or got.get("head") != head:
        return None
    e = next((x for x in _read_manifest(d) if x.get("seq") == got.get("seq")
              and x.get("kind") == "bundle" and x.get("key") == key), None)
    p = _entry_path(d, e) if e else None
    if p is None or not p.is_file() or p.is_symlink():
        return None
    return {"key": key, "seq": e["seq"], "sha256": e["sha256"], "size": e["size"]}


def note_bundle(workspace: Path, session_id: str, key: str, base: str, head: str, seq: int) -> None:
    """Remember which entry holds ``base..head`` of ``key`` (``sealed_bundle``)."""
    d = session_dir(workspace, session_id)
    with _Lock(d, ".bundles.lock"):
        try:
            got = json.loads((d / SEALED_BUNDLES).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
        got = got if isinstance(got, dict) else {}
        got[key] = {"base": base, "head": head, "seq": seq}
        tmp = d / f".tmp-sealed-{secrets.token_hex(4)}"
        tmp.write_text(json.dumps(got), encoding="utf-8")
        os.replace(tmp, d / SEALED_BUNDLES)


def _cap(v: Any, n: int) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + "…"
    if isinstance(v, list):
        return [_cap(x, n) for x in v]
    if isinstance(v, dict):
        return {k: _cap(x, n) for k, x in v.items()}
    return v


def _tool_use(b: dict) -> dict:
    """A tool call as the summary reads it: its command whole (a check is found in it),
    everything else capped."""
    out = _cap({k: v for k, v in b.items() if k not in ("input", "display")}, FIELD_MAX)
    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
    disp = b.get("display") if isinstance(b.get("display"), dict) else {}
    if inp:
        out["input"] = {k: _cap(v, TEXT_MAX if k == "command" else FIELD_MAX) for k, v in inp.items()}
    if disp:
        out["display"] = {k: _cap(v, TEXT_MAX if k == "object" else FIELD_MAX) for k, v in disp.items()}
    return out


def turn_projection(rows: list[dict], *, first_turn: bool = False) -> bytes:
    """The turn's rows, keeping what the host's summary reads: role, id, time, the steer
    marks, and the blocks it counts; a tool result keeps its id and error flag only."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        blocks = []
        for b in r.get("content") if isinstance(r.get("content"), list) else []:
            if not isinstance(b, dict) or b.get("type") not in _READ_BLOCKS:
                continue
            if b["type"] == "tool_result":
                blocks.append({"type": "tool_result", "tool_use_id": b.get("tool_use_id"),
                               "is_error": b.get("is_error") is True})
            elif b["type"] == "text":
                blocks.append({"type": "text", "text": _cap(b.get("text"), TEXT_MAX)})
            elif b["type"] == "tool_use":
                blocks.append(_tool_use(b))
            else:
                blocks.append(_cap(b, FIELD_MAX))
        if isinstance(r.get("content"), str) and r.get("role") == "user":
            blocks.append({"type": "text", "text": _cap(r["content"], FIELD_MAX)})
        out.append({"id": r.get("id"), "role": r.get("role"), "created_at": r.get("created_at"),
                    **({"metadata": {k: True for k in ("steer", "steered") if k in meta}}
                       if any(k in meta for k in ("steer", "steered")) else {}),
                    "content": blocks})
    head = {"first_turn": first_turn}
    body = json.dumps({**head, "rows": out}, ensure_ascii=False).encode("utf-8")
    while len(body) > MAX_BYTES["turn"] and out:
        # too large: the earliest rows of the turn go first (the end says how it ended)
        out.pop(0)
        body = json.dumps({**head, "rows": out, "truncated": True}, ensure_ascii=False).encode("utf-8")
    return body


def _at(v: Any) -> Optional[datetime]:
    """A row time as the messages table writes it (naive local, datetime.now()); an aware
    one is turned into local time the same way."""
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def turn_slice(rows: list[dict], *, after_id: Optional[str] = None,
               until: Optional[str] = None) -> tuple[list[dict], bool]:
    """(the rows of the turn that just ended, whether they start the session).

    The turn starts after ``after_id``, the last row an earlier ``turn`` entry holds
    (``SEALED_TURNS``); with none, at the oldest row read. Rows created after ``until``
    (the turn's end) belong to a later turn. A turn longer than the rows read back
    (``TURN_ROWS``) starts at the oldest one read and is not the session's first."""
    end = _at(until) if until else None
    if end is not None:
        rows = [r for r in rows if not isinstance(r, dict) or (_at(r.get("created_at")) or end) <= end]
    ids = [r.get("id") if isinstance(r, dict) else None for r in rows]
    if after_id is not None and after_id in ids:
        return rows[len(ids) - ids[::-1].index(after_id):], False
    return rows, after_id is None and len(rows) < TURN_ROWS


def _sealed_through(d: Path) -> Optional[str]:
    try:
        v = json.loads((d / SEALED_TURNS).read_text(encoding="utf-8")).get("last_id")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) else None


def seal_turn(workspace: Path, session_id: str, rows: Optional[list[dict]],
              error: Optional[str] = None, *, until: Optional[str] = None) -> Optional[dict]:
    """Seal the turn that just ended (``turn_slice``) and move ``SEALED_TURNS`` to its last
    row. Rows that could not be read seal ``{"error": ...}`` and leave the marker, so the
    next turn's entry holds them. Never raises."""
    try:
        d = session_dir(workspace, session_id)
        with _Lock(d, ".turn.lock"):      # not the entry lock: append takes that one inside
            if rows is None:
                return append(workspace, session_id, "turn",
                              data=json.dumps({"rows": [], "error": error or "rows unreadable"}).encode())
            sliced, first = turn_slice(rows, after_id=_sealed_through(d), until=until)
            entry = append(workspace, session_id, "turn", data=turn_projection(sliced, first_turn=first))
            last = next((r.get("id") for r in reversed(sliced)
                         if isinstance(r, dict) and isinstance(r.get("id"), str)), None)
            if last is not None:
                tmp = d / f".tmp-sealed-{secrets.token_hex(4)}"
                tmp.write_text(json.dumps({"last_id": last}), encoding="utf-8")
                os.replace(tmp, d / SEALED_TURNS)
            return entry
    except Exception as exc:  # noqa: BLE001 — the record never breaks the turn
        logger.error("hive_records: turn of %s not sealed (%s)", session_id, type(exc).__name__)
        try:
            return append(workspace, session_id, "turn",
                          data=json.dumps({"rows": [], "error": type(exc).__name__}).encode())
        except Exception:  # noqa: BLE001
            return None


def seal_lessons(workspace: Path, session_id: str) -> Optional[dict]:
    """Seal the session's lesson units not sealed before; None when there are none."""
    from core import hive_lessons
    d = session_dir(workspace, session_id)
    with _Lock(d, ".lessons.lock"):     # not the entry lock: append takes that one inside
        try:
            sealed = set(json.loads((d / SEALED_LESSONS).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            sealed = set()
        got = hive_lessons.collect(session_id, exclude=sealed)
        if not got["lessons"]:
            return None
        entry = append(workspace, session_id, "lessons", data=json.dumps(
            {"lessons": [{"source": x["source"], "unit": x["unit"]} for x in got["lessons"]],
             "truncated": got["truncated"]}, ensure_ascii=False).encode("utf-8"))
        tmp = d / f".tmp-sealed-{secrets.token_hex(4)}"
        tmp.write_text(json.dumps(sorted(sealed | {x["id"] for x in got["lessons"]})), encoding="utf-8")
        os.replace(tmp, d / SEALED_LESSONS)
        return entry


def seal_end(workspace: Path, session_id: str, reason: str) -> None:
    """A session job ended or the session was deleted: its lessons, then ``closed``.
    Never raises."""
    try:
        seal_lessons(workspace, session_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("hive_records: lessons of %s not sealed (%s)", session_id, type(exc).__name__)
    try:
        append(workspace, session_id, "closed", data=json.dumps({"reason": reason}).encode())
    except Exception as exc:  # noqa: BLE001
        logger.error("hive_records: %s not closed (%s)", session_id, type(exc).__name__)


def on_hive() -> bool:
    return os.environ.get("SWARMAI_MODE") == "hive"


def _workspace() -> Path:
    from core.project_registry import get_swarmws
    return Path(get_swarmws())


def on_session_job_end(session_id: str, state: str) -> None:
    """The S2/S7 job of a session reached ``done`` or ``failed`` (worker.S2JobExecutor,
    also a job a restart failed). Never raises."""
    try:
        if on_hive() and state in ("done", "failed") and SESSION_RE.fullmatch(session_id or ""):
            seal_end(_workspace(), session_id, "reviewed" if state == "done" else "review_failed")
    except Exception as exc:  # noqa: BLE001 — never stops the session jobs
        logger.error("hive_records: ending %s failed (%s)", session_id, type(exc).__name__)


def on_session_deleted(session_id: str) -> None:
    try:
        if on_hive() and SESSION_RE.fullmatch(session_id or ""):
            seal_end(_workspace(), session_id, "deleted")
    except Exception as exc:  # noqa: BLE001
        logger.error("hive_records: closing deleted %s failed (%s)", session_id, type(exc).__name__)


def on_run_landed(session_id: Optional[str]) -> None:
    """A pipeline run landed: the lessons its REFLECT queued, sealed for its creator session
    (a run that lands after its session's review would otherwise never be sealed)."""
    if not on_hive() or not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id):
        return
    try:
        seal_lessons(_workspace(), session_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("hive_records: run lessons of %s not sealed (%s)", session_id, type(exc).__name__)


def _expire(d: Path, now: float) -> bool:
    """Remove ``d`` when nothing in it changed for RETENTION_DAYS, under its entry lock
    taken without waiting: a record being sealed right now (a session resumed after a
    month) is busy, so it is left for the next sweep."""
    from utils.file_lock import flock_exclusive_nb, flock_unlock
    newest = max((p.lstat().st_mtime for p in d.iterdir()), default=d.lstat().st_mtime)
    if now - newest <= RETENTION_DAYS * 86400:
        return False
    with open(d / ".lock", "a+") as f:
        try:
            flock_exclusive_nb(f)
        except OSError:
            return False
        try:
            newest = max((p.lstat().st_mtime for p in d.iterdir() if p.name != ".lock"),
                         default=d.lstat().st_mtime)
            if now - newest <= RETENTION_DAYS * 86400:
                return False
            shutil.rmtree(d)
            return True
        finally:
            flock_unlock(f)


def _sweep_dirs(root: Path, name_re: "re.Pattern", now: float) -> dict:
    removed = tmp = 0
    if not root.is_dir() or root.is_symlink() or root.parent.is_symlink():
        return {"removed": 0, "tmp": 0}
    for d in root.iterdir():
        if not d.is_dir() or d.is_symlink() or not name_re.fullmatch(d.name):
            continue
        try:
            if _expire(d, now):
                removed += 1
                continue
            for p in d.glob(".tmp-*"):
                if now - p.lstat().st_mtime > TMP_STALE_S:
                    p.unlink(missing_ok=True)
                    tmp += 1
        except OSError as exc:
            logger.warning("hive_records: sweeping %s failed (%s)", d.name, type(exc).__name__)
    return {"removed": removed, "tmp": tmp}


def sweep(workspace: Path, now: Optional[float] = None) -> dict:
    """On a Hive: remove records untouched for RETENTION_DAYS and stale temporary files."""
    return _sweep_dirs(Path(workspace).joinpath(*ROOT), SESSION_RE, time.time() if now is None else now)


# ── host side ──────────────────────────────────────────────────────────────────

DISPATCH_RE = re.compile(r"so_[0-9a-f]{12}")
PULL_PAGES = 5
LISTING_MAX = 256 * 1024
MAX_KEPT_ENTRIES = 2_000
MAX_KEPT_BYTES = 300 * 1024 * 1024
TURN_READ_BYTES = 20 * 1024 * 1024       # turn entries read back for one summary
MAX_UNITS_READ = 200


def _host_root() -> Path:
    from core.swarm_out import _state_dir
    return _state_dir() / "records"


def local_dir(dispatch_id: str) -> Path:
    if not DISPATCH_RE.fullmatch(dispatch_id or ""):
        raise ValueError("not a dispatch id")
    return _host_root() / dispatch_id


def local_entries(dispatch_id: str) -> list[dict]:
    return _read_manifest(local_dir(dispatch_id))


def _read_local(dispatch_id: str, e: dict) -> Any:
    path = _entry_path(local_dir(dispatch_id), e)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def turn_rows(dispatch_id: str) -> tuple[list[dict], bool, bool]:
    """(rows of the kept turns in order, whether the record starts at the session's first
    turn, whether older turns were left out to stay within TURN_READ_BYTES)."""
    turns = [e for e in local_entries(dispatch_id) if e.get("kind") == "turn"]
    chunks: list[list[dict]] = []
    used = 0
    partial = False
    for e in reversed(turns):
        size = e.get("size") if isinstance(e.get("size"), int) else 0
        if used + size > TURN_READ_BYTES and chunks:
            partial = True
            break
        used += size
        body = _read_local(dispatch_id, e)
        if isinstance(body, dict) and isinstance(body.get("rows"), list):
            chunks.append([r for r in body["rows"] if isinstance(r, dict)])
    first = _read_local(dispatch_id, turns[0]) if turns else None
    complete = isinstance(first, dict) and first.get("first_turn") is True
    return [r for c in reversed(chunks) for r in c], complete, partial


def lesson_units(dispatch_id: str) -> list[dict]:
    out: list[dict] = []
    for e in local_entries(dispatch_id):
        if e.get("kind") == "lessons" and len(out) < MAX_UNITS_READ:
            body = _read_local(dispatch_id, e)
            if isinstance(body, dict) and isinstance(body.get("lessons"), list):
                out += [x for x in body["lessons"] if isinstance(x, dict)]
    return out[:MAX_UNITS_READ]


def bundle_path(dispatch_id: str, seq: int, key: str) -> Optional[Path]:
    """The kept bundle entry ``seq`` of repository ``key`` (core/hive_return checks its
    bytes against the fact)."""
    for e in local_entries(dispatch_id):
        if e.get("seq") == seq and e.get("kind") == "bundle" and e.get("key") == key:
            p = _entry_path(local_dir(dispatch_id), e)
            return p if p is not None and p.is_file() and not p.is_symlink() else None
    return None


def state(dispatch_id: str) -> dict:
    """{seq, closed, last_at} of the kept record (``last_at``: when this machine kept the
    newest entry)."""
    es = local_entries(dispatch_id)
    return {"seq": es[-1]["seq"] if es else 0, "closed": bool(es) and es[-1].get("kind") == "closed",
            "last_at": es[-1].get("at") if es else None}


def _valid_entry(e: Any, expect_seq: int) -> Optional[str]:
    if not isinstance(e, dict) or type(e.get("seq")) is not int or e["seq"] != expect_seq:
        return "out_of_order"
    kind = e.get("kind")
    if kind not in KINDS:
        return "unknown_kind"
    size, sha = e.get("size"), e.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_BYTES[kind]:
        return "too_large"
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        return "bad_entry"
    if kind == "bundle" and not (isinstance(e.get("key"), str) and KEY_RE.fullmatch(e["key"])):
        return "bad_entry"
    return None


async def _get_listing(client: Any, path: str, after: int) -> tuple[int, Any]:
    """(status, parsed body or None) of a listing, read up to LISTING_MAX."""
    async with client.stream("GET", path, params={"after": after}) as r:
        if r.status_code != 200:
            return r.status_code, None
        length = r.headers.get("content-length")
        if length and length.isdigit() and int(length) > LISTING_MAX:
            return 200, None
        chunks: list[bytes] = []
        size = 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > LISTING_MAX:
                return 200, None
            chunks.append(chunk)
    try:
        return 200, json.loads(b"".join(chunks))
    except ValueError:
        return 200, None


async def _get_file(client: Any, path: str, cap: int, dest: Path) -> tuple[str, int]:
    """Stream a file to ``dest`` (a temporary path); (sha256, size), or ValueError past ``cap``."""
    h = hashlib.sha256()
    size = 0
    async with client.stream("GET", path) as r:
        if r.status_code != 200:
            raise ValueError(f"http_{r.status_code}")
        with open(dest, "wb") as f:
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > cap:
                    raise ValueError("too_large")
                h.update(chunk)
                f.write(chunk)
            f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())   # up to a bundle's size: off the loop
    return h.hexdigest(), size


def _kept_bytes(d: Path, kept: list[dict]) -> int:
    """Bytes the kept record holds on disk (a pruned bundle no longer counts)."""
    n = 0
    for e in kept:
        p = _entry_path(d, e)
        if p is not None and isinstance(e.get("size"), int) and (e.get("kind") != "bundle" or p.is_file()):
            n += e["size"]
    return n


PRUNE_TURNS_READ = 50     # newest turn entries read back to find the newest outcome


def _referenced(d: Path, es: list[dict]) -> Optional[tuple[int, set]]:
    """(seq of the newest turn entry holding an outcome with ``repos``, the bundle seqs that
    outcome refers to) — the outcome the result summary reads its facts from
    (``swarm_out_results.summarize``); None when no turn read back has one."""
    for e in [x for x in es if x.get("kind") == "turn"][::-1][:PRUNE_TURNS_READ]:
        p = _entry_path(d, e)
        try:
            body = json.loads(p.read_text(encoding="utf-8")) if p is not None else None
        except (OSError, ValueError):
            body = None
        rows = body.get("rows") if isinstance(body, dict) else None
        for r in reversed(rows if isinstance(rows, list) else []):
            content = r.get("content") if isinstance(r, dict) else None
            for b in reversed(content if isinstance(content, list) else []):
                if isinstance(b, dict) and b.get("type") == "turn_outcome" and isinstance(b.get("repos"), list):
                    return e["seq"], {f["bundle"]["seq"] for f in b["repos"] if isinstance(f, dict)
                                      and isinstance(f.get("bundle"), dict)
                                      and type(f["bundle"].get("seq")) is int}
    return None


def _prune_bundles(d: Path) -> int:
    """Delete kept bundles no reader needs. Each bundle holds its repository's whole branch,
    and code is taken back from the bundles the newest outcome with repos refers to
    (core/hive_return), so a bundle sealed before that outcome's turn and not referred to
    by it is superseded. Bundles sealed after it stay (a later turn refers to them). The
    manifest lines stay. Returns how many files were deleted."""
    es = _read_manifest(d)
    got = _referenced(d, es)
    if got is None:
        return 0
    turn_seq, keep = got
    gone = 0
    for e in es:
        if e.get("kind") == "bundle" and e["seq"] < turn_seq and e["seq"] not in keep:
            p = _entry_path(d, e)
            try:
                if p is not None and p.is_file() and not p.is_symlink():
                    p.unlink()
                    gone += 1
            except OSError as exc:
                logger.warning("hive_records: pruning %s failed (%s)", p.name, type(exc).__name__)
    return gone


_UNSUPPORTED = {"supported": False, "seq": 0, "closed": False, "last_at": None, "error": None}


async def pull(row: dict, inst: dict) -> dict:
    """Take the run's new entries from its Hive. Returns ``{supported, seq, closed,
    last_at, error}``; ``supported`` False when nothing is kept here and the Hive has no
    record of the session (an older Hive, no turn ended yet, or no answer)."""
    from core import swarm_out
    did, sid = row.get("dispatch_id") or "", row.get("session_id")
    if not isinstance(sid, str) or not SESSION_RE.fullmatch(sid):
        return dict(_UNSUPPORTED)          # a session a Hive cannot keep a record for
    d = local_dir(did)
    out = {"supported": True, "error": None}
    try:
        async with swarm_out._client(inst) as client:
            for _ in range(PULL_PAGES):
                kept = local_entries(did) if d.is_dir() else []
                cur = kept[-1]["seq"] if kept else 0
                status, body = await _get_listing(client, f"/api/hive/records/{sid}", cur)
                listed = body.get("entries") if isinstance(body, dict) else None
                if not isinstance(listed, list):
                    if not kept:
                        return dict(_UNSUPPORTED)
                    out["error"] = ("record_gone" if status == 404 else
                                    f"http_{status}" if status != 200 else "unexpected_body")
                    break
                last = body.get("last")
                if isinstance(last, int) and not isinstance(last, bool) and last < cur:
                    out["error"] = "record_gone"      # the Hive's record was swept and restarted
                    break
                d.mkdir(parents=True, exist_ok=True)
                used = await asyncio.to_thread(_kept_bytes, d, kept)
                for e in listed[:PAGE]:
                    why = _valid_entry(e, cur + 1)
                    if not why and (len(kept) >= MAX_KEPT_ENTRIES or used + e["size"] > MAX_KEPT_BYTES):
                        why = "record_too_large"
                    if why:
                        out["error"] = why
                        break
                    name = _name(e["seq"], e["kind"], e.get("key"))
                    tmp = d / f".tmp-{secrets.token_hex(6)}"
                    try:
                        sha, size = await _get_file(client, f"/api/hive/records/{sid}/{e['seq']}",
                                                    e["size"], tmp)   # what it listed (≤ the kind's cap)
                        if sha != e["sha256"] or size != e["size"]:
                            raise ValueError("sha_mismatch")
                        os.replace(tmp, d / name)
                    except ValueError as exc:
                        tmp.unlink(missing_ok=True)
                        out["error"] = str(exc)[:40]
                        break
                    except BaseException:
                        tmp.unlink(missing_ok=True)
                        raise
                    entry = {"seq": e["seq"], "kind": e["kind"], "file": name, "sha256": sha,
                             "size": size, "at": _now(), **({"key": e["key"]} if e["kind"] == "bundle" else {})}
                    await asyncio.to_thread(_append_line, d, entry)
                    kept.append(entry)
                    used += size
                    cur = e["seq"]
                if out["error"] or not body.get("more"):
                    break
            if d.is_dir():
                await asyncio.to_thread(_prune_bundles, d)
    except swarm_out.SwarmOutError:
        if not (d.is_dir() and local_entries(did)):
            return dict(_UNSUPPORTED)
        out["error"] = "hive_unusable"
    except Exception as exc:  # noqa: BLE001 — unreachable: keep what is kept, retry next check
        logger.warning("hive_records: pulling %s failed (%s)", did, type(exc).__name__)
        if not (d.is_dir() and local_entries(did)):
            return dict(_UNSUPPORTED)
        out["error"] = "hive_silent"
    return {**out, **state(did)}


def sweep_host(now: Optional[float] = None) -> dict:
    """On this machine: remove kept records untouched for RETENTION_DAYS and stale
    temporary files of interrupted pulls."""
    return _sweep_dirs(_host_root(), DISPATCH_RE, time.time() if now is None else now)


def backstop_passed(last_at: Any, days: int = 14) -> bool:
    """No new entry for ``days``: stop asking even without a ``closed``."""
    try:
        t = datetime.strptime(str(last_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - t > timedelta(days=days)
