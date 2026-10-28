"""
File system watcher for Code Intelligence auto-refresh.

Watches indexed project directories for source file changes and triggers
incremental reindex after a debounce period. Only active in daemon/hive mode.

Uses watchfiles (Rust-based, async-native, cross-platform). Paths the project's
git ignores (PyInstaller output under desktop/src-tauri/binaries/, build dirs)
are dropped per batch with one `git check-ignore --stdin` call: the static skip
list below missed them, and every build re-indexed ~170 generated files.
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from core import executors

if TYPE_CHECKING:
    from .graph_store import GraphStore

logger = logging.getLogger(__name__)

# Max concurrent watchers (memory budget: ~50KB each)
_MAX_WATCHERS = 4
_DEBOUNCE_MS = 2000  # 2 seconds
_MAX_BATCH_SIZE = 50  # Skip incremental when too many files change at once
_CHUNK_SIZE = 20  # Process files in chunks to limit memory spikes

# Which files the graph holds is parser.is_indexed_path (one answer for every path).
# On top of it the watcher skips these path segments INSIDE the repo: workspace /
# knowledge dirs whose bulk writes triggered jetsam kills. They are matched on the
# path relative to the repo root, never the absolute path: bound repos are cloned
# under ~/.swarm-ai/bindings/, so an absolute-path match on ".swarm-ai" would skip
# every file of every bound repo.
_SKIP_PATH_SEGMENTS = {
    "Knowledge", "DailyActivity", ".context", ".swarm-ai",
    "Attachments", "DailyBriefs", "JobResults", "Signals",
    "EvalHistory", "Eval", ".artifacts", "Services",
}


class CodeIntelWatcher:
    """Background watcher that triggers incremental index on file changes."""

    def __init__(self, project_name: str, project_root: Path, graph_store: 'GraphStore'):
        self._project_name = project_name
        self._root = project_root
        self._started_graph = graph_store
        self._task: asyncio.Task | None = None
        self._running = False

    def _current_graph(self) -> 'GraphStore':
        """The CURRENT cached store for this watcher's db, else the one started with
        (blocking: it reads bindings.yaml and may open the db, so call it off the loop).

        A db re-created on disk gets a new store from ``load_project_graphs`` (and the
        old one is closed); a watcher still writing the old store would fail.
        """
        try:
            from . import load_project_graphs
            for _ref, current in load_project_graphs(self._project_name):
                if current._db_path == self._started_graph._db_path:
                    return current
        except Exception as exc:  # noqa: BLE001 -- keep the graph we started with
            logger.debug("CodeIntelWatcher %s: current graph lookup failed: %s",
                         self._project_name, exc)
        return self._started_graph

    async def start(self):
        """Start watching. Idempotent — calling twice is safe."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"CodeIntelWatcher started for {self._project_name} at {self._root}")

    async def stop(self):
        """Stop watching. Idempotent."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"CodeIntelWatcher stopped for {self._project_name}")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _watch_loop(self):
        """Core watch loop using watchfiles async generator."""
        try:
            import watchfiles
        except ImportError:
            logger.warning("watchfiles not installed — FS watcher disabled")
            self._running = False
            return

        try:
            async for changes in watchfiles.awatch(
                self._root,
                watch_filter=self._filter,
                debounce=_DEBOUNCE_MS,
                step=200,
            ):
                if not self._running:
                    break
                changed_files = [Path(path) for _, path in changes]
                source_changes = [f for f in changed_files if self._in_repo(f) is not None]
                if source_changes:
                    source_changes = await executors.run_in(
                        "io", _drop_git_ignored, self._root, source_changes)
                if not source_changes:
                    continue
                # Gate: large batches of source changes are unusual (typically a
                # git checkout/rebase). Process them but with extra chunking and
                # a warning log. The _filter already excluded non-source dirs, so
                # anything here IS source code — don't drop it.
                if len(source_changes) > _MAX_BATCH_SIZE:
                    logger.warning(
                        f"CodeIntelWatcher: large batch of {len(source_changes)} source "
                        f"changes for {self._project_name} — processing in chunks"
                    )
                await self._trigger_incremental(source_changes)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"CodeIntelWatcher error for {self._project_name}: {e}")
        finally:
            self._running = False

    def _in_repo(self, path: Path) -> Path | None:
        """*path* relative to the repo root when the graph holds it, else None."""
        from .parser import is_indexed_path
        rel = None
        for root in (self._root, self._root.resolve()):
            try:
                rel = path.relative_to(root)
                break
            except ValueError:
                continue
        if rel is None or any(part in _SKIP_PATH_SEGMENTS for part in rel.parts):
            return None
        return rel if is_indexed_path(rel) else None

    def _filter(self, change, path: str) -> bool:
        """Only files the graph holds (parser.is_indexed_path), relative to the root."""
        return self._in_repo(Path(path)) is not None

    async def _trigger_incremental(self, changed_files: list[Path]):
        """Run incremental reindex + route extraction in thread pool.

        Two-phase approach to balance memory and performance:
        1. Graph update (incremental_update) runs ONCE for all files — single FTS rebuild
        2. Route extraction runs in chunks of _CHUNK_SIZE — limits tree-sitter memory
        """
        try:
            relative_files = []
            abs_files_valid = []
            for f in changed_files:
                try:
                    relative_files.append(str(f.relative_to(self._root)))
                    abs_files_valid.append(f)
                except ValueError:
                    continue

            if not relative_files:
                return

            # A bind is clearing / re-cloning this checkout and rebuilds the whole graph
            # itself: its deletes and creates are not edits to replay.
            from core.ddd_bindings import bind_in_progress
            if bind_in_progress(self._root):
                logger.debug("CodeIntelWatcher: %s is being bound — batch skipped",
                             self._project_name)
                return

            # The store to write, resolved off the loop (it may read bindings.yaml
            # and open the db).
            graph = await executors.run_in("io", self._current_graph)

            # Phase 1: Single graph update (one FTS rebuild, not per-chunk)
            await executors.run_in(
                "io",
                graph.incremental_update,
                self._root,
                relative_files,
            )

            # Phase 2: Route extraction in chunks (tree-sitter parsing is memory-heavy)
            for i in range(0, len(relative_files), _CHUNK_SIZE):
                if not self._running:
                    break
                chunk_rel = relative_files[i:i + _CHUNK_SIZE]
                chunk_abs = abs_files_valid[i:i + _CHUNK_SIZE]
                await executors.run_in(
                    "io",
                    self._extract_routes_only,
                    graph,
                    chunk_rel,
                    chunk_abs,
                )
                # Yield between chunks to avoid event loop starvation
                if i + _CHUNK_SIZE < len(relative_files):
                    await asyncio.sleep(0.1)

            logger.debug(
                f"CodeIntelWatcher: incremental reindex for {self._project_name} "
                f"({len(relative_files)} files)"
            )
        except Exception as e:
            logger.warning(f"CodeIntelWatcher incremental reindex failed: {e}")

    def _extract_routes_only(self, graph: 'GraphStore', relative_files: list[str],
                             abs_files: list[Path]):
        """Re-store the routes of one chunk of changed files (runs in a thread).

        Separated from the graph update so that the update (one FTS rebuild) runs
        once while route extraction (memory-heavy tree-sitter) is chunked. Uses the
        same route store as every other writer (build.routes_for), which re-applies
        the include_router prefixes, or a route query after a watcher batch would
        resolve to the wrong handler.
        """
        from .build import routes_for
        existing = [rel for rel, p in zip(relative_files, abs_files) if p.exists()]
        if existing:
            routes_for(graph, self._root, existing)


def _drop_git_ignored(root: Path, files: list[Path]) -> list[Path]:
    """The files git does not ignore in the repo at ``root`` (blocking: run off
    the loop). One subprocess for the whole batch. Not a git repo, or git
    failing, keeps every file: the watcher then behaves as before."""
    from utils.stdin_file import run_with_stdin_file   # never input= (module doc)
    try:
        proc = run_with_stdin_file(
            ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
            "\0".join(str(f) for f in files),
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("CodeIntelWatcher: git check-ignore failed: %s", exc)
        return files
    if proc.returncode not in (0, 1):  # 1 = none ignored; 128 = not a repo
        return files
    ignored = {p for p in proc.stdout.decode(errors="replace").split("\0") if p}
    return [f for f in files if str(f) not in ignored]


# ── Module-level watcher registry ──────────────────────────────────────

# db path → watcher: one watcher per graph, so each bound repo of a project is watched.
_watchers: dict[str, CodeIntelWatcher] = {}
_watcher_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Get or create the module-level asyncio lock."""
    global _watcher_lock
    if _watcher_lock is None:
        _watcher_lock = asyncio.Lock()
    return _watcher_lock


async def start_watcher(project_name: str, project_root: Path, graph_store: 'GraphStore') -> bool:
    """Start a watcher for one of a project's graphs (keyed by its db). Returns True if
    started or already running, False if at capacity (``_MAX_WATCHERS`` graphs)."""
    lock = _get_lock()
    key = str(graph_store._db_path)

    async with lock:
        if key in _watchers and _watchers[key].is_running:
            return True  # Already running

        if len(_watchers) >= _MAX_WATCHERS:
            # Evict oldest idle watcher
            for k, w in list(_watchers.items()):
                if not w.is_running:
                    del _watchers[k]
                    break
            else:
                logger.warning(f"CodeIntelWatcher: at capacity ({_MAX_WATCHERS} graphs), "
                               f"not watching {project_name} at {project_root}")
                return False

        watcher = CodeIntelWatcher(project_name, project_root, graph_store)
        await watcher.start()
        _watchers[key] = watcher
        return True


async def stop_watcher(project_name: str):
    """Stop every watcher of a project (one per graph)."""
    lock = _get_lock()

    async with lock:
        for key, w in list(_watchers.items()):
            if w._project_name == project_name:
                await w.stop()
                del _watchers[key]


async def stop_all_watchers():
    """Stop all watchers. Called on shutdown."""
    for name in {w._project_name for w in list(_watchers.values())}:
        await stop_watcher(name)
