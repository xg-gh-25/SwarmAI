"""Writing a code graph from a repo: the one full rebuild and the one incremental update.

Every path that rebuilds a graph (the reindex job, ``bind_repo``, the Brain Hub
re-index button, a library mount) calls ``rebuild``; every path that re-stores
changed files (the commit hook, the session-start health check) calls
``update_files``, and the file watcher, which chunks its work, calls
``GraphStore.incremental_update`` and ``routes_for`` itself. Before this there were
four rebuilds and three incremental paths, and they disagreed: two rebuilds stored
no routes, one marked an emptied graph as fresh, and ``clear()`` kept the routes of
deleted handlers.

What every rebuild now does the same way:
- An empty parse leaves the graph exactly as it was and unstamped (a transient empty
  parse — a repo not yet on disk, a parser teardown — must not erase a good graph or
  make an empty one read fresh). A graph that has no ``repo_root`` yet gets it, so a
  first bind of an empty repo still knows where it points.
- Coverage holes and parse status are stored (the analysis reports them).
- Routes (and router prefixes) are extracted, unless the caller says routes have no
  reader (a library mount). A prefix failure is logged and never fails the write.
- ``head`` is the commit the CALLER read before the parse: a commit that lands while
  the parse runs stays unindexed and the next incremental update takes it.
- The write runs under the graph's write lock, so a reader on the same store sees the
  old graph or the new one, never an emptied one. Parsing and route extraction run
  before the lock.

The caller owns everything else: which repo, the ownership check, a bind lock,
mount-specific meta.
"""
from __future__ import annotations

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
    from .parser import LANGUAGE_MAP
    from .route_parser import extract_routes
    full = repo_root / rel_path
    lang = LANGUAGE_MAP.get(full.suffix)
    if lang is None or not full.is_file():
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
            routes: bool = True, stamp: bool = True) -> RebuildResult:
    """Replace *graph* with a fresh parse of *repo_root* (rules in the module doc).

    *stamp*: mark the graph fully indexed at *head*. A library mount passes False —
    its freshness is the mount's own ``last_synced``.
    """
    from .parser import parse_repo_with_coverage

    root = Path(repo_root).resolve()
    out = parse_repo_with_coverage(root)
    results = out.results
    if not results:
        if graph.get_meta("repo_root") is None:
            graph.set_meta("repo_root", str(root))
        return RebuildResult(empty=True, parse_status=out.status,
                             coverage_holes=out.coverage_holes)
    found = [r for p in results for r in (_file_routes(root, p.file_path) if routes else [])]
    with graph._conn_lock:
        graph.clear()
        graph.bulk_insert(results, repo_root=root)
        graph.set_meta("parse_status", out.status)
        graph.set_meta("coverage_holes", json.dumps(out.coverage_holes))
        if head:
            graph.set_meta("coverage_commit", head)
        if found:
            graph.insert_routes(found)
            _apply_prefixes(graph, root)
        if stamp:
            graph.mark_indexed(full=True, head=head)
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
