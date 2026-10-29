"""Writing a code graph from a repo: the one full rebuild and the one incremental update.

Every path that rebuilds a graph (``bind_repo``, the build child the Brain Hub
re-index button, a Confirm and a reader start, a library mount) calls ``rebuild``;
the one incremental path, ``ensure_fresh`` (a reader out of the daemon, the hourly
job), calls ``update_files``. Before this there were four rebuilds and three
incremental paths, and they disagreed: two rebuilds stored no routes, one marked an
emptied graph as fresh, and ``clear()`` kept the routes of deleted handlers.

The daemon never parses (design 2026-10-29-code-intel-unification §0.2): what it
asks for runs in a child (``spawn_build`` / ``spawn_child``). One writer per graph
across processes holds ``write_lock``.

What every rebuild now does the same way:
- An empty parse leaves the graph exactly as it was and unstamped (a transient empty
  parse — a repo not yet on disk, a parser teardown — must not erase a good graph or
  make an empty one read fresh). A graph that has no ``repo_root`` yet gets it, so a
  first bind of an empty repo still knows where it points.
- Coverage holes and parse status are stored (the analysis reports them), and so is
  ``PARSER_VERSION``: a graph stored by an older parser reads stale (``parser_outdated``).
- Routes (and router prefixes) are extracted, unless the caller says routes have no
  reader (a library mount). A prefix failure is logged and never fails the write.
- ``head`` is the commit the CALLER read before the parse: a commit that lands while
  the parse runs stays unindexed and the next incremental update takes it.
- The write is one SQLite transaction (``GraphStore.transaction``), so a reader in any
  process sees the old graph or the new one, never an emptied one, and a writer killed
  half-way leaves the old graph. Parsing and route extraction run before it; router
  prefixes after it.

The caller owns everything else: which repo, the ownership check, a bind lock,
mount-specific meta.
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# More changed files than this: rebuild instead of updating file by file (also the
# freshness check's "suggest a full rebuild" line).
FULL_REBUILD_FILES = 100

# What the parser and the store's resolution emit (node ids, edge kinds, confidence
# bands, which calls are typed). Bump it with any change to that output: every graph
# stored with a lower version reads stale and is rebuilt by a child, with no step by
# a person (design 2026-10-29-code-intel-auto D3). 1: receivers typed, values and
# patches as references (§0.10). 2: calls on literals, builtins and outside libraries'
# objects get no edge; self.x typed by the class's attributes; same-file return
# annotations; a name-only call named like a builtin method is its own band
# (design 2026-10-29-code-intel-e2e B). 3: tool caches (.gradle, .cache, .nox,
# .ruff_cache) are not read. 4: a call on a chain through a call (``X.builder().build()``)
# is a call on a value, not on X.
PARSER_VERSION = 4


def parser_outdated(graph: GraphStore) -> bool:
    """The graph holds symbols written by an older parser (no version stored, or a
    lower one). A graph with no symbols is not outdated: there is nothing in it to be
    wrong, and an empty parse never stamps (it would read stale forever); a graph this
    parser rebuilt and found nothing in (``empty_parse_version``) is not outdated
    either."""
    def _int(key: str) -> int:
        try:
            return int(graph.get_meta(key) or 0)
        except ValueError:
            return 0
    if _int("parser_version") >= PARSER_VERSION or _int("empty_parse_version") >= PARSER_VERSION:
        return False
    return graph._conn.execute("SELECT 1 FROM code_nodes LIMIT 1").fetchone() is not None


@dataclass
class RebuildResult:
    empty: bool
    nodes: int = 0
    files: int = 0
    routes: int = 0
    parse_status: str = ""
    coverage_holes: list = field(default_factory=list)


@dataclass
class UpdateResult:
    needs_full: bool = False
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every changed file landed: only then may a commit marker move past them."""
        return not self.needs_full and not self.failed


def _file_routes(repo_root: Path, rel_path: str) -> list:
    """The routes one file declares (pure: reads the file, stores nothing)."""
    from .parser import LANGUAGE_MAP, resolves_inside
    from .route_parser import extract_routes
    full = repo_root / rel_path
    lang = LANGUAGE_MAP.get(full.suffix)
    if lang is None or not full.is_file() or not resolves_inside(repo_root, full):
        return []
    try:
        return extract_routes(rel_path, full.read_text(encoding="utf-8", errors="replace"), lang)
    except Exception as exc:  # noqa: BLE001 — route extraction is best-effort per file
        logger.debug("route extraction failed for %s: %s", rel_path, exc)
        return []


def _apply_prefixes(graph: GraphStore, root: Path) -> None:
    """Re-apply router prefixes. A refinement of stored routes, never fatal: a failure
    (e.g. "database is locked" while another process writes) leaves the routes
    unprefixed and must not fail the rebuild or the update that stored them."""
    from . import resolve_router_prefixes
    try:
        resolve_router_prefixes(graph, root)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("router prefix resolution failed for %s: %s", root, exc)


def rebuild(graph: GraphStore, repo_root: str | Path, *, head: str | None,
            routes: bool = True, stamp: bool = True,
            meta: dict[str, str] | None = None) -> RebuildResult:
    """Replace *graph* with a fresh parse of *repo_root* (rules in the module doc).

    *stamp*: mark the graph fully indexed at *head*. A library mount passes False —
    its freshness is the mount's own ``last_synced``. *meta*: more ``graph_meta`` keys,
    written in the same transaction (``clear()`` empties the table, so a key written
    after the commit would be missing for a moment, and for good if the writer died).
    """
    from .parser import parse_repo_with_coverage

    root = Path(repo_root).resolve()
    out = parse_repo_with_coverage(root)
    results = out.results
    if Path(repo_root).resolve() != root:
        # the directory was swapped (e.g. for a symlink) while it was read: what was
        # parsed is not the checked directory, so nothing is written
        logger.warning("code intel: %s changed while it was read; not written", repo_root)
        return RebuildResult(empty=True, parse_status="root-changed")
    if not results:
        if graph.get_meta("repo_root") is None:
            graph.set_meta("repo_root", str(root))
        # this parser found nothing to parse: the graph is left as it was (a transient
        # empty parse must not wipe it), and it no longer reads outdated, so readers
        # stop asking for a rebuild every ten minutes (F11)
        graph.set_meta("empty_parse_version", str(PARSER_VERSION))
        return RebuildResult(empty=True, parse_status=out.status,
                             coverage_holes=out.coverage_holes)
    found = [r for p in results for r in (_file_routes(root, p.file_path) if routes else [])]
    # One transaction: a reader in another process sees the old graph or the new one,
    # and a writer killed half-way leaves the old one (clear() used to commit alone).
    with graph.transaction():
        graph.clear()
        graph.bulk_insert(results, repo_root=root)
        graph.set_meta("parse_status", out.status)
        graph.set_meta("coverage_holes", json.dumps(out.coverage_holes))
        if head:
            graph.set_meta("coverage_commit", head)
        if found:
            graph.insert_routes(found)
        if stamp:
            graph.mark_indexed(full=True, head=head)
        graph.set_meta("parser_version", str(PARSER_VERSION))
        for key, value in (meta or {}).items():
            graph.set_meta(key, value)
    if found:
        # After the commit: a prefix failure must not undo the rebuild, and a reader
        # seeing the bare decorator paths for a moment is harmless.
        _apply_prefixes(graph, root)
    graph.checkpoint_truncate()
    return RebuildResult(empty=False, nodes=sum(len(p.nodes) for p in results),
                         files=len(results), routes=len(found), parse_status=out.status,
                         coverage_holes=out.coverage_holes)


def routes_for(graph: GraphStore, repo_root: str | Path, files: list[str], *,
               prefixes: bool = True) -> int:
    """Re-store the routes of *files* (repo-relative; a missing file loses its routes),
    then re-apply the router prefixes. Returns the number of routes stored."""
    root = Path(repo_root)
    n = 0
    for rel in files:
        found = _file_routes(root, rel)
        try:
            graph.delete_routes_for_file(rel)
            if found:
                n += graph.insert_routes(found)
        except sqlite3.Error as exc:
            # One file's store error must not leave every later file's routes stale.
            logger.warning("storing routes for %s failed: %s", rel, exc)
    if prefixes and files:
        _apply_prefixes(graph, root)
    return n


def update_files(graph: GraphStore, repo_root: str | Path, files: list[str]) -> UpdateResult:
    """Re-store *files* (repo-relative; deleted ones are removed) and their routes.

    More than FULL_REBUILD_FILES files: nothing is stored and ``needs_full`` is set —
    the caller schedules a rebuild instead of indexing a prefix of the change and
    leaving the rest behind. Never writes ``last_indexed_commit``: a caller that owns
    a commit marker moves it only when ``complete``.
    """
    files = list(dict.fromkeys(files))
    if len(files) > FULL_REBUILD_FILES:
        return UpdateResult(needs_full=True)
    res = graph.incremental_update(repo_root, files)
    restored = [f for f in res.get("updated", []) if (Path(repo_root) / f).is_file()]
    if restored:
        routes_for(graph, repo_root, restored)
    return UpdateResult(updated=list(res.get("updated", [])), skipped=list(res.get("skipped", [])),
                        failed=list(res.get("failed", [])))


# ── building and refreshing the graphs a project owns (run out of the daemon) ──

WRITE_LOCK_WAIT_S = 600  # an explicit rebuild waits this long for another writer


def head_of(repo_root: str | Path) -> str | None:
    """The repo's HEAD commit, read BEFORE a parse (``rebuild``'s contract)."""
    import subprocess
    try:
        from core.code_intel.local_roots import GIT_SAFE
        out = subprocess.run(["git", *GIT_SAFE, "-C", str(repo_root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


@contextlib.contextmanager
def _file_lock(path: Path, wait_s: float):
    """An exclusive ``fcntl`` lock on *path*; yields True when held, False when
    another process still holds it after *wait_s* seconds (0: do not wait)."""
    import time
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:          # not POSIX: no second writer to exclude in practice
        yield True
        return
    with open(path, "a+") as fh:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.5)
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def write_lock(db_path: str | Path, *, wait: bool):
    """One writer per graph across processes: an ``fcntl`` lock on ``<db>.write.lock``.
    Yields True when held. ``wait=False`` yields False at once if another process
    holds it; ``wait=True`` waits up to ``WRITE_LOCK_WAIT_S``, then yields False.
    (The SQLite transaction keeps a rebuild atomic; this lock only keeps two
    processes from parsing the same repo at once.)"""
    with _file_lock(Path(str(db_path) + ".write.lock"),
                    WRITE_LOCK_WAIT_S if wait else 0) as held:
        yield held


# A full parse at a time across every graph a build child rebuilds: after a deploy
# that bumps PARSER_VERSION every graph reads stale at once, and N children parsing
# in parallel would load the machine. A child waits its turn (a queued child is not a
# hang: the one ahead is parsing); past this it gives up and the next reader or the
# hourly job asks again.
REBUILD_TURN_WAIT_S = 3600


def _rebuild_turn_path() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "code_intel" / "rebuild.lock"


def rebuild_turn():
    """The machine-wide turn for a full parse in a build child (``build_graph``)."""
    return _file_lock(_rebuild_turn_path(), REBUILD_TURN_WAIT_S)


def _sync_docs(project_dir: str | Path, repo_root, changed: list[str] | None) -> None:
    """The repo's documents follow its HEAD (design 2026-10-27 §5.3): re-indexed in
    place for recall and re-ranked in survey.json. A failure never fails the graph."""
    try:
        from core.brain_bootstrap.doc_index import sync_repo_after_head_move
        sync_repo_after_head_move(project_dir, repo_root, changed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("code intel: repo document sync for %s failed: %s: %s",
                       repo_root, type(exc).__name__, exc)


def build_graph(ref, *, project_dir: str | Path | None = None,
                only_if_stale: bool = False, queue: bool = False) -> RebuildResult | None:
    """Build (or rebuild) the graph of one owned repo (*ref*: a ``CodeIntelDb``) on its
    own connection, holding its write lock (waiting for another writer). The db file
    is created when it does not exist. *only_if_stale*: do not wait for the lock, and
    skip (None) when another process holds it or the graph is already current — what
    a rebuild started by a reader asks for. With *project_dir*, the repo's documents
    are re-synced after a rebuild. *queue* (the build child): wait for the
    machine-wide turn (``rebuild_turn``) before parsing."""
    from .freshness import check_freshness
    from .graph_store import GraphStore
    from core.ddd_bindings import bind_in_progress
    if ref.repo_root is not None and bind_in_progress(ref.repo_root):
        # the bind re-clones the repo and rebuilds this graph itself
        logger.info("code intel: %s is being bound; skipped", ref.repo)
        return None
    ref.db_path.parent.mkdir(parents=True, exist_ok=True)
    # A queued child takes the machine-wide turn FIRST and the graph's own lock only
    # then: waiting for its turn while holding the graph's lock would keep a bind
    # (which waits WRITE_LOCK_WAIT_S for it) and every reader's update out for as
    # long as the queue. Nothing waits for the turn while holding a graph lock.
    with (rebuild_turn() if queue else contextlib.nullcontext(True)) as turn:
        if not turn:
            logger.info("code intel: %s waited %ss for its turn to parse; skipped",
                        ref.repo, REBUILD_TURN_WAIT_S)
            return None
        with write_lock(ref.db_path, wait=not only_if_stale) as held:
            if not held:
                logger.info("code intel: %s is being written by another process; skipped", ref.repo)
                return None
            if ref.repo_root is not None and bind_in_progress(ref.repo_root):
                # a bind started while this waited: it rebuilds the graph
                logger.info("code intel: %s is being bound; skipped", ref.repo)
                return None
            graph = GraphStore(ref.db_path)
            try:
                if only_if_stale and graph.get_meta("repo_root") is not None:
                    fr = check_freshness(graph)
                    if not fr.stale:
                        return None
                head = head_of(ref.repo_root)
                result = rebuild(graph, ref.repo_root, head=head)
            finally:
                graph.close()
    if project_dir is not None and not result.empty:
        _sync_docs(project_dir, ref.repo_root, None)
    return result


def ensure_graph(project_dir: str | Path, *, repo: str | None = None,
                 full: bool = False, only_if_stale: bool = False,
                 queue: bool = False) -> list[dict]:
    """Build every graph *project_dir* owns that does not exist yet (all of them, or
    the one named *repo*); with *full*, rebuild those that exist too. One dict per
    graph: ``{repo, db, action, nodes?, error?}``. A failure is reported, never raised."""
    from . import owned_graphs
    out: list[dict] = []
    for ref in owned_graphs(project_dir):
        if repo is not None and ref.repo != repo:
            continue
        row = {"repo": ref.repo, "db": str(ref.db_path)}
        if ref.db_path.exists() and not full:
            out.append({**row, "action": "exists"})
            continue
        if ref.repo_root is None or not Path(ref.repo_root).is_dir():
            out.append({**row, "action": "skipped", "error": "repo not on disk"})
            continue
        try:
            built = build_graph(ref, project_dir=project_dir, only_if_stale=only_if_stale,
                                queue=queue)
        except Exception as exc:  # noqa: BLE001 — one repo must not stop the others
            logger.warning("code intel: building %s failed: %s: %s", ref.repo,
                           type(exc).__name__, exc)
            out.append({**row, "action": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if built is None:
            out.append({**row, "action": "skipped"})
        else:
            out.append({**row, "action": "empty" if built.empty else "built", "nodes": built.nodes})
    return out


def spawn_build(project: str, *, repo: str | None = None, full: bool = False,
                only_if_stale: bool = False):
    """Start ``python -m core.code_intel.build`` for *project* as a child in its own
    session: a parse never runs in the caller's process (the daemon's GIL is the chat
    loop's; an agent's Bash call has a timeout). Output goes to
    ``<app data>/logs/code_intel_build.log``. Returns the ``Popen``."""
    # name=value: a project or checkout name that starts with "-" stays a value
    args = ["-m", "core.code_intel.build", f"--project={project}"]
    if repo:
        args.append(f"--repo={repo}")
    if full:
        args.append("--full")
    if only_if_stale:
        args.append("--if-stale")
    return spawn_child(args)


def spawn_child(args: list[str]):
    """``python <args>`` (the backend's own Python, packaged or not) as a detached
    child in its own session, output appended to ``<app data>/logs/code_intel_build.log``.
    Every code-graph write the daemon asks for goes through here."""
    import subprocess
    from config import get_app_data_dir
    from utils.backend_bin import py_command
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "code_intel_build.log", "ab") as log:
        return subprocess.Popen(py_command(*args), stdin=subprocess.DEVNULL, stdout=log,
                                stderr=log, start_new_session=True)


def _db_meta(db_path: str | Path, key: str) -> str | None:
    """One ``graph_meta`` value, read-only (None when missing or unreadable)."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM graph_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _head(root: Path) -> str | None:
    import subprocess
    from .local_roots import GIT_SAFE
    try:
        out = subprocess.run(["git", *GIT_SAFE, "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def db_parser_outdated(db_path: str | Path) -> bool:
    """``parser_outdated`` for a graph file, read-only (opening a ``GraphStore`` runs
    its schema script). False when the file is missing or unreadable."""
    import sqlite3
    path = Path(db_path)
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        for key in ("parser_version", "empty_parse_version"):
            row = conn.execute("SELECT value FROM graph_meta WHERE key = ?", (key,)).fetchone()
            try:
                stored = int(row[0]) if row and row[0] else 0
            except ValueError:
                stored = 0
            if stored >= PARSER_VERSION:
                return False
        return conn.execute("SELECT 1 FROM code_nodes LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# A reader in the daemon asks for a rebuild at most this often per graph (it is read
# on every Brain Hub open; the child it starts takes minutes).
REBUILD_REQUEST_EVERY_S = 600


def request_rebuild(project: str, ref) -> bool:
    """Ask for a full ``--if-stale`` rebuild child of one owned graph: one is started
    unless one was asked for in the last ``REBUILD_REQUEST_EVERY_S`` (a
    ``<db>.rebuild-requested`` marker, written once a child started). Every reader
    that finds a graph to rebuild asks through here, in the daemon or out of it, so
    repeated reads start one child, not one each. It starts a child, it never parses.
    True when a rebuild is on its way (started now or recently); False when no child
    could be started."""
    import fcntl
    import os
    import time
    if not ref.repo:
        return False
    marker = Path(str(ref.db_path) + ".rebuild-requested")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(marker) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return False                       # no marker can be kept: never an unlimited spawn
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)     # check, claim and spawn as one step
        try:
            if time.time() - marker.stat().st_mtime < REBUILD_REQUEST_EVERY_S:
                return True
        except OSError:
            pass
        try:
            marker.touch()                 # claimed before the child starts
        except OSError:
            return False
        try:
            spawn_build(project, repo=ref.repo, full=True, only_if_stale=True)
        except OSError as exc:
            logger.warning("code intel: could not start a rebuild of %s: %s", ref.repo, exc)
            return False
        return True
    finally:
        os.close(fd)


def prepare_graph(project: str, repo_root: str | Path) -> str:
    """Before a review needs it: start a build child for the graph *project* owns of the
    checkout *repo_root* when it is missing, built by an older parser, or behind its
    HEAD (at most one child per ``REBUILD_REQUEST_EVERY_S``, as ``request_rebuild``).
    Returns what it did: ``none`` (not owned), ``current``, ``requested``. Never parses
    in the caller."""
    from jobs.paths import PROJECTS_DIR
    from . import owned_graphs
    root = Path(repo_root).resolve()
    for ref in owned_graphs(PROJECTS_DIR / project):
        if ref.repo_root is None or Path(ref.repo_root).resolve() != root:
            continue
        if ref.db_path.exists() and not db_parser_outdated(ref.db_path):
            if _db_meta(ref.db_path, "last_indexed_commit") == _head(root):
                return "current"
        return "requested" if request_rebuild(project, ref) else "none"
    return "none"


@dataclass
class Freshness:
    """What a reader may say about a graph it is about to answer from."""
    state: str                      # fresh | updated | rebuilding | busy | stale
    commits_behind: int | None = None
    changed: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def current(self) -> bool:
        return self.state in ("fresh", "updated")


def ensure_fresh(project_dir: str | Path, ref, graph: GraphStore, *,
                 spawn: bool = True) -> Freshness:
    """Bring *graph* up to its repo's HEAD before a reader out of the daemon answers.

    No change: nothing is written. Up to ``FULL_REBUILD_FILES`` changed files: an
    incremental update now (well under a second each for this repo), the commit marker
    moved when every file landed, the repo's documents re-synced. More, a base rebased
    away, never indexed, or written by an older parser: no rebuild here (it can take
    minutes, past an agent's Bash timeout); with *spawn*, a detached ``--if-stale``
    rebuild child is asked for (``request_rebuild``: one per ten minutes per graph), and
    the answer comes from the graph as it is. The same when an update leaves a file behind
    (it would fail again next time) or the graph was built from another directory.
    Another writer holding the graph, or a bind in progress: answered as it is, at once."""
    from .freshness import check_freshness
    project_dir = Path(project_dir)
    root = ref.repo_root
    if root is not None:
        from core.ddd_bindings import bind_in_progress
        if bind_in_progress(root):
            return Freshness("busy", message="the repo is being bound; answered from the graph as it is")
    fr = check_freshness(graph)
    if not fr.stale:
        return Freshness("fresh")
    behind, changed = fr.commits_behind, list(fr.changed_files)

    def _rebuild(why: str) -> Freshness:
        if spawn and ref.repo:
            if not request_rebuild(project_dir.name, ref):
                return Freshness("stale", behind, changed, why)
            return Freshness("rebuilding", behind, changed,
                             f"{why}; a full rebuild was started, ask again in a minute")
        return Freshness("stale", behind, changed, why)

    big = fr.suggest_full_rebuild or len(changed) > FULL_REBUILD_FILES
    if root is None:
        return _rebuild(fr.reason or "behind the repo")
    stored = graph.get_meta("repo_root")
    if stored and Path(stored).resolve() != Path(root).resolve():
        # the changed files come from the stored repo's history; applied by relative
        # path to another repo they would mix two repos in one graph
        return _rebuild("the graph was built from another directory")
    if big:
        return _rebuild(fr.reason or "behind the repo")
    with write_lock(ref.db_path, wait=False) as held:
        if not held:
            return Freshness("busy", behind, changed,
                             "another process is updating the graph; answered from it as it is")
        upd = update_files(graph, root, changed)
        if upd.complete and fr.current_head:
            graph.set_meta("last_indexed_commit", fr.current_head)
    _sync_docs(project_dir, root, changed)
    if upd.complete:
        return Freshness("updated", behind, changed)
    # A file that fails every time would keep the graph behind for good: the full
    # parse records it as a coverage hole and moves the marker.
    return _rebuild(f"{len(upd.failed)} changed files could not be updated")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python -m core.code_intel.build",
                                 description="Build the code graphs a project owns.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--repo", default=None, help="only this owned repo (its name)")
    ap.add_argument("--full", action="store_true", help="rebuild graphs that exist too")
    ap.add_argument("--if-stale", action="store_true",
                    help="skip a graph another process is writing or that is already current")
    args = ap.parse_args(argv)
    from jobs.paths import PROJECTS_DIR
    # a child: one full parse at a time across the machine
    rows = ensure_graph(PROJECTS_DIR / args.project, repo=args.repo, full=args.full,
                        only_if_stale=args.if_stale, queue=True)
    print(json.dumps({"project": args.project, "graphs": rows}, ensure_ascii=False))
    return 1 if any(r["action"] == "failed" for r in rows) else 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
