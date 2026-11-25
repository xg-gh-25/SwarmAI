"""Library mount registry — pointers to external knowledge, never copies.

The mount registry backs the Library overlay's Mounted section. A mount is a
lightweight row {id, scope, path, kind, index_ref, last_synced, health,
enabled, created_at} — a POINTER into the user's disk, indexed IN PLACE by the
engine best suited to its kind (code → per-mount code_intel graph via
index_code_mount; docs → content chunked into the shared Knowledge FTS5 via
index_docs_mount, run_3f837bdd option B1). Both index AT MOUNT TIME — no chat, no
LLM. This module owns the registry (CRUD + source-exists health) + the two indexers.

Design: Knowledge/Designs/2026-08-02-library-mount-points-design.md (Cycle 2).

Deliberate architecture (Gate-1 revision + R25): this store SELF-OWNS its schema
via ensure_table() — the KnowledgeStore(conn) pattern — so it never touches the
CRITICAL 263-caller database/sqlite.py. It takes a sqlite3.Connection, making it
trivially testable with an in-memory DB and decoupled from the app DB lifecycle.

Index-not-warehouse invariant: this store holds a `path` string + a `briefing`
snapshot, never the external content. Health only reports whether the pointer is
still valid; recall reads the LIVE source on a hit.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Open-ended by design (the paradigm grows by adding a kind, never a type).
# Enforced at the write boundary so a bad kind fails loud (fail-closed), not
# silently stored — the registry is a contract, not a scratchpad.
VALID_KINDS = ("code", "docs", "url")

# Workspace-global mount scope (Gate-2 #2): a mount added without a project context
# registers here and is reachable from recall in ANY active project. Per-project
# scoping still works (register under a project name) for a mount that should ONLY
# surface in that project — but the DEFAULT is global, matching the user's mental
# model ("I added this folder to my library" = available everywhere), and avoiding
# the register-scope('SwarmAI') vs recall-scope(active-project) divergence.
GLOBAL_SCOPE = "GLOBAL"

# Health is a source-exists signal, NOT a content-freshness claim: 'fresh' = the
# pointer resolves to a live dir/file; 'missing' = the source is gone (dangling
# reference). 'stale' (source mtime > index) is set by the freshness job, which
# then re-indexes the mount.
VALID_HEALTH = ("fresh", "stale", "missing")


def _add_column(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Add column *name*; another process (the API, a sweep, a child) may have added it
    between our check and this ALTER on the first start after an upgrade."""
    try:
        conn.execute(f"ALTER TABLE library_mounts ADD COLUMN {name} {decl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


# What a docs index writes; bump it with any change to which files it reads or how,
# and every docs mount indexed by a lower version is re-indexed by the next sweep
# (the files already on disk are what the change is for). 1: every UTF-8 text file.
# 2: Office lock files and backup copies are not read (_not_content).
# 3: an HTML or SVG file is indexed as the text it shows (_markup_text).
DOCS_INDEX_VERSION = 3

# A backup copy's suffix: x.bak, x.bak2, x.html.bak-1788432226, x.html.bak-title.
_BACKUP_SUFFIX = re.compile(r"\.bak(?:\d*|[-_.][^/]*)$", re.IGNORECASE)


# Markup indexed as the text a reader sees, not its source: a slide deck written as
# HTML is mostly CSS and script, which filled its chunks and kept recall's relevance
# check (the query's words in the hit text) from finding the slides' own words.
_MARKUP_SUFFIXES = (".html", ".htm", ".xhtml", ".svg")


# A string literal in a script (double, single or back quotes) long enough to hold a
# piece of markup; and the sign that it does (an opening tag).
_SCRIPT_STRINGS = (('"', re.compile(r'"((?:[^"\\\n]|\\.){20,})"')),
                   ("'", re.compile(r"'((?:[^'\\\n]|\\.){20,})'")),
                   ("`", re.compile(r"`([^`]{20,})`")))
_OPENING_TAG = re.compile(r"<[a-zA-Z][a-zA-Z0-9]*[\s>/]")


def _script_markup(script: str) -> list[str]:
    """The markup a script carries in its string literals (a deck that builds its
    slides from ``const SLIDES = [{"html": "<div>..."}]``): each literal decoded (its
    escapes, as JS reads them) and kept only when it starts with a tag. Plain strings
    (names, CSS) are not, and neither is code that happens to sit between two
    unrelated quotes (a minified bundle has thousands of such matches)."""
    import json
    found: list[str] = []
    for quote, pattern in _SCRIPT_STRINGS:
        for m in pattern.finditer(script):
            lit = m.group(1)
            if quote != "`":
                body = lit.replace("\\'", "'").replace('"', '\\"') if quote == "'" else lit
                try:
                    lit = json.loads('"' + body + '"')
                except ValueError:
                    pass
            if _OPENING_TAG.match(lit.lstrip()):
                found.append(lit)
    return found


# Encoded data pasted into a page as text (a base64 image in a saved mail): no
# reader reads it, and one such run filled most of a 4 MB "text".
_ENCODED_RUN = re.compile(r"[A-Za-z0-9+/=_-]{200,}")


def _markup_text(markup: str, *, scripts: bool = True) -> str:
    """The text an HTML or SVG page shows, as markdown-ish plain text.

    Kept: the title (a ``#`` line), text nodes, and h2/h3 as ``##`` / ``###`` lines,
    so ``chunk_markdown`` splits a deck at its slide headings; block elements end a
    line. Dropped: tags and attributes, comments, and what is never shown as text
    (``script``, ``style``, ``noscript``, ``template``, the ``head`` apart from its
    title). Entities are decoded. A script's code is dropped, but markup it carries
    in its string literals (a deck that renders its slides from script), and a page
    held in an ``iframe``'s ``srcdoc`` (a deck of framed slides), are read the same
    way and appended (``scripts``; one level, a nested page's own are not read).
    Runs of encoded data (base64) are dropped. A page with no visible text gives ""
    (not indexed)."""
    from html.parser import HTMLParser

    hidden = {"script", "style", "noscript", "template", "head"}
    blocks = {"p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "section",
              "article", "header", "footer", "nav", "aside", "main", "br", "hr",
              "blockquote", "pre", "figure", "figcaption", "dt", "dd", "h1", "h4",
              "h5", "h6", "text", "tspan", "g"}

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []
            self.title: list[str] = []
            self.skip = 0          # depth inside hidden elements
            self.in_title = False
            self.in_script = False
            self.scripts: list[str] = []
            self.framed: list[str] = []     # srcdoc pages

        def handle_starttag(self, tag, attrs):
            if tag == "iframe":
                self.framed += [v for k, v in attrs if k == "srcdoc" and v]
            if tag == "title":
                self.in_title = True
            elif tag == "script":
                self.in_script = True
                self.skip += 1
            elif tag in hidden:
                self.skip += 1
            elif tag in ("h2", "h3"):
                self.out.append("\n\n" + "#" * int(tag[1]) + " ")
            elif tag in blocks:
                self.out.append("\n")

        def handle_startendtag(self, tag, attrs):
            if tag in blocks:
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag == "title":
                self.in_title = False
            elif tag == "script":
                self.in_script = False
                self.skip = max(0, self.skip - 1)
            elif tag in hidden:
                self.skip = max(0, self.skip - 1)
            elif tag in ("h2", "h3"):
                self.out.append("\n\n")
            elif tag in blocks:
                self.out.append("\n")

        def handle_data(self, data):
            if self.in_script:
                self.scripts.append(data)
            elif self.in_title:
                self.title.append(data)
            elif not self.skip:
                self.out.append(data)

    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 — a malformed page gives what was read so far
        pass
    text = "".join(parser.out)
    if scripts:
        for piece in parser.framed + _script_markup("".join(parser.scripts)):
            text += "\n" + _markup_text(piece, scripts=False)
    text = _ENCODED_RUN.sub(" ", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    body = "\n".join(line for line in lines if line)
    body = re.sub(r"^(#{2,3}) *$\n?", "", body, flags=re.MULTILINE)  # a heading with no text
    body = re.sub(r"\n(#{2,3} )", r"\n\n\1", body)
    if not body.strip():
        return ""
    title = " ".join("".join(parser.title).split())
    return (f"# {title}\n\n" if title else "") + body.strip() + "\n"


def _not_content(name: str) -> bool:
    """A file name no index reads, as content or as a change: a dot entry, an Office
    lock file (``~$deck.pptx``, written while the deck is open), an editor backup
    (``notes.md~``) or a backup copy (``deck.html.bak-1788432226``). A backup repeats
    its original, so in recall it crowds the current version out with older text."""
    return (name.startswith((".", "~$")) or name.endswith("~")
            or _BACKUP_SUFFIX.search(name) is not None)


def _index_started_stamp() -> str:
    """The stamp an index writes when it lands: the moment it STARTED reading, one
    second early (stamps are whole seconds, mtimes are floored to them). A file edited
    while the index ran is then newer than the stamp and the next sweep takes it."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")


class LibraryMounts:
    """CRUD + health for the library mount registry over a sqlite3 connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # Return dict-like rows so callers (tests, the API) read by column name.
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row

    def ensure_table(self) -> None:
        """Create the mount registry table if absent (idempotent)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS library_mounts (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                path TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('code', 'docs', 'url')),
                briefing TEXT DEFAULT '',
                index_ref TEXT,
                last_synced TEXT,
                health TEXT NOT NULL DEFAULT 'fresh' CHECK (health IN ('fresh', 'stale', 'missing')),
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # A scope+path pair is unique — mounting the same dir twice in one scope
        # is a no-op update, not a duplicate row.
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_library_mounts_scope_path "
            "ON library_mounts(scope, path)"
        )
        # When the last index found no content (indexed_empty). A source unchanged
        # since then is not stale: without this an emptied mount would read stale on
        # every sweep and be re-indexed forever. Added to tables made before it.
        # index_version: the DOCS_INDEX_VERSION a docs index was written with.
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(library_mounts)")}
        for name, decl in (("indexed_empty_at", "TEXT"), ("index_version", "INTEGER")):
            if name not in cols:
                _add_column(self._conn, name, decl)
        self._conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add_mount(
        self,
        *,
        scope: str,
        path: str,
        kind: str,
        briefing: str = "",
        index_ref: Optional[str] = None,
    ) -> str:
        """Register a mount. Returns its id. Rejects an unknown kind (fail-closed).

        Health is computed at insert from the live source (a mount whose path
        already doesn't exist lands as 'missing', not a false 'fresh').
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid mount kind {kind!r}; must be one of {VALID_KINDS}")
        mid = uuid.uuid4().hex
        health = self._probe_health(path)
        self._conn.execute(
            "INSERT INTO library_mounts (id, scope, path, kind, briefing, index_ref, health) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, scope, path, kind, briefing, index_ref, health),
        )
        self._conn.commit()
        return mid

    def get_mount(self, mount_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM library_mounts WHERE id = ?", (mount_id,))
        return cur.fetchone()

    def list_mounts(self, scope: Optional[str] = None) -> list[sqlite3.Row]:
        if scope is None:
            cur = self._conn.execute("SELECT * FROM library_mounts ORDER BY created_at")
        else:
            cur = self._conn.execute(
                "SELECT * FROM library_mounts WHERE scope = ? ORDER BY created_at", (scope,)
            )
        return cur.fetchall()

    def delete_mount(self, mount_id: str) -> bool:
        """Unmount (registry row only — never touches the external source). Returns
        True if a row was removed, False if the id was unknown (no-op, not a crash)."""
        cur = self._conn.execute("DELETE FROM library_mounts WHERE id = ?", (mount_id,))
        self._conn.commit()
        # Gate-2 #4 (HIGH): close+evict any cached GraphStore handle for this mount,
        # else a long-lived daemon leaks the sqlite connection across mount churn.
        _invalidate_mount_graph(mount_id)
        # B1 (run_3f837bdd): a docs mount indexed its content into the shared
        # Knowledge FTS5 under mount:<id>/ keys — clear them so recall never hits an
        # orphan chunk of an unmounted dir. Best-effort: a store-open failure must
        # not fail the unmount (the registry row is already gone).
        _remove_mount_knowledge_chunks(mount_id)
        return cur.rowcount > 0

    def set_enabled(self, mount_id: str, enabled: bool) -> bool:
        cur = self._conn.execute(
            "UPDATE library_mounts SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, mount_id),
        )
        self._conn.commit()
        # Gate-2 #4 (HIGH): disabling evicts the cached handle (freed now, re-loaded
        # fresh if re-enabled) — no lingering open connection, no stale-graph serve.
        if not enabled:
            _invalidate_mount_graph(mount_id)
        return cur.rowcount > 0

    def mark_synced(self, mount_id: str, index_ref: Optional[str] = None,
                    synced_at: Optional[str] = None,
                    index_version: Optional[int] = None) -> bool:
        """Record a successful index: stamp last_synced (``synced_at``, the moment the
        indexer started reading, or now) + index_ref, and set health fresh. Called by
        the indexer after a graph is built."""
        cur = self._conn.execute(
            "UPDATE library_mounts SET last_synced = COALESCE(?, datetime('now')), "
            "index_ref = COALESCE(?, index_ref), index_version = COALESCE(?, index_version), "
            "health = 'fresh' WHERE id = ?",
            (synced_at, index_ref, index_version, mount_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def mark_indexed_empty(self, mount_id: str, *, at: Optional[str] = None,
                           recall_lost: bool = False,
                           index_version: Optional[int] = None) -> None:
        """Record that an index found no content (``at``: when it started), so the
        unchanged source reads fresh and is not re-indexed on every sweep.
        ``recall_lost``: the index removed what recall reached (a docs re-index prunes
        every chunk), so last_synced, the UI's "recall reaches it", is cleared too; an
        empty code parse keeps the previous graph and its stamp."""
        self._conn.execute(
            "UPDATE library_mounts SET indexed_empty_at = COALESCE(?, datetime('now')), "
            "index_version = COALESCE(?, index_version), health = 'fresh'"
            + (", last_synced = NULL" if recall_lost else "") + " WHERE id = ?",
            (at, index_version, mount_id),
        )
        self._conn.commit()

    def set_kind(self, mount_id: str, kind: str) -> None:
        """Change a mount's kind (the sweep re-judges it; nobody picks a kind by hand).
        Both index stamps are cleared: no index of the new kind exists yet, so the mount
        reads not-indexed and every sweep tries again until one lands."""
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid mount kind {kind!r}; must be one of {VALID_KINDS}")
        self._conn.execute(
            "UPDATE library_mounts SET kind = ?, last_synced = NULL, indexed_empty_at = NULL "
            "WHERE id = ?", (kind, mount_id))
        self._conn.commit()

    # ── Health ────────────────────────────────────────────────────────────

    def check_health(self, mount_id: str) -> Optional[str]:
        """Re-probe the source and persist+return the health. None if unknown id.

        Precedence (Cycle 7): missing > stale > fresh.
        - 'missing': source deleted/moved — the agent reports "source no longer at
          <path>" rather than failing silently.
        - 'stale': source still exists but was edited AFTER last_synced (its
          briefing/graph is an older snapshot). Recall still lands on the pointer
          (agent reads the LIVE source), so stale only lowers HIT probability, never
          returns wrong content.
        - 'fresh': source exists and is not newer than the last index (or never
          synced yet — nothing to be stale against).
        """
        row = self.get_mount(mount_id)
        if row is None:
            return None
        health = self._probe_health(row["path"], row["last_synced"], row["indexed_empty_at"])
        self._conn.execute(
            "UPDATE library_mounts SET health = ? WHERE id = ?", (health, mount_id)
        )
        self._conn.commit()
        return health

    @staticmethod
    def _probe_health(path: str, last_synced: Optional[str] = None,
                      indexed_empty_at: Optional[str] = None) -> str:
        """Health probe: missing (gone) > stale (edited after index) > fresh.

        `last_synced` is the SQLite ``datetime('now')`` UTC text stamped by
        mark_synced, `indexed_empty_at` the one mark_indexed_empty stamps; the newer
        of the two is the last index. When neither is set (never indexed), a present
        source is 'fresh' — there is no index to be stale against yet."""
        try:
            p = Path(path).expanduser()
            if not p.exists():
                return "missing"
            stamps = [t for t in (_parse_sqlite_utc(x) for x in (last_synced, indexed_empty_at) if x)
                      if t is not None]
            if stamps:
                synced_ts = max(stamps)
                # SQLite datetime('now') is SECOND-granularity; file mtimes are
                # sub-second. Floor the source mtime to whole seconds before the
                # compare so a file written at 12:00:00.7 then synced at "12:00:00"
                # is NOT falsely stale (the .7 fraction the stamp can't represent).
                # A real edit lands a full second later → correctly stale.
                if synced_ts is not None and int(_source_max_mtime(p)) > synced_ts:
                    return "stale"
            return "fresh"
        except OSError:
            return "missing"

    def refresh(self, mount_id: str) -> Optional[str]:
        """Alias for check_health — re-probe + persist one mount's health."""
        return self.check_health(mount_id)

    def is_registered(self, scope: str, path: str) -> bool:
        """True iff `path` is an ENABLED mount registered under `scope`.

        Per-scope + enabled-only by design: a disabled mount (toggle off) or a
        mount registered under a DIFFERENT scope does NOT authorize indexing —
        anything looser is a global allowlist, which reopens run_1950e67e.
        Path match is normalization-tolerant (expanduser + rstrip + resolve) so a
        trailing-slash / ~ difference doesn't silently fail a legit mount, but it
        never widens to a prefix/substring match (that would authorize siblings).
        """
        want = _norm_path(path)
        for row in self.list_mounts(scope=scope):
            if not row["enabled"]:
                continue
            if _norm_path(row["path"]) == want:
                return True
        return False


def _norm_path(path: str) -> str:
    """Canonical path form for registry equality (never a prefix match)."""
    try:
        return str(Path(path.rstrip("/")).expanduser().resolve())
    except OSError:
        return path.rstrip("/")


def _parse_sqlite_utc(text: str) -> Optional[float]:
    """Parse SQLite ``datetime('now')`` text ('YYYY-MM-DD HH:MM:SS', UTC) → epoch
    seconds. Returns None if unparseable (→ caller treats the mount as fresh, never
    a false-stale from a parse error)."""
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _source_max_mtime(root: Path) -> float:
    """Newest mtime across the source (a file → its mtime; a dir → the max over
    its tree, directories included, so a created or deleted entry counts as a change).

    Vendored/build dirs are not walked, and dot-entries, lock files and backup copies
    (``_not_content``) not read: no indexer reads them, and counting them made a mount
    read stale on every ``git fetch``, Finder ``.DS_Store`` rewrite or deck opened in
    PowerPoint, which now starts a re-index. (Creating one still moves its
    directory's mtime: one extra re-index, not a loop.) An mtime in the future (clock
    skew, an extracted archive) is skipped: it would read stale on every sweep. A walk
    error falls back to what was read so far, so a permission hiccup never crashes the
    freshness probe."""
    import os
    import time
    horizon = time.time() + 60
    try:
        if root.is_file():
            return root.stat().st_mtime
        newest = root.stat().st_mtime
        if newest > horizon:
            newest = 0.0
    except OSError:
        return 0.0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [d for d in dirnames if d not in _JUDGE_SKIP_DIRS and not d.startswith(".")]
        for name in dirnames + [f for f in filenames if not _not_content(f)]:
            try:
                m = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue
            if newest < m <= horizon:
                newest = m
    return newest


def refresh_all_mounts(store: "LibraryMounts", scope: Optional[str] = None, *,
                       reindex: bool = False) -> dict:
    """The periodic freshness sweep: re-probe every mount's health, persist it, and
    return a {scanned, fresh, stale, missing[, reindexing, rejudged]} summary.

    With ``reindex`` (the library-freshness job) an enabled mount is also kept
    current: its kind is judged again (a mount registered before the judge changed
    keeps its old kind otherwise), and a mount whose source changed since its last
    index, whose kind changed, or whose code graph an older parser wrote is
    re-indexed in a child. Never raises on one bad mount (best-effort — a broken
    mount is counted, not fatal)."""
    summary = {"scanned": 0, "fresh": 0, "stale": 0, "missing": 0}
    try:
        rows = store.list_mounts(scope=scope)
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_all_mounts: list failed: %s", exc)
        return summary
    for row in rows:
        summary["scanned"] += 1
        try:
            health = store.check_health(row["id"])
        except Exception as exc:  # noqa: BLE001 — one bad mount must not sink the sweep
            logger.debug("refresh_all_mounts: %s probe failed: %s", row["id"], exc)
            continue
        if health in summary:
            summary[health] += 1
        if reindex and health != "missing" and row["enabled"]:
            try:
                _keep_current(store, row, health, summary)
            except Exception as exc:  # noqa: BLE001 — one bad mount must not sink the sweep
                logger.warning("mount %s: keeping it current failed: %s", row["id"], exc)
    return summary


def _keep_current(store: "LibraryMounts", row, health: str, summary: dict) -> None:
    """Re-judge one mount's kind and start a re-index child when its index is behind:
    the source changed since the last index (``stale``), the kind changed, or the index
    was written by an older parser (code) or an older DOCS_INDEX_VERSION (docs) —
    after a deploy that changes either nobody re-mounts (design
    2026-10-29-code-intel-auto D3)."""
    from core.code_intel.build import db_parser_outdated, spawn_child
    mid, kind = row["id"], row["kind"]
    if kind not in ("code", "docs"):
        return
    # stale, or no index has ever landed (a first index that failed, or a kind switch
    # whose index has not landed yet): both are retried until one does
    need = health == "stale" or (row["last_synced"] is None and row["indexed_empty_at"] is None)
    judged = _judge_kind(row["path"])
    if judged is not None and judged != kind:
        # Only on a sure verdict (a marker found, or a full shallow scan without one):
        # an error or a capped scan must not flip a mount and throw its index away.
        # An indexer of the old kind still writing would write after the clear below.
        if _index_running(mid, kind):
            return
        store.set_kind(mid, judged)
        # The old index no longer answers for this mount: recall reads a code graph
        # only for a code mount, and docs chunks are cleared here (the graph file is
        # left on disk; it is rewritten if the mount turns back into code).
        if kind == "docs":
            _remove_mount_knowledge_chunks(mid)
        else:
            _invalidate_mount_graph(mid)
        summary["rejudged"] = summary.get("rejudged", 0) + 1
        kind, need = judged, True
    if kind == "code" and db_parser_outdated(_mount_db_path(mid)):
        need = True
    if kind == "docs" and (row["index_version"] or 0) < DOCS_INDEX_VERSION:
        need = True
    if not need or _index_running(mid, kind):
        return
    try:
        spawn_child(["-m", "core.library_mounts", f"index-{kind}", mid])
    except OSError as exc:
        logger.warning("mount %s: could not start a re-index: %s", mid, exc)
        return
    summary["reindexing"] = summary.get("reindexing", 0) + 1


def _index_lock_path(mount_id: str, kind: str) -> Path:
    """The path whose ``write_lock`` one indexer of this mount holds while it writes."""
    return _mount_db_path(mount_id) if kind == "code" else _mounts_dir() / mount_id / "docs"


def _queue_lock_path(mount_id: str) -> Path:
    """The path whose ``write_lock`` an index child of this mount holds for its whole
    life, from its start: a code child waits up to an hour for its parse turn before it
    takes the write lock, and must be seen while it waits."""
    return _mounts_dir() / mount_id / "queue"


def _index_running(mount_id: str, kind: str) -> bool:
    """True when another process is indexing this mount now or a child of it is
    waiting for its turn, so the sweep neither starts a second child behind it nor
    switches the mount's kind under it."""
    from core.code_intel.build import write_lock
    for path in (_queue_lock_path(mount_id), _index_lock_path(mount_id, kind)):
        with write_lock(path, wait=False) as held:
            if not held:
                return True
    return False


# ── Ownership plan A — parallel predicate + composed oracle ──────────────────
#
# The contamination guard `repo_root_is_owned` (run_1950e67e) stays UNTOUCHED and
# strict at its 3 project-loop sites. Mount indexing (Cycle 4) uses the composed
# oracle below instead — owned OR explicitly-registered — so an external dir the
# user opted in via the registry is indexable WITHOUT loosening the project guard.


def mount_path_is_registered(store: "LibraryMounts", scope: str, path: str) -> bool:
    """Free-function form of LibraryMounts.is_registered (the parallel predicate).

    Kept as a module function (not only a method) so the mount-index call site can
    compose it with repo_root_is_owned without holding a store method reference."""
    return store.is_registered(scope, path)


def is_mount_indexable(
    project_dir,
    path: str,
    scope: str,
    store: "LibraryMounts",
) -> bool:
    """The composed ownership oracle for the MOUNT-index path: owned OR registered.

    `owned` = the project's TECH.md declares `path` as its repo (the existing
    guard); `registered` = `path` is an enabled mount in THIS scope. Either branch
    authorizes indexing; neither → reject (the invariant that keeps a random
    external path out). This is ONLY for the new mount-index path — the 3
    project-loop sites keep calling repo_root_is_owned directly (a project reindex
    must never pick up a mount).
    """
    try:
        from core.code_intel import repo_root_is_owned
        if repo_root_is_owned(project_dir, path):
            return True
    except Exception:  # noqa: BLE001 — ownership check must never raise into the gate
        pass
    return mount_path_is_registered(store, scope, path)


# ── Code-dir mount: per-mount graph + additive recall pass (Cycle 4) ─────────
#
# A code mount is indexed into its OWN code_intel graph under the workspace
# (Knowledge/Library/mounts/<id>/code_intel.db) with repo_root pointed at the
# EXTERNAL source. This graph is SEPARATE from every project graph (no shared
# _graph_cache key → structurally impossible to contaminate a project's brain —
# the run_1950e67e concern). Its symbols surface via recall_mounts(), which the
# codeintel recall leg calls as an ADDITIVE pass (no signature change to
# _codeintel_recall / recall_all — Gate-1 rev 3).

# Per-mount GraphStore cache, keyed by mount id (NOT project name — never touches
# code_intel._graph_cache, so a mount can never collide with / evict a project).
_mount_graph_cache: dict[str, object] = {}
_mount_cache_lock = threading.Lock()


# Dirs skipped by judge_mount_kind's scan: never descend these when looking for a
# repo marker — a marker inside node_modules/vendor belongs to a dependency, not to
# THIS directory (a vendored `package.json` must not make a docs folder read as code).
_JUDGE_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__",
                    "dist", "build", ".next", "target", ".cache", "vendor"}

# Repo markers: the files/dirs a REAL project uses to declare itself a buildable
# repo. Presence of any of these (at the root or a shallow subdir) means "this is a
# proper repo worth a code_intel graph". A folder of scattered source FILES with no
# such marker is NOT a repo — it is docs-with-code-samples, and building a code graph
# over it is noise (the AI-Native mislabel, run_139d7652). Judge by REPO-NESS, not by
# "contains any parseable file". `.git` is the strongest single signal; the manifests
# cover the language ecosystems code_intel can actually parse. requirements.txt is
# deliberately EXCLUDED — a lone requirements.txt often sits beside notebooks/notes and
# is too weak a signal alone (it would re-open the over-eager-code bug from the docs side).
_REPO_MARKERS = {
    ".git",                                    # any VCS-tracked tree
    "package.json", "tsconfig.json",           # JS/TS
    "pyproject.toml", "setup.py", "setup.cfg",  # Python
    "Cargo.toml",                               # Rust
    "go.mod",                                   # Go
    "pom.xml", "build.gradle", "build.gradle.kts",  # JVM
    "Gemfile",                                  # Ruby
    "composer.json",                            # PHP
    "CMakeLists.txt", "Makefile",               # C/C++/make
}
# How deep below the mount root we look for a marker. A monorepo often carries its
# markers at the root; a repo mounted one level up (e.g. ~/repos/foo) has them at
# depth 0-1. We scan root + immediate children dirs (depth<=2 path-parts below root)
# so a real repo is found cheaply without an unbounded walk.
_JUDGE_MARKER_DEPTH = 2
_JUDGE_SCAN_CAP = 5000  # entries scanned before defaulting to 'docs' (bounded)


def judge_mount_kind(path: str) -> str:
    """The kind a mount is registered with: :func:`_judge_kind`, 'docs' when it cannot
    tell (the lower-risk kind)."""
    return _judge_kind(path) or "docs"


def _judge_kind(path: str) -> Optional[str]:
    """Judge a directory's mount kind by REPO-NESS: 'code' iff it looks like a real,
    buildable repository (carries a repo marker — `.git`, `package.json`,
    `pyproject.toml`, `Cargo.toml`, `go.mod`, … see `_REPO_MARKERS`), else 'docs'.

    WHY repo-marker, not "contains any source file" (run_139d7652): the old rule
    returned 'code' on the FIRST parseable file found, so a docs-majority folder with
    a stray `.py`/`.ts` sample (e.g. an "AI-Native" notes folder) was mislabeled code
    and got a mostly-empty code_intel graph. A folder of scattered code FILES is not a
    repo; only a marker-bearing tree earns a code graph. Everything else is docs (the
    lower-risk kind — no code graph; its text content is chunked into the shared
    Knowledge FTS5 by index_docs_mount, so recall still reaches it).

    Bounded scan: only root + shallow subdirs (depth <= `_JUDGE_MARKER_DEPTH`),
    never descending into vendored subtrees or below that depth, capped at
    `_JUDGE_SCAN_CAP` entries. 'docs' for an empty or marker-less dir; None when it
    cannot tell (not a dir, an error, the scan cap): the sweep then leaves the kind
    as it is, and registration takes 'docs' (judge_mount_kind)."""
    import os
    try:
        root = Path(path).expanduser()
        if not root.is_dir():
            return None
        # Root-level marker is the cheap common case (git repo / manifest at root).
        for marker in _REPO_MARKERS:
            try:
                if (root / marker).exists():
                    return "code"
            except OSError:
                continue
        # Shallow walk for a marker in an immediate subdir (mounted-one-level-up case).
        # A vendored dir's own name counts, its contents do not (node_modules/dep/
        # package.json is a dependency's marker); `.git` is BOTH a skip-dir and a
        # marker, so a shallow `.git` DIR still counts while its contents stay
        # unread (Gate-2 LOW, run_139d7652: a .git-only subdir repo was mislabeled
        # docs). os.walk is pruned, so nothing below the depth is ever read.
        scanned = 0
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base  # this dir's entries sit at depth + 1
            for name in dirnames + filenames:
                scanned += 1
                if scanned > _JUDGE_SCAN_CAP:
                    return None  # too big to classify cheaply
                if name in _REPO_MARKERS:
                    return "code"
            if depth + 2 > _JUDGE_MARKER_DEPTH:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in _JUDGE_SKIP_DIRS]
        return "docs"
    except Exception:  # noqa: BLE001 — judgement must never raise into the caller
        return None


def is_protected_system_path(path: str) -> bool:
    """True if `path` is at/under a protected system root (exfiltration guard).
    Shared by the Inbox copy + the mount register endpoint so both agree on the
    same threat model (Gate-2 #1: the two sibling endpoints must not diverge)."""
    from core.library_inbox import _is_system_path
    try:
        return _is_system_path(Path(path).expanduser().resolve())
    except OSError:
        return True  # unresolvable → refuse (fail-closed)


def _mounts_dir() -> Path:
    """Workspace dir that holds per-mount indexes: SwarmWS/Knowledge/Library/mounts/.
    Indirected through a function so tests can monkeypatch it to a tmp dir."""
    from jobs.paths import SWARMWS
    return SWARMWS / "Knowledge" / "Library" / "mounts"


def _mount_db_path(mount_id: str) -> Path:
    return _mounts_dir() / mount_id / "code_intel.db"


def _load_mount_graph(mount_id: str):
    """Load (cached) the per-mount GraphStore, or None if not indexed yet."""
    with _mount_cache_lock:
        if mount_id in _mount_graph_cache:
            return _mount_graph_cache[mount_id]
    db_path = _mount_db_path(mount_id)
    if not db_path.exists():
        return None
    try:
        from core.code_intel.graph_store import GraphStore
        graph = GraphStore(db_path)
        with _mount_cache_lock:
            if mount_id in _mount_graph_cache:  # double-check after acquiring
                graph.close()
                return _mount_graph_cache[mount_id]
            _mount_graph_cache[mount_id] = graph
        return graph
    except Exception as exc:  # noqa: BLE001
        logger.warning("mount %s: failed to load graph: %s", mount_id, exc)
        return None


def _invalidate_mount_graph(mount_id: str) -> None:
    with _mount_cache_lock:
        g = _mount_graph_cache.pop(mount_id, None)
    if g is not None:
        try:
            g.close()
        except Exception:  # noqa: BLE001
            pass


def _remove_mount_knowledge_chunks(mount_id: str) -> int:
    """Clear a docs mount's chunks from the shared Knowledge FTS5 (best-effort).

    Called on unmount (B1, run_3f837bdd). A store-open/DB failure is swallowed — the
    registry row is already deleted, and a leftover chunk self-heals on the next
    re-index of a same-id mount (ids are uuid4, so collision is not a concern). Kept
    a free function (not a LibraryMounts method) because LibraryMounts holds the
    registry connection, not the recall-DB connection — this opens its own."""
    try:
        from core.knowledge_store import KnowledgeStore
        from core.vec_db import open_vec_db
        with open_vec_db() as conn:
            if conn is None:
                return 0
            kstore = KnowledgeStore(conn)
            kstore.ensure_tables()
            return kstore.remove_mount_chunks(mount_id)
    except Exception as exc:  # noqa: BLE001 — unmount must never fail on chunk cleanup
        logger.debug("mount %s: knowledge-chunk cleanup skipped: %s", mount_id, exc)
        return 0


def index_code_mount(store: "LibraryMounts", mount_id: str) -> dict:
    """Index a code-kind mount into its own per-mount graph (repo_root=external).

    Uses code_intel's public parse API + GraphStore public API only — never
    touches the CRITICAL shared _graph_cache or the project reindex loop. Returns
    {status, symbols?} — status ∈ {indexed, indexed_empty, skipped_non_code, unknown, source_missing,
    already_indexing}.

    last_synced honesty (run_139d7652, Gate-2 HIGH): mark_synced is the signal the
    Library UI reads to claim "indexed — recall reaches it". A marker-bearing repo
    with ZERO parseable source (e.g. a .git tree of only docs, or all-unknown
    extensions) yields an EMPTY graph — recall can reach nothing in it. So we
    mark_synced ONLY when the graph actually has symbols (total > 0); an empty
    result returns 'indexed_empty' WITHOUT stamping last_synced, so the honesty
    badge correctly reads it as not-recall-reachable rather than lying "indexed".
    """
    row = store.get_mount(mount_id)
    if row is None:
        return {"status": "unknown"}
    src = Path(row["path"]).expanduser()
    if not src.is_dir():
        store.check_health(mount_id)  # persists 'missing'
        return {"status": "source_missing"}

    from core.code_intel.build import rebuild, write_lock
    from core.code_intel.graph_store import GraphStore

    _invalidate_mount_graph(mount_id)  # drop any cached handle before rewrite
    db_path = _mount_db_path(mount_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with write_lock(db_path, wait=False) as held:
        if not held:  # a second index of the same mount is already running
            return {"status": "already_indexing"}
        # read under the lock: the sweep switches a kind only while no indexer holds it
        row = store.get_mount(mount_id)
        if row is None or row["kind"] != "code":
            return {"status": "skipped_non_code"}
        started = _index_started_stamp()
        graph = GraphStore(db_path)
        # The one full rebuild, without routes (a library's routes have no reader) and
        # without the indexed stamp (a mount's freshness is its own last_synced). An
        # empty parse leaves a previous graph as it was.
        # repo_root points at the EXTERNAL source, as given
        built = rebuild(graph, src, head=None, routes=False, stamp=False,
                        meta={"repo_root": str(src), "mount_id": mount_id})
        total = 0 if built.empty else built.nodes
    with _mount_cache_lock:
        _mount_graph_cache[mount_id] = graph
    if total > 0:
        # Real symbols indexed → stamp last_synced (the UI's "recall reaches it" signal).
        store.mark_synced(mount_id, index_ref=str(db_path), synced_at=started)
        return {"status": "indexed", "symbols": total}
    # Marker present but no parseable source → empty graph, recall reaches nothing.
    # Do NOT stamp last_synced: the honesty badge must read this as not-indexed. The
    # empty result is recorded, so the sweep re-indexes it only after the next change.
    store.mark_indexed_empty(mount_id, at=started)
    return {"status": "indexed_empty", "symbols": 0}


# Dirs skipped by the docs-index walk — same vendored/build junk the kind judge
# skips; indexing a node_modules tree as "docs" would be noise, not knowledge.
_DOCS_SKIP_DIRS = _JUDGE_SKIP_DIRS
_DOCS_SCAN_CAP = 5000  # max files walked before stopping (bounded, like the judge)


def index_docs_mount(store: "LibraryMounts", mount_id: str) -> dict:
    """Index a docs-kind mount's REAL text content into the shared Knowledge FTS5,
    at mount time — no LLM, no chat, no briefing step (run_3f837bdd, option B1).

    This is the docs counterpart of index_code_mount, and the systematic fix for the
    old gap: docs mounts USED to require a chat-triggered `write_docs_cards` briefing
    pass before recall could reach them (write_docs_cards deleted this run). Instead
    we treat a mounted docs dir exactly like our own Knowledge/ — chunk every
    UTF-8-decodable text file with the SAME pure `chunk_markdown`, and upsert into the
    SAME `KnowledgeStore` / `knowledge_fts` (`open_vec_db()` → data.db) that
    `_recall_library` reads. So recall reaches a freshly-mounted docs dir with ZERO
    new recall code.

    Mechanics:
    - source_file key = ``mount:<id>/<rel>`` — a distinct namespace from Knowledge/
      rel keys (the `.context/Archives/` precedent) so mount chunks never collide
      with / thrash the native index.
    - UTF-8 gate: a file that `read_text('utf-8')` decodes is indexed; a binary
      (UnicodeDecodeError) is skipped — content-decodability IS the filter (XG:
      index any text file, skip binaries), not an extension allowlist.
    - delta-sync per file (get_existing_hashes / remove_stale_chunks) so a re-index
      is cheap and idempotent; stale files' chunks are pruned via remove_mount_chunks
      for keys no longer present.
    - bounded walk (skip vendored dirs, dotfiles, lock files and backup copies —
      ``_not_content`` — and cap files) so a huge dir can't hang.
    - mark_synced ONLY when chunks were written (honesty, run_139d7652): an
      empty/all-binary dir returns 'indexed_empty' WITHOUT stamping last_synced, so
      the UI badge reads it as not-recall-reachable.

    Returns {status: indexed|indexed_empty|skipped_non_docs|unknown|source_missing|
    already_indexing|store_unavailable, chunks}.
    """
    row = store.get_mount(mount_id)
    if row is None:
        return {"status": "unknown"}
    src = Path(row["path"]).expanduser()
    if not src.is_dir():
        store.check_health(mount_id)  # persists 'missing'
        return {"status": "source_missing"}

    from core.knowledge_store import MOUNT_KEY_PREFIX, KnowledgeStore, index_text_items
    from core.vec_db import open_vec_db

    key_prefix = f"{MOUNT_KEY_PREFIX}{mount_id}/"

    def _items():
        walked = 0
        for p in sorted(src.rglob("*")):
            if any(part in _DOCS_SKIP_DIRS for part in p.relative_to(src).parts):
                continue
            if _not_content(p.name):
                continue
            if not p.is_file():
                continue
            walked += 1
            if walked > _DOCS_SCAN_CAP:
                break
            try:
                content = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable → skip (content-decodability is the gate)
            if p.suffix.lower() in _MARKUP_SUFFIXES:
                content = _markup_text(content)   # the text it shows, not its source
            yield p.relative_to(src).as_posix(), content

    from core.code_intel.build import write_lock
    with write_lock(_index_lock_path(mount_id, "docs"), wait=False) as held:
        if not held:  # another process is indexing this mount now
            return {"status": "already_indexing", "chunks": 0}
        # read under the lock: the sweep switches a kind only while no indexer holds it
        row = store.get_mount(mount_id)
        if row is None or row["kind"] != "docs":
            return {"status": "skipped_non_docs", "chunks": 0}
        started = _index_started_stamp()
        with open_vec_db() as conn:
            if conn is None:  # nothing read, nothing recorded: the next sweep tries again
                return {"status": "store_unavailable", "chunks": 0}
            kstore = KnowledgeStore(conn)
            kstore.ensure_tables()
            # Shared delta-sync (knowledge_store.index_text_items): only content-bearing
            # files count as seen — a whitespace-only file must not make the mount read
            # as indexed (honesty, run_139d7652 badge invariant) — and keys of files that
            # vanished since the last index are pruned.
            stats = index_text_items(kstore, key_prefix, _items())
    total_chunks, seen_keys = stats["written"], stats["files"]

    if total_chunks > 0 or seen_keys:
        # Content-bearing files exist (seen_keys) — even if a delta re-index added 0
        # NEW chunks, the mount is recall-reachable, so stamp it. An empty/all-binary/
        # all-whitespace dir has empty seen_keys → falls through to indexed_empty.
        store.mark_synced(mount_id, index_ref=key_prefix, synced_at=started,
                          index_version=DOCS_INDEX_VERSION)
        return {"status": "indexed", "chunks": total_chunks}
    # No text content at all (empty or all-binary) → recall reaches nothing (its old
    # chunks were just pruned); recorded, so the sweep re-indexes it only after the
    # next change.
    store.mark_indexed_empty(mount_id, at=started, recall_lost=True,
                             index_version=DOCS_INDEX_VERSION)
    return {"status": "indexed_empty", "chunks": 0}


def recall_mounts(query: str, *, scope: str, store: "LibraryMounts", limit: int = 8) -> list[dict]:
    """Search all ENABLED code mounts in `scope` OR the workspace-GLOBAL scope.

    Gate-2 #2: recall runs per active-project, but a mount added "to my library"
    registers GLOBAL and must surface in EVERY project — so we union the active
    scope with GLOBAL. A mount explicitly scoped to a project still surfaces only
    there. Each hit is a symbol dict stamped with `mount_id` + `mount_path` so the
    agent Reads the LIVE external source. Never raises — a missing/broken mount
    graph is skipped. This is the additive pass the codeintel recall leg composes in.
    """
    out: list[dict] = []
    scopes = {scope, GLOBAL_SCOPE}
    try:
        rows = [r for s in scopes for r in store.list_mounts(scope=s)]
    except Exception:  # noqa: BLE001
        return out
    for row in rows:
        if not row["enabled"] or row["kind"] != "code":
            continue
        graph = _load_mount_graph(row["id"])
        if graph is None:
            continue
        try:
            hits = graph.search_symbols(query, limit=limit)
        except Exception as exc:  # noqa: BLE001 — one bad mount must not sink recall
            logger.debug("mount %s: search failed: %s", row["id"], exc)
            continue
        for h in hits:
            out.append({**h, "mount_id": row["id"], "mount_path": row["path"]})
    # Rank across mounts by the graph's own rank (more-negative BM25 = better).
    out.sort(key=lambda h: h.get("rank", 0.0))
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    """``python -m core.library_mounts index-code|index-docs <mount id>``: index a
    mount in this process. The daemon starts it as a child (``code_intel.build.spawn_child``)
    so an index never holds the daemon's GIL; a code parse waits for its turn."""
    import json
    import sqlite3
    import sys
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ("index-code", "index-docs"):
        print("usage: python -m core.library_mounts index-code|index-docs <mount id>",
              file=sys.stderr)
        return 2
    from jobs.paths import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    from core.code_intel.build import rebuild_turn, write_lock
    try:
        store = LibraryMounts(conn)
        store.ensure_table()
        # held for the child's whole life, so the sweep sees a child that still waits
        # for its turn and starts no second one behind it
        with write_lock(_queue_lock_path(args[1]), wait=False) as queued:
            if not queued:
                result = {"status": "already_queued"}
            elif args[0] == "index-docs":
                result = index_docs_mount(store, args[1])
            else:
                # one full parse at a time across the machine, as the build children
                with rebuild_turn() as turn:
                    result = index_code_mount(store, args[1]) if turn else {"status": "no_turn"}
    finally:
        conn.close()
    print(json.dumps({"mount": args[1], **result}))
    return 0 if result.get("status") in ("indexed", "indexed_empty", "already_queued") else 1


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
