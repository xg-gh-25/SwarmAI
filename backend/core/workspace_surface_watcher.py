"""Layer-2 filesystem watcher for Canvas live-surfacing (author-agnostic).

WHAT / WHY
----------
Layer-1 (streaming_orchestrator._build_file_write_events) surfaces files the
PARENT agent writes via its own tools. The Claude SDK filters sub-agent sidechain
messages (claude_agent_sdk/types.py:1600), so a file written by a sub-agent / CLI
subprocess / hook is invisible to Layer-1. This watcher catches those writes at
the filesystem and routes them — via surface_injection — onto the sole streaming
session's live SSE stream (see surface_injection.py for the bleed-proof
attribution rule). Additive: Layer-1 per-tool emit + the pipeline-finish
sweep_run_changes fallback are unchanged.

DESIGN (mirrors code_intel/watcher.py, the proven in-production pattern)
------------------------------------------------------------------------
- watchfiles.awatch over SwarmWS with a LIGHTWEIGHT ``watch_filter`` (extension +
  path-segment only — NO git subprocess in the filter; that would be one
  check-ignore per event = a flood).
- On a debounced batch, the review verdict runs ONCE via
  ``needs_human_review_batch`` OFF-LOOP (asyncio.to_thread) — one check-ignore
  subprocess per owning tree, not per file.
- Only ``content``/``knowledge`` verdicts are surfaced (matches the frontend
  Canvas gate: useReferencedFiles drops process/source from the rail,
  useCanvasAutoSurface pops only content/knowledge). ``source`` aggregates at
  pipeline finish; ``process`` never surfaces.
- daemon/hive only (started from main.py, same gate as CodeIntelWatcher).

FAIL-SAFE: the watch loop is wrapped so a watcher error never crashes the daemon
(mirrors CodeIntelWatcher._watch_loop). A per-batch exception is logged and
skipped — surfacing is best-effort, never load-bearing.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core import surface_injection

logger = logging.getLogger(__name__)

_DEBOUNCE_MS = 2000  # match CodeIntelWatcher; coalesces a burst of writes
_STEP_MS = 200
_MAX_BATCH = 50  # a larger batch is a checkout/bulk op — cap what we surface

# Extensions that can be user-facing deliverables (content/knowledge docs +
# common report/artifact formats). The batch verdict (needs_human_review) makes
# the FINAL call; this is just the cheap first-pass filter so we don't verdict
# every binary/cache write.
_WATCHED_EXTENSIONS = {
    ".md", ".txt", ".html", ".htm", ".json", ".csv", ".tsv",
    ".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".svg",
}

# Path segments that never carry a surfaceable deliverable — skip in the cheap
# filter so their bulk writes never even reach the (more expensive) batch verdict.
# Mirrors code_intel/watcher.py:_SKIP_PATH_SEGMENTS + build/cache noise. NOTE:
# needs_human_review is the authority that ultimately drops noise; this is a
# performance pre-filter, deliberately a SUPERSET-safe subset (only skip what is
# unambiguously non-deliverable).
_SKIP_SEGMENTS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache",
    "DailyActivity", "JobResults", "Signals", "EvalHistory",
    ".artifacts", "Services",
}


class WorkspaceSurfaceWatcher:
    """Background watcher that surfaces author-agnostic writes to the Canvas."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start watching. Idempotent."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("WorkspaceSurfaceWatcher started at %s", self._root)

    async def stop(self) -> None:
        """Stop watching. Idempotent."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("WorkspaceSurfaceWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def _filter(self, change, path: str) -> bool:
        """Lightweight per-event filter (NO subprocess). Extension + segment only."""
        p = Path(path)
        if any(part in _SKIP_SEGMENTS for part in p.parts):
            return False
        return p.suffix.lower() in _WATCHED_EXTENSIONS

    async def _watch_loop(self) -> None:
        try:
            import watchfiles
        except ImportError:
            logger.warning("watchfiles not installed — WorkspaceSurfaceWatcher disabled")
            self._running = False
            return

        try:
            async for changes in watchfiles.awatch(
                self._root,
                watch_filter=self._filter,
                debounce=_DEBOUNCE_MS,
                step=_STEP_MS,
            ):
                if not self._running:
                    break
                # changes: set of (Change, path). Surface only additions/mods
                # (a deletion is not a "written deliverable").
                paths = [
                    path for change, path in changes
                    if getattr(change, "name", "") != "deleted"
                ]
                if not paths:
                    continue
                if len(paths) > _MAX_BATCH:
                    logger.info(
                        "WorkspaceSurfaceWatcher: large batch (%d) — surfacing first %d",
                        len(paths), _MAX_BATCH,
                    )
                    paths = paths[:_MAX_BATCH]
                try:
                    await self._handle_batch(paths)
                except Exception as exc:  # noqa: BLE001
                    # Best-effort: one bad batch never kills the loop.
                    logger.warning("WorkspaceSurfaceWatcher batch failed: %s", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.error("WorkspaceSurfaceWatcher loop error: %s", exc)
        finally:
            self._running = False

    async def _handle_batch(self, paths: list[str]) -> None:
        """Classify a batch (off-loop) and publish content/knowledge events.

        Verdict runs via needs_human_review_batch in a thread (one check-ignore
        subprocess per owning tree). Only content/knowledge surface; source
        aggregates at pipeline finish and process never surfaces (matches the
        frontend Canvas gate).
        """
        from core.needs_human_review import needs_human_review_batch

        verdicts = await asyncio.to_thread(
            needs_human_review_batch, paths, "written",
            swarmws_root=str(self._root),
        )
        for raw_path, verdict in verdicts.items():
            if not verdict.review_worthy:
                continue
            if verdict.kind not in ("content", "knowledge"):
                # source → finish-time PR aggregate; process → never surface.
                continue
            abs_path = str(Path(raw_path).resolve())
            try:
                # Normalize separators to '/' (fix4, run_bfbbe0fd): on Windows a
                # WindowsPath str has backslashes, but Layer-1 + the frontend
                # path-key dedup Map use forward slashes — a mismatch double-pops
                # the same file. Match the codebase convention (.replace).
                rel = str(Path(abs_path).relative_to(self._root.resolve())).replace("\\", "/")
            except ValueError:
                # Path is outside SwarmWS — should not happen (awatch is rooted
                # there), but if it does, DO NOT publish an absolute string as the
                # `path` field (breaks the event contract + frontend path-key dedup).
                # Skip + log rather than emit a malformed event.
                logger.warning(
                    "WorkspaceSurfaceWatcher: path %s not under root %s — skipping",
                    abs_path, self._root,
                )
                continue
            event = {
                "type": "file_changed",
                "path": rel,
                "absolutePath": abs_path,
                "kind": verdict.kind,
                "operation": "written",
            }
            surface_injection.publish_file_event(event)
