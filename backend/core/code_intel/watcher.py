"""
File system watcher for Code Intelligence auto-refresh.

Watches indexed project directories for source file changes and triggers
incremental reindex after a debounce period. Only active in daemon/hive mode.

Uses watchfiles (Rust-based, async-native, cross-platform).
"""

import asyncio
import logging
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

# File extensions worth watching (from parser.py LANGUAGE_MAP)
_WATCHED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go",
    ".rs", ".rb", ".cs", ".kt", ".kts", ".php", ".swift",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
}

# Directories to skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".eggs", "egg-info", ".next", ".nuxt",
}

# Path segments that indicate non-source directories (never contain indexable code)
# These are workspace/knowledge dirs whose bulk writes trigger jetsam kills
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
        self._graph = graph_store
        self._task: asyncio.Task | None = None
        self._running = False

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
                source_changes = [f for f in changed_files if f.suffix in _WATCHED_EXTENSIONS]
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

    def _filter(self, change, path: str) -> bool:
        """Only watch parseable source files, skip noise directories."""
        p = Path(path)
        parts = p.parts
        # Skip noise directories (build artifacts, caches)
        if any(part in _SKIP_DIRS for part in parts):
            return False
        # Skip non-source workspace directories (knowledge, context, attachments)
        if any(part in _SKIP_PATH_SEGMENTS for part in parts):
            return False
        return p.suffix in _WATCHED_EXTENSIONS

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

            # Phase 1: Single graph update (one FTS rebuild, not per-chunk)
            await executors.run_in(
                "io",
                self._graph.incremental_update,
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

    def _extract_routes_only(self, relative_files: list[str], abs_files: list[Path]):
        """Extract routes for changed files (runs in thread, chunked).

        Separated from graph update so that graph update (single FTS rebuild)
        runs once, while route extraction (memory-heavy tree-sitter) is chunked.
        """
        from . import extract_and_store_routes
        from .parser import LANGUAGE_MAP

        for abs_path, rel_path in zip(abs_files, relative_files):
            if abs_path.suffix in LANGUAGE_MAP and abs_path.exists():
                try:
                    content = abs_path.read_text(errors="replace")
                    language = LANGUAGE_MAP[abs_path.suffix]
                    extract_and_store_routes(self._graph, rel_path, content, language)
                except Exception:
                    pass  # Route extraction is best-effort


# ── Module-level watcher registry ──────────────────────────────────────

_watchers: dict[str, CodeIntelWatcher] = {}
_watcher_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Get or create the module-level asyncio lock."""
    global _watcher_lock
    if _watcher_lock is None:
        _watcher_lock = asyncio.Lock()
    return _watcher_lock


async def start_watcher(project_name: str, project_root: Path, graph_store: 'GraphStore') -> bool:
    """Start a watcher for a project. Returns True if started, False if at capacity."""
    lock = _get_lock()

    async with lock:
        if project_name in _watchers and _watchers[project_name].is_running:
            return True  # Already running

        if len(_watchers) >= _MAX_WATCHERS:
            # Evict oldest idle watcher
            for name, w in list(_watchers.items()):
                if not w.is_running:
                    del _watchers[name]
                    break
            else:
                logger.warning(f"CodeIntelWatcher: at capacity ({_MAX_WATCHERS}), cannot start for {project_name}")
                return False

        watcher = CodeIntelWatcher(project_name, project_root, graph_store)
        await watcher.start()
        _watchers[project_name] = watcher
        return True


async def stop_watcher(project_name: str):
    """Stop a specific project watcher."""
    lock = _get_lock()

    async with lock:
        if project_name in _watchers:
            await _watchers[project_name].stop()
            del _watchers[project_name]


async def stop_all_watchers():
    """Stop all watchers. Called on shutdown."""
    for name in list(_watchers.keys()):
        await stop_watcher(name)
