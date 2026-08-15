"""Library mount registry — pointers to external knowledge, never copies.

The mount registry backs the Library overlay's Mounted section. A mount is a
lightweight row {id, scope, path, kind, briefing, index_ref, last_synced, health,
enabled, created_at} — a POINTER into the user's disk, indexed IN PLACE by the
engine best suited to its kind (code → code_intel graph; docs → briefing cards on
the FTS5 library leg). This module owns ONLY the registry (CRUD + source-exists
health); indexing + ownership-plan-A live in later cycles.

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
# reference). 'stale' (source mtime > index) is set by the freshness job cycle.
VALID_HEALTH = ("fresh", "stale", "missing")


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

    def mark_synced(self, mount_id: str, index_ref: Optional[str] = None) -> bool:
        """Record a successful index: stamp last_synced (now) + index_ref, and
        set health fresh. Called by the indexer after a graph is built."""
        cur = self._conn.execute(
            "UPDATE library_mounts SET last_synced = datetime('now'), "
            "index_ref = COALESCE(?, index_ref), health = 'fresh' WHERE id = ?",
            (index_ref, mount_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

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
        health = self._probe_health(row["path"], row["last_synced"])
        self._conn.execute(
            "UPDATE library_mounts SET health = ? WHERE id = ?", (health, mount_id)
        )
        self._conn.commit()
        return health

    @staticmethod
    def _probe_health(path: str, last_synced: Optional[str] = None) -> str:
        """Health probe: missing (gone) > stale (edited after index) > fresh.

        `last_synced` is the SQLite ``datetime('now')`` UTC text stamped by
        mark_synced. When absent (never indexed), a present source is 'fresh' —
        there is no index to be stale against yet."""
        try:
            p = Path(path).expanduser()
            if not p.exists():
                return "missing"
            if last_synced:
                synced_ts = _parse_sqlite_utc(last_synced)
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
    its tree). Bounded scan; a walk error falls back to the root's own mtime so a
    permission hiccup never crashes the freshness probe."""
    try:
        if root.is_file():
            return root.stat().st_mtime
        newest = root.stat().st_mtime
        for p in root.rglob("*"):
            try:
                m = p.stat().st_mtime
                if m > newest:
                    newest = m
            except OSError:
                continue
        return newest
    except OSError:
        return 0.0


def refresh_all_mounts(store: "LibraryMounts", scope: Optional[str] = None) -> dict:
    """Light periodic freshness job: re-probe every mount's health, persist it,
    and return a {scanned, fresh, stale, missing} summary. Never raises on one bad
    mount (best-effort — a broken mount is counted, not fatal)."""
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
    return summary


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
    """Judge a directory's mount kind by REPO-NESS: 'code' iff it looks like a real,
    buildable repository (carries a repo marker — `.git`, `package.json`,
    `pyproject.toml`, `Cargo.toml`, `go.mod`, … see `_REPO_MARKERS`), else 'docs'.

    WHY repo-marker, not "contains any source file" (run_139d7652): the old rule
    returned 'code' on the FIRST parseable file found, so a docs-majority folder with
    a stray `.py`/`.ts` sample (e.g. an "AI-Native" notes folder) was mislabeled code
    and got a mostly-empty code_intel graph. A folder of scattered code FILES is not a
    repo; only a marker-bearing tree earns a code graph. Everything else is docs (the
    lower-risk kind — no code index, recall reaches it via docs briefing cards).

    Bounded scan: only root + shallow subdirs (depth <= `_JUDGE_MARKER_DEPTH`),
    skipping vendored subtrees, capped at `_JUDGE_SCAN_CAP` entries. Defaults to
    'docs' for an empty / unreadable / huge / marker-less dir."""
    try:
        root = Path(path).expanduser()
        if not root.is_dir():
            return "docs"
        # Root-level marker is the cheap common case (git repo / manifest at root).
        for marker in _REPO_MARKERS:
            try:
                if (root / marker).exists():
                    return "code"
            except OSError:
                continue
        # Shallow walk for a marker in an immediate subdir (mounted-one-level-up case).
        scanned = 0
        for p in root.rglob("*"):
            try:
                rel_parts = p.relative_to(root).parts
            except ValueError:
                continue
            # Prune by ANCESTOR only (not p itself): a path INSIDE a vendored dir
            # (node_modules/dep/package.json) is a dependency's marker, skip it. But
            # `.git` is BOTH a skip-dir (never descend) AND a repo marker — checking
            # ancestors (rel_parts[:-1]) instead of all parts lets a shallow `.git`
            # DIR still count as a marker while its CONTENTS stay pruned (Gate-2 LOW,
            # run_139d7652: a .git-only subdir repo was mislabeled docs).
            if any(part in _JUDGE_SKIP_DIRS for part in rel_parts[:-1]):
                continue
            depth = len(rel_parts)  # # path parts below root
            if depth > _JUDGE_MARKER_DEPTH:
                continue
            scanned += 1
            if scanned > _JUDGE_SCAN_CAP:
                return "docs"  # too big to classify cheaply → safe default
            if p.name in _REPO_MARKERS:
                return "code"
        return "docs"
    except Exception:  # noqa: BLE001 — judgement must never raise into the caller
        return "docs"


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


def index_code_mount(store: "LibraryMounts", mount_id: str) -> dict:
    """Index a code-kind mount into its own per-mount graph (repo_root=external).

    Uses code_intel's public parse API + GraphStore public API only — never
    touches the CRITICAL shared _graph_cache or the project reindex loop. Returns
    {status, symbols?} — status ∈ {indexed, indexed_empty, skipped_non_code, unknown, source_missing}.

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
    if row["kind"] != "code":
        return {"status": "skipped_non_code"}
    src = Path(row["path"]).expanduser()
    if not src.is_dir():
        store.check_health(mount_id)  # persists 'missing'
        return {"status": "source_missing"}

    from core.code_intel.parser import parse_repo_with_coverage
    from core.code_intel.graph_store import GraphStore

    parse_out = parse_repo_with_coverage(src)
    _invalidate_mount_graph(mount_id)  # drop any cached handle before rewrite
    db_path = _mount_db_path(mount_id)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    graph = GraphStore(db_path)
    total = 0
    if parse_out.results:
        graph.clear()
        graph.bulk_insert(parse_out.results, repo_root=str(src))
        graph.set_meta("repo_root", str(src))  # points at the EXTERNAL source
        graph.set_meta("mount_id", mount_id)
        total = sum(len(pr.nodes) for pr in parse_out.results)
    with _mount_cache_lock:
        _mount_graph_cache[mount_id] = graph
    if total > 0:
        # Real symbols indexed → stamp last_synced (the UI's "recall reaches it" signal).
        store.mark_synced(mount_id, index_ref=str(db_path))
        return {"status": "indexed", "symbols": total}
    # Marker present but no parseable source → empty graph, recall reaches nothing.
    # Do NOT stamp last_synced: the honesty badge must read this as not-indexed.
    return {"status": "indexed_empty", "symbols": 0}


def _card_slug(rel_name: str) -> str:
    """Stable, filesystem-safe card filename for a source file (idempotent key).
    A subdir separator / dot becomes '-', so rewriting the SAME source file always
    targets the SAME card (delta, never a dup)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in rel_name.strip("/"))
    return f"card-{safe}.md"


def write_docs_cards(
    store: "LibraryMounts",
    mount_id: str,
    *,
    briefings: dict,
    overview: str,
) -> dict:
    """Persist file-level briefing cards for a docs-kind mount (Cycle 5).

    `briefings` maps a source-relative filename → the agent's briefing text (the
    agent, chat-native, judges which files are worth a card and writes the text —
    this function is the mechanical writer). Each card is a plain .md POINTER:
    it carries the LIVE source path + the briefing, NEVER a copy of the source
    bytes (index-not-warehouse). Cards land in Knowledge/Library/mounts/<id>/ and
    are auto-indexed by the existing sync_knowledge_index rglob('*.md') → surface
    on the library FTS5 recall leg with zero new recall code. Idempotent: the same
    source file always rewrites the same card. Returns {status, cards?}.
    """
    row = store.get_mount(mount_id)
    if row is None:
        return {"status": "unknown"}
    if row["kind"] != "docs":
        return {"status": "skipped_non_docs"}
    src_root = Path(row["path"]).expanduser()
    card_dir = _mounts_dir() / mount_id
    card_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for rel_name, briefing in briefings.items():
        source_path = src_root / rel_name
        card_path = card_dir / _card_slug(rel_name)
        body = (
            f"# {rel_name}\n\n"
            f"> Mounted docs briefing — recall lands here, then Read the LIVE source.\n\n"
            f"- **source:** `{source_path}`\n"
            f"- **mount:** `{mount_id}` (scope `{row['scope']}`)\n\n"
            f"## Briefing\n\n{briefing}\n"
        )
        card_path.write_text(body, encoding="utf-8")  # rewrite-in-place = idempotent
        written += 1

    # Directory-overview card (recall landing spot for a whole-corpus query).
    (card_dir / "_overview.md").write_text(
        f"# Mounted docs: {src_root.name}\n\n"
        f"- **source dir:** `{src_root}`\n"
        f"- **mount:** `{mount_id}` (scope `{row['scope']}`)\n\n"
        f"## Overview\n\n{overview}\n",
        encoding="utf-8",
    )
    store.mark_synced(mount_id, index_ref=str(card_dir))
    return {"status": "written", "cards": written}


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
