"""session_changes — Canvas Changes: what ONE session's own transcript says it changed.

Design: docs/design/canvas-changes-from-turns.md (v4). The session's persisted
messages are the only source; how a change was made (native edit, pipeline worktree,
sub-agent, Bash) is never asked, and no run.json is read.

- **Files changed** — each persisted ``turn_changes`` block (the turn's "N files
  changed" card, core/turn_changes), exactly as recorded: one group per turn.
- **Commits mentioned** — commit ids the session's assistant text names in a markdown
  code span (``code_span_shas``), kept only when git confirms a commit in exactly one
  bound repo (repo_changes.repo_identities: one object store = one repo).

``read`` is ONE blocking call for the ``canvas_changes`` pool. It opens its own
read-only sqlite3 connection (never the shared aiosqlite pool, so no JSON is decoded
on the event loop and no chat reader is borrowed) and reads a newest-first WINDOW of
the session's candidate rows (pages of PAGE_ROWS, at most WINDOW_ROWS / WINDOW_BYTES;
"earlier messages not scanned" past it). Later reads take only the rows whose
transcript rev moved (schema v15 transcript_revs, at most INCR_ROWS / INCR_BYTES);
a deletion, a change older than the window or a bigger increment re-reads the window.
Facts are cached per session in an LRU bounded by bytes (CACHE_BYTES).

Git (commits only; nothing at read for files): one ``cat-file --batch-check`` per
repo for unknown candidates, one ``log --no-walk`` for unknown commits (immutable,
cached by bytes), "pushed" for the newest PUSHED_FOR commits (cached PUSHED_TTL_S).

``working_repo`` is the terminal's default location from the same window: the newest
card's first bound-repo gitRoot (docs/design/terminal-locations.md).

Public: ``read``, ``working_repo``, ``code_span_shas``, ``row_facts``, ``tree_diff``, ``clear_cache``,
and the limits below.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
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
ROW_MAX_SHAS = 20                 # commit candidates kept per row
INCR_ROWS = 100
INCR_BYTES = 2 * 1024 * 1024
MAX_TURNS = 200
MAX_SHAS = 200
CACHE_BYTES = 4 * 1024 * 1024
MAX_SESSIONS = 64
FACTS_BYTES = 2 * 1024 * 1024
MAX_COMMIT_FILES = 200
PUSHED_FOR = 20
PUSHED_TTL_S = 60.0
MISSING_TTL_S = 300.0
PREFIX_TTL_S = 600.0              # an abbreviated id can become ambiguous later
UPSTREAM_MAX = 256
GIT_TIMEOUT_S = 10.0

_HEX = re.compile(r"(?:[0-9a-f]{7,40}|[0-9a-f]{64})")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: the card fields Changes shows (the rest of a block row is not kept)
_FILE_FIELDS = ("path", "absolutePath", "repo", "status", "added", "deleted", "binary",
                "diffable", "gitRoot", "start", "end")


# ── extraction ──────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """*text* without its fenced code blocks (``` / ~~~): only inline spans count."""
    out, fence = [], None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) \
                    and not line.strip()[len(m.group(1)):].strip():
                fence = None
            continue
        out.append(line)
    return "\n".join(out)


def code_span_shas(text: str) -> list[str]:
    """Commit-id candidates of *text*: markdown code spans (CommonMark: a run of N
    backticks closed by the next run of exactly N, an escaped backtick opens
    nothing) whose whole content is 7-40 or 64 hex digits; lower-cased, in order,
    distinct. Fenced code blocks do not count."""
    s = _strip_fences(text or "")
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "\\":
            i += 2
            continue
        if s[i] != "`":
            i += 1
            continue
        j = i
        while j < n and s[j] == "`":
            j += 1
        run = j - i
        k = j
        close = -1
        while k < n:
            if s[k] == "`":
                m = k
                while m < n and s[m] == "`":
                    m += 1
                if m - k == run:
                    close = k
                    break
                k = m
            else:
                k += 1
        if close < 0:
            i = j
            continue
        body = s[j:close]
        if len(body) >= 2 and body[0] == " " and body[-1] == " " and body.strip():
            body = body[1:-1]
        cand = body.lower()
        if _HEX.fullmatch(cand) and cand not in out:
            out.append(cand)
        i = close + run
    return out


def row_facts(row_id: str, created_at: str, content: str) -> Optional[dict]:
    """The Changes facts of one assistant row: its ``turn_changes`` cards and the
    commit candidates of its text blocks; None when it has neither (or is not a
    block list)."""
    try:
        blocks = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return None
    if not isinstance(blocks, list):
        return None
    cards, shas = [], []
    for i, blk in enumerate(blocks):
        if not isinstance(blk, dict):
            continue
        kind = blk.get("type")
        if kind == "turn_changes" and len(cards) < ROW_MAX_CARDS:
            rows = [f for f in blk.get("files") or [] if isinstance(f, dict) and f.get("path")]
            files = [{k: f.get(k) for k in _FILE_FIELDS} for f in rows[:CARD_MAX_FILES]]
            if files:
                omitted = blk.get("omitted")
                omitted = (omitted if isinstance(omitted, int) and omitted > 0 else 0) + max(0, len(rows) - CARD_MAX_FILES)
                cards.append({"id": f"{row_id}:{i}", "at": created_at, "files": files, "omitted": omitted})
        elif kind == "text" and isinstance(blk.get("text"), str):
            for c in code_span_shas(blk["text"]):
                if c not in shas and len(shas) < ROW_MAX_SHAS:
                    shas.append(c)
    if not cards and not shas:
        return None
    return {"cards": cards, "shas": shas}


# ── reading the transcript (own read-only connection, worker thread) ───────────

_CANDIDATE = ("m.session_id = ? AND m.role = 'assistant' AND (m.sent IS NULL OR m.sent != 0) "
              "AND (instr(m.content, '\"turn_changes\"') > 0 OR instr(m.content, '`') > 0)")


def _is_candidate(role: Any, sent: Any, content: Any) -> bool:
    return (role == "assistant" and sent != 0 and isinstance(content, str)
            and ('"turn_changes"' in content or "`" in content))


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
    """Drop every cached window and commit fact (tests; a session's delete)."""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()
        _LOCKS.clear()
    with _GIT_LOCK:
        _FACTS.clear()
        _FACTS_SIZE[0] = 0
        _CONFIRMED.clear()
        _PUSHED.clear()
        _UPSTREAM.clear()


# ── commits (git, object reads only) ─────────────────────────────────────────

_GIT_LOCK = threading.Lock()
#: (store, full sha) -> commit facts (immutable); bounded by bytes. "store" is
#: repo_changes.repo_identities' common dir + inode: a replaced repo is a new store.
_FACTS: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
_FACTS_SIZE = [0]
#: (store, candidate) -> (full sha or None, when): a miss and an abbreviated hit expire
_CONFIRMED: "OrderedDict[tuple[str, str], tuple[Optional[str], float]]" = OrderedDict()
_CONFIRMED_MAX = 4000
#: (store, full sha) -> (pushed, when)
_PUSHED: "OrderedDict[tuple[str, str], tuple[bool, float]]" = OrderedDict()
_PUSHED_MAX = 2000
#: store -> (upstream ref or "", when)
_UPSTREAM: "OrderedDict[str, tuple[str, float]]" = OrderedDict()


def _git(root: str, *args: str, stdin: Optional[str] = None, ok: tuple[int, ...] = (0,)) -> Optional[str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           input=stdin, timeout=GIT_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("session_changes: git %s in %s failed: %s", args[0], root, exc)
        return None
    if r.returncode not in ok:
        logger.debug("session_changes: git %s: %s", args[0], (r.stderr or "").strip()[:200])
        return None
    return r.stdout


def _confirm(ident: dict, cands: list[str]) -> dict[str, str]:
    """{candidate: full sha} for the candidates that are commits of this store."""
    now = time.monotonic()
    common, out, ask = ident["store"], {}, []
    with _GIT_LOCK:
        for c in cands:
            hit = _CONFIRMED.get((common, c))
            # a full id never changes its answer; an abbreviated one can become
            # ambiguous, and a miss can appear (a fetch): both expire
            ttl = None if hit and hit[0] and len(c) in (40, 64) else (PREFIX_TTL_S if hit and hit[0] else MISSING_TTL_S)
            if hit is not None and (ttl is None or now - hit[1] < ttl):
                if hit[0]:
                    out[c] = hit[0]
            else:
                ask.append(c)
    if ask:
        text = _git(ident["root"], "cat-file", "--batch-check=%(objectname) %(objecttype)",
                    stdin="".join(f"{c}\n" for c in ask))
        lines = (text or "").splitlines()
        got: dict[str, Optional[str]] = {}
        if len(lines) == len(ask):
            for c, line in zip(ask, lines):
                parts = line.split()
                full = parts[0].lower() if len(parts) == 2 and parts[1] == "commit" else None
                got[c] = full if full and full.startswith(c) else None
        with _GIT_LOCK:
            for c, full in got.items():
                _CONFIRMED[(common, c)] = (full, now)
                if full:
                    out[c] = full
            while len(_CONFIRMED) > _CONFIRMED_MAX:
                _CONFIRMED.popitem(last=False)
    return out


def _facts(ident: dict, fulls: list[str]) -> dict[str, dict]:
    """{full sha: {sha, short, subject, at, files, omittedFiles}}: one ``git log``
    for the ones not cached."""
    common = ident["store"]
    with _GIT_LOCK:
        want = [s for s in fulls if (common, s) not in _FACTS]
    if want:
        text = _git(ident["root"], "log", "--no-walk=unsorted", "--no-renames",
                    "--format=%x1e%H%x1f%s%x1f%cI", "--numstat", "-z", *want) or ""
        for chunk in text.split("\x1e"):
            head, _, rest = chunk.partition("\0")
            parts = head.split("\x1f")
            if len(parts) != 3:
                continue
            files = []
            for rec in rest.lstrip("\n").split("\0"):
                f = rec.split("\t", 2)
                if len(f) == 3:
                    files.append({"path": f[2], "added": None if f[0] == "-" else int(f[0]),
                                  "deleted": None if f[1] == "-" else int(f[1])})
            facts = {"sha": parts[0], "short": parts[0][:9], "subject": parts[1], "at": parts[2],
                     "files": files[:MAX_COMMIT_FILES],
                     "omittedFiles": max(0, len(files) - MAX_COMMIT_FILES)}
            size = len(json.dumps(facts, separators=(",", ":")))
            with _GIT_LOCK:
                if (common, parts[0]) not in _FACTS:
                    _FACTS[(common, parts[0])] = {**facts, "_size": size}
                    _FACTS_SIZE[0] += size
                while _FACTS and _FACTS_SIZE[0] > FACTS_BYTES:
                    _, gone = _FACTS.popitem(last=False)
                    _FACTS_SIZE[0] -= gone["_size"]
    with _GIT_LOCK:
        out = {}
        for s in fulls:
            f = _FACTS.get((common, s))
            if f is not None:
                _FACTS.move_to_end((common, s))
                out[s] = {k: v for k, v in f.items() if k != "_size"}
        return out


def _pushed(ident: dict, full: str) -> Optional[bool]:
    """Whether the repo's upstream contains *full*; None when there is no upstream
    or git cannot say. Cached PUSHED_TTL_S (true and false)."""
    now = time.monotonic()
    common = ident["store"]
    with _GIT_LOCK:
        hit = _PUSHED.get((common, full))
        if hit is not None and now - hit[1] < PUSHED_TTL_S:
            return hit[0]
        up = _UPSTREAM.get(common)
    if up is None or now - up[1] >= PUSHED_TTL_S:
        ref = (_git(ident["root"], "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "").strip()
        up = (ref, now)
        with _GIT_LOCK:
            _UPSTREAM[common] = up
            _UPSTREAM.move_to_end(common)
            while len(_UPSTREAM) > UPSTREAM_MAX:
                _UPSTREAM.popitem(last=False)
    if not up[0]:
        return None
    rc = _git_rc(ident["root"], "merge-base", "--is-ancestor", full, up[0])
    if rc not in (0, 1):
        return None
    value = rc == 0
    with _GIT_LOCK:
        _PUSHED[(common, full)] = (value, now)
        while len(_PUSHED) > _PUSHED_MAX:
            _PUSHED.popitem(last=False)
    return value


def _git_rc(root: str, *args: str) -> Optional[int]:
    """A git call answered by its exit code; None when it could not run."""
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, timeout=GIT_TIMEOUT_S,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("session_changes: git %s in %s failed: %s", args[0], root, exc)
        return None


# ── the read ─────────────────────────────────────────────────────────────────

def _commits(mentions: list[tuple[str, str]]) -> list[dict]:
    """The candidates ``(sha prefix, latest mention)`` that git confirms as a commit
    of exactly one bound repo, newest mention first, with their facts."""
    if not mentions:
        return []
    from core.repo_changes import repo_identities
    idents = repo_identities()
    cands = [c for c, _ in mentions]
    hits: dict[str, list[tuple[dict, str]]] = {c: [] for c in cands}
    for ident in idents:
        for c, full in _confirm(ident, cands).items():
            hits[c].append((ident, full))
    seen: set[tuple[str, str]] = set()
    chosen: list[tuple[dict, str, str]] = []
    for c, at in mentions:
        if len(hits[c]) != 1:          # in no repo, or in two: left off, never guessed
            continue
        ident, full = hits[c][0]
        if (ident["store"], full) in seen:
            continue
        seen.add((ident["store"], full))
        chosen.append((ident, full, at))
    by_ident: dict[str, list[str]] = {}
    for ident, full, _ in chosen:
        by_ident.setdefault(ident["store"], []).append(full)
    facts: dict[tuple[str, str], dict] = {}
    for ident in idents:
        for full, f in _facts(ident, by_ident.get(ident["store"], [])).items():
            facts[(ident["store"], full)] = f
    out = []
    for n, (ident, full, at) in enumerate(chosen):
        f = facts.get((ident["store"], full))
        if f is None:
            continue
        out.append({**f, "repo": ident["name"], "root": ident["root"], "mentionedAt": at,
                    "pushed": _pushed(ident, full) if n < PUSHED_FOR else None})
    return out


def read(session_id: str, db_path: str) -> dict[str, Any]:
    """The session's Changes: ``{turns, olderTurns, commits, scannedAll}`` — turns
    newest first (each one card: ``{id, at, files, omitted}``), commits by latest
    mention. Blocking: run it on the ``canvas_changes`` pool."""
    rows, scanned_all = _window(session_id, db_path)
    cards = [c for r in rows for c in reversed(r["cards"])]
    mentions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for r in rows:
        for c in r["shas"]:
            if c not in seen and len(mentions) < MAX_SHAS:
                seen.add(c)
                mentions.append((c, r["key"][0]))
    return {"turns": cards[:MAX_TURNS], "olderTurns": max(0, len(cards) - MAX_TURNS),
            "commits": _commits(mentions), "scannedAll": scanned_all}


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
