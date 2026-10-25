"""session_changes — Canvas Changes: what ONE session's own transcript says it changed.

Design: docs/design/canvas-changes-from-turns.md (v4). The session's persisted
messages are the only source; how a change was made (native edit, pipeline worktree,
sub-agent, Bash) is never asked, and no run.json is read.

- **Files changed** — each persisted ``turn_changes`` block (the turn's "N files
  changed" card, core/turn_changes), exactly as recorded: one group per turn.
- **Landed** — the session's landings, read from the stamp ``session_worktree land``
  writes into git (core/session_landings, design
  docs/design/2026-10-25-turn-result-card.md §5.3): newest LANDED_MAX within
  LANDED_DAYS, each checked against git. The transcript is never read for them.

``read`` is ONE blocking call for the ``canvas_changes`` pool. It opens its own
read-only sqlite3 connection (never the shared aiosqlite pool, so no JSON is decoded
on the event loop and no chat reader is borrowed) and reads a newest-first WINDOW of
the session's candidate rows (pages of PAGE_ROWS, at most WINDOW_ROWS / WINDOW_BYTES;
"earlier messages not scanned" past it). Later reads take only the rows whose
transcript rev moved (schema v15 transcript_revs, at most INCR_ROWS / INCR_BYTES);
a deletion, a change older than the window or a bigger increment re-reads the window.
Facts are cached per session in an LRU bounded by bytes (CACHE_BYTES). Landings are
cached per session by the bound checkouts' branch tips (a landing moves a tip), at
most LANDED_TTL_S.

``working_repo`` is the terminal's default location from the same window: the newest
card's first bound-repo gitRoot (docs/design/terminal-locations.md).

Public: ``read``, ``working_repo``, ``row_facts``, ``tree_diff``, ``clear_cache``,
and the limits below.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PAGE_ROWS = 100
WINDOW_ROWS = 400
WINDOW_BYTES = 8 * 1024 * 1024
ROW_MAX_BYTES = 1024 * 1024       # a larger single row is not read ("not scanned")
ROW_MAX_CARDS = 3                 # per row (a row normally holds one card)
CARD_MAX_FILES = 60               # the card's own cap (turn_changes.MAX_FILES)
INCR_ROWS = 100
INCR_BYTES = 2 * 1024 * 1024
MAX_TURNS = 200
CACHE_BYTES = 4 * 1024 * 1024
MAX_SESSIONS = 64
LANDED_MAX = 20
LANDED_DAYS = 30
LANDED_BUDGET_S = 10.0
LANDED_TTL_S = 60.0

#: the card fields Changes shows (the rest of a block row is not kept)
_FILE_FIELDS = ("path", "absolutePath", "repo", "status", "added", "deleted", "binary",
                "diffable", "gitRoot", "start", "end")


# ── extraction ──────────────────────────────────────────────────────────────

def row_facts(row_id: str, created_at: str, content: str) -> Optional[dict]:
    """The Changes facts of one assistant row: its ``turn_changes`` cards; None when
    it has none (or is not a
    block list)."""
    try:
        blocks = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return None
    if not isinstance(blocks, list):
        return None
    cards = []
    for i, blk in enumerate(blocks):
        if not isinstance(blk, dict):
            continue
        if blk.get("type") == "turn_changes" and len(cards) < ROW_MAX_CARDS:
            rows = [f for f in blk.get("files") or [] if isinstance(f, dict) and f.get("path")]
            files = [{k: f.get(k) for k in _FILE_FIELDS} for f in rows[:CARD_MAX_FILES]]
            if files:
                omitted = blk.get("omitted")
                omitted = (omitted if isinstance(omitted, int) and omitted > 0 else 0) + max(0, len(rows) - CARD_MAX_FILES)
                cards.append({"id": f"{row_id}:{i}", "at": created_at, "files": files, "omitted": omitted})
    if not cards:
        return None
    return {"cards": cards}


# ── reading the transcript (own read-only connection, worker thread) ───────────

_CANDIDATE = ("m.session_id = ? AND m.role = 'assistant' AND (m.sent IS NULL OR m.sent != 0) "
              "AND instr(m.content, '\"turn_changes\"') > 0")


def _is_candidate(role: Any, sent: Any, content: Any) -> bool:
    return (role == "assistant" and sent != 0 and isinstance(content, str)
            and '"turn_changes"' in content)


def _connect(db_path: str) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = 1")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _revs(conn: sqlite3.Connection, session_id: str) -> tuple[int, int]:
    try:
        row = conn.execute("SELECT rev, deleted_rev FROM session_revs WHERE session_id = ?",
                           (session_id,)).fetchone()
    except sqlite3.OperationalError:      # a database before schema v15
        return 0, 0
    return (int(row[0]), int(row[1])) if row else (0, 0)


class _Entry:
    """One session's window: the output-bearing rows by row id, the rev it is at, and
    how far back it was read (``boundary``: the oldest scanned row's key; everything
    older is unread unless ``scanned_all``)."""

    __slots__ = ("rev", "rows", "boundary", "scanned_all", "size")

    def __init__(self, rev: int) -> None:
        self.rev = rev
        self.rows: dict[str, dict] = {}
        self.boundary: Optional[tuple[str, int]] = None
        self.scanned_all = False
        self.size = 0

    def put(self, row_id: str, key: tuple[str, int], facts: Optional[dict]) -> None:
        old = self.rows.pop(row_id, None)
        if old is not None:
            self.size -= old["size"]
        if facts is not None:
            size = len(json.dumps(facts, separators=(",", ":")))
            self.rows[row_id] = {"key": key, **facts, "size": size}
            self.size += size

    def trim(self) -> None:
        """Keep the newest WINDOW_ROWS rows within CACHE_BYTES; what is dropped
        becomes "not scanned"."""
        if len(self.rows) <= WINDOW_ROWS and self.size <= CACHE_BYTES:
            return
        ordered = sorted(self.rows.items(), key=lambda kv: kv[1]["key"], reverse=True)
        keep, size = {}, 0
        for rid, r in ordered:
            if len(keep) >= WINDOW_ROWS or (keep and size + r["size"] > CACHE_BYTES):
                break
            keep[rid] = r
            size += r["size"]
        self.rows, self.size, self.scanned_all = keep, size, False
        self.boundary = min(r["key"] for r in keep.values()) if keep else None


def _read_window(conn: sqlite3.Connection, session_id: str, rev: int) -> _Entry:
    """The newest candidate rows, page by page, until WINDOW_ROWS / WINDOW_BYTES. Each
    page lists row lengths first; content is fetched only for the rows the byte budget
    still admits, and a row over ROW_MAX_BYTES is not read (it counts as not scanned)."""
    entry = _Entry(rev)
    rows_read, bytes_read, last, skipped = 0, 0, None, False
    head = "SELECT m.created_at, m.rowid, m.id, length(m.content) FROM messages m WHERE " + _CANDIDATE
    while True:
        if last is None:
            page = conn.execute(head + " ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                                (session_id, PAGE_ROWS)).fetchall()
        else:
            page = conn.execute(head + " AND (m.created_at < ? OR (m.created_at = ? AND m.rowid < ?)) "
                                "ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?",
                                (session_id, last[0], last[0], last[1], PAGE_ROWS)).fetchall()
        take: list[tuple[str, int, str]] = []
        budget_hit = False
        for created_at, rowid, row_id, length in page:
            length = int(length or 0)
            if rows_read + len(take) >= WINDOW_ROWS or (rows_read or take) and bytes_read + length > WINDOW_BYTES:
                budget_hit = True
                break
            last = (str(created_at), int(rowid))
            if length > ROW_MAX_BYTES:
                skipped = True
                continue
            take.append((last[0], last[1], row_id))
            bytes_read += length
        if take:
            marks = ",".join("?" for _ in take)
            contents = dict(conn.execute(f"SELECT id, content FROM messages WHERE id IN ({marks})",
                                         [t[2] for t in take]).fetchall())
            for created_at, rowid, row_id in take:
                entry.put(row_id, (created_at, rowid), row_facts(row_id, created_at, contents.get(row_id)))
            rows_read += len(take)
        if budget_hit:
            break
        if len(page) < PAGE_ROWS:
            entry.scanned_all = not skipped
            break
    entry.boundary = last
    entry.trim()
    return entry


def _apply_increment(conn: sqlite3.Connection, session_id: str, entry: _Entry, rev: int) -> bool:
    """Bring *entry* to *rev* from the rows whose rev moved; False when the window has
    to be read again (too many / too large, or a change older than the window)."""
    heads = conn.execute(
        "SELECT r.row_id, length(m.content) FROM transcript_revs r JOIN messages m ON m.id = r.row_id "
        "WHERE r.session_id = ? AND r.kind = 'message' AND r.rev > ? LIMIT ?",
        (session_id, entry.rev, INCR_ROWS + 1)).fetchall()
    if len(heads) > INCR_ROWS or sum(int(h[1] or 0) for h in heads) > INCR_BYTES:
        return False
    large = {h[0] for h in heads if int(h[1] or 0) > ROW_MAX_BYTES}
    ids = [h[0] for h in heads if h[0] not in large]
    for row_id in large:               # not read: its old facts go, and it counts as not scanned
        entry.put(row_id, (), None)
        entry.scanned_all = False
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT m.created_at, m.rowid, m.id, m.role, m.sent, m.content FROM messages m "
            f"WHERE m.id IN ({marks})", ids).fetchall()
        for created_at, rowid, row_id, role, sent, content in rows:
            key = (str(created_at), int(rowid))
            if not (entry.scanned_all or entry.boundary is None or key >= entry.boundary):
                if _is_candidate(role, sent, content) or row_id in entry.rows:
                    return False          # older than the window: its old facts are unknown
                continue
            facts = row_facts(row_id, key[0], content) if _is_candidate(role, sent, content) else None
            entry.put(row_id, key, facts)
    entry.rev = rev
    entry.trim()
    return True


_SESSIONS: "OrderedDict[str, _Entry]" = OrderedDict()
_SESSIONS_LOCK = threading.Lock()       # the LRU and _LOCKS only, held briefly
_LOCKS: dict[str, threading.Lock] = {}  # one per session: its entry's only mutator


def _session_lock(session_id: str) -> threading.Lock:
    with _SESSIONS_LOCK:
        lock = _LOCKS.get(session_id)
        if lock is None:
            if len(_LOCKS) > 4 * MAX_SESSIONS:   # drop the idle locks of uncached sessions
                for sid in [s for s, lk in _LOCKS.items() if s not in _SESSIONS and not lk.locked()]:
                    del _LOCKS[sid]
            lock = _LOCKS[session_id] = threading.Lock()
        return lock


def _window(session_id: str, db_path: str) -> tuple[list[dict], bool]:
    """A snapshot of the session's current window: its rows newest first (copies) and
    whether everything was scanned. Reads of one session are serialized by its lock;
    other sessions never wait on them."""
    with _session_lock(session_id):
        with _SESSIONS_LOCK:
            entry = _SESSIONS.get(session_id)
        conn = _connect(db_path)
        try:
            rev, deleted = _revs(conn, session_id)
            fresh = entry is not None and entry.rev == rev and deleted <= entry.rev
            if not fresh and (entry is None or deleted > entry.rev
                              or not _apply_increment(conn, session_id, entry, rev)):
                entry = _read_window(conn, session_id, rev)
        finally:
            conn.close()
        rows = sorted((dict(r) for r in entry.rows.values()), key=lambda r: r["key"], reverse=True)
        scanned_all = entry.scanned_all
        with _SESSIONS_LOCK:
            _SESSIONS[session_id] = entry
            _SESSIONS.move_to_end(session_id)
            total = sum(e.size for e in _SESSIONS.values())
            while len(_SESSIONS) > 1 and (total > CACHE_BYTES or len(_SESSIONS) > MAX_SESSIONS):
                _, gone = _SESSIONS.popitem(last=False)
                total -= gone.size
        return rows, scanned_all


def clear_cache() -> None:
    """Drop every cached window and landing read (tests; a session's delete)."""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()
        _LOCKS.clear()
    with _LANDED_LOCK:
        _LANDED.clear()


# ── landings (core/session_landings) ──────────────────────────────────────────

_LANDED_LOCK = threading.Lock()
#: session id -> (bound checkouts' branch tips, when, result)
_LANDED: "OrderedDict[str, tuple[tuple, float, dict]]" = OrderedDict()


def _tips() -> tuple:
    """((checkout, HEAD ref, its commit), ...) of the bound checkouts: a landing moves
    one, so an unchanged tuple means no new landing. Read-only file reads, no git."""
    from core import session_landings
    out = []
    for root in session_landings._bound_checkouts():
        try:
            head = Path(root, ".git", "HEAD").read_text().strip()
            ref = head[len("ref: "):] if head.startswith("ref: ") else ""
            tip = Path(root, ".git", ref).read_text().strip() if ref and Path(root, ".git", ref).is_file() else None
        except OSError:
            ref, tip = "", None
        out.append((root, ref, tip))
    return tuple(out)


def _landed(session_id: str) -> dict:
    """``{landed, landedTruncated, unverified, unknown}`` for Canvas, cached per session
    while the bound checkouts' tips stay put (a packed ref reads as None: re-read
    after LANDED_TTL_S at most)."""
    from core import session_landings
    tips, now = _tips(), time.monotonic()
    with _LANDED_LOCK:
        hit = _LANDED.get(session_id)
        if hit and hit[0] == tips and now - hit[1] < LANDED_TTL_S:
            return hit[2]
    res = session_landings.read_session(
        session_id, deadline=session_landings.Deadline(LANDED_BUDGET_S),
        cap=session_landings.CANVAS_CAP, since_days=LANDED_DAYS, limit=LANDED_MAX)
    out = {"landed": res["landed"], "landedTruncated": res["truncated"],
           "unverified": res["unverified"], "unknown": res["unknown"]}
    with _LANDED_LOCK:
        _LANDED[session_id] = (tips, now, out)
        _LANDED.move_to_end(session_id)
        while len(_LANDED) > MAX_SESSIONS:
            _LANDED.popitem(last=False)
    return out


# ── the read ─────────────────────────────────────────────────────────────────

def read(session_id: str, db_path: str) -> dict[str, Any]:
    """The session's Changes: ``{turns, olderTurns, scannedAll, landed,
    landedTruncated, unverified, unknown}`` — turns newest first (each one card:
    ``{id, at, files, omitted}``), landings newest first. Blocking: run it on the
    ``canvas_changes`` pool."""
    rows, scanned_all = _window(session_id, db_path)
    cards = [c for r in rows for c in reversed(r["cards"])]
    return {"turns": cards[:MAX_TURNS], "olderTurns": max(0, len(cards) - MAX_TURNS),
            "scannedAll": scanned_all, **_landed(session_id)}


def working_repo(session_id: str, db_path: str) -> Optional[dict]:
    """The repo this session is coding in (the terminal's default location): the
    ``gitRoot`` of the first file, newest card first, that is a bound repo's main
    checkout (repo_changes.authorized_root, ``.git`` a directory). A card's gitRoot is
    already its store's main checkout (turn_changes._main_checkout), so a change made
    in a worktree names its main. A file outside a bound repo (SwarmWS, a scratch
    clone) is skipped; nothing found is None. ``{path, name}``. Blocking, the same
    cached window as ``read``; git only via the cached repo identities."""
    from core.repo_changes import authorized_root
    rows, _ = _window(session_id, db_path)
    seen: set[str] = set()
    for r in rows:
        for c in reversed(r["cards"]):
            for f in c["files"]:
                root = f.get("gitRoot")
                if not isinstance(root, str) or not root or root in seen:
                    continue
                seen.add(root)
                real = authorized_root(root)
                if real and os.path.isdir(os.path.join(real, ".git")):
                    return {"path": real, "name": os.path.basename(real)}
    return None


def tree_diff(root: str, start: str, end: str, path: str) -> Optional[str]:
    """One file's diff of a turn card, for a root of a bound repo's object store
    (repo_changes.authorized_root: bindings only, never run.json); None when not
    available. Raises turn_changes.DiffUnavailable on a transient failure. Blocking."""
    from core.repo_changes import authorized_root
    from core.turn_changes import _valid_diff_args, diff_trees
    if not _valid_diff_args(start, end, path):       # before any git work
        return None
    real = authorized_root(root)
    return diff_trees(real, start, end, path) if real else None
