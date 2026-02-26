"""Context snapshot cache with version-based invalidation.

This module implements the context snapshot caching layer (PE Fix #4)
to avoid redundant database and filesystem reads during agent turns
and preview polling.  It wraps ``ContextAssembler.assemble()`` with an
in-memory LRU cache keyed by
``(project_id, thread_id, token_budget, context_version_hash)``.

Version counters are lightweight integers incremented at relevant
mutation points (messages added, tasks updated, files changed, etc.).
When all counters are unchanged the cached ``AssembledContext`` is
returned directly; when any counter differs a fresh assembly is
triggered and the result stored.

Key public symbols:

- ``VersionCounters``        — Dataclass holding all version counters
- ``CacheEntry``             — Cached assembly result with version metadata
- ``ContextSnapshotCache``   — Cache manager class with LRU eviction
- ``context_cache``          — Module-level singleton for use by other modules

Validates: Requirements 34.1, 34.2, 34.3, 34.4, 34.5, 38.1
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.context_assembler import AssembledContext, ContextAssembler

logger = logging.getLogger(__name__)


@dataclass
class VersionCounters:
    """Lightweight version counters for cache invalidation.

    Each counter tracks a specific category of mutation that can
    affect the assembled context.  The ``compute_hash()`` method
    produces a deterministic short hash used as part of the cache key.

    Attributes:
        thread_version: Incremented when messages are added to a thread.
        task_version: Incremented when tasks are created/updated/deleted.
        todo_version: Incremented when todos are created/updated/deleted.
        project_files_version: Incremented when project files change.
        memory_version: Incremented when Memory/ files are written.
    """

    thread_version: int = 0
    task_version: int = 0
    todo_version: int = 0
    project_files_version: int = 0
    memory_version: int = 0

    def compute_hash(self) -> str:
        """Deterministic hash of all version counters.

        Returns a 16-character hex digest derived from the concatenated
        counter values.  Identical counter states always produce the
        same hash.
        """
        raw = (
            f"{self.thread_version}:{self.task_version}:"
            f"{self.todo_version}:{self.project_files_version}:"
            f"{self.memory_version}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class CacheEntry:
    """A cached context assembly result with version metadata.

    Attributes:
        context: The assembled context snapshot.
        version_hash: Hash of the version counters at assembly time.
        project_id: Project UUID this entry belongs to.
        thread_id: Optional chat thread ID.
        token_budget: Token budget used for this assembly.
        created_at: Monotonic timestamp for LRU ordering.
    """

    context: "AssembledContext"
    version_hash: str
    project_id: str
    thread_id: Optional[str]
    token_budget: int
    created_at: float = field(default_factory=time.monotonic)


class ContextSnapshotCache:
    """In-memory LRU cache for assembled context snapshots.

    Avoids redundant DB + FS reads when context hasn't changed.
    Cache entries are invalidated when version counters change.
    Uses ``OrderedDict`` for efficient LRU eviction.

    Args:
        max_entries: Maximum number of cache entries before LRU
            eviction kicks in.  Defaults to 50.

    Validates: Requirements 34.1, 34.3, 34.4, 34.5, 38.1
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_entries = max_entries
        # ── In-memory version counters (PE Fix P1/D2) ─────────────────
        #
        # These counters live on the singleton ``context_cache`` instance
        # and are NOT persisted to the database.  They are incremented by
        # manager hooks (task_manager, todo_manager, etc.) whenever a
        # mutation occurs.  Because SwarmAI runs as a single-process
        # sidecar, in-memory tracking is sufficient — there is no second
        # process that could mutate data without incrementing these.
        #
        # The DB-derived counter is ``thread_version``, read from the
        # ``context_version`` column on the ``chat_threads`` table.  It
        # tracks per-thread mutations (new messages, binding changes).
        #
        # Together, the in-memory counters + DB thread_version form the
        # composite ``VersionCounters`` used for cache key hashing.
        # ──────────────────────────────────────────────────────────────
        self._task_version: int = 0
        self._todo_version: int = 0
        self._project_files_version: int = 0
        self._memory_version: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_assemble(
        self,
        assembler: "ContextAssembler",
        project_id: str,
        thread_id: Optional[str],
        token_budget: int,
    ) -> "AssembledContext":
        """Return cached context if versions unchanged, else re-assemble.

        Steps:
        1. Read current version counters from DB.
        2. Compute version hash.
        3. Build cache key from ``(project_id, thread_id, token_budget,
           version_hash)``.
        4. On hit → return cached result (move to end for LRU).
        5. On miss → call ``assembler.assemble()``, store result, evict
           oldest entry if over capacity.

        Validates: Requirements 34.3, 34.4, 38.1
        """
        counters = await self._read_version_counters(project_id, thread_id)
        version_hash = counters.compute_hash()
        key = self._make_key(project_id, thread_id, token_budget, version_hash)

        if key in self._cache:
            # Cache hit — move to end (most recently used)
            self._cache.move_to_end(key)
            logger.info(
                "Cache hit: project=%s thread=%s version=%s",
                project_id,
                thread_id,
                version_hash,
            )
            return self._cache[key].context

        # Cache miss — perform full assembly
        logger.info(
            "Cache miss: project=%s thread=%s version=%s",
            project_id,
            thread_id,
            version_hash,
        )
        context = await assembler.assemble(project_id, thread_id)

        entry = CacheEntry(
            context=context,
            version_hash=version_hash,
            project_id=project_id,
            thread_id=thread_id,
            token_budget=token_budget,
        )
        self._cache[key] = entry
        self._cache.move_to_end(key)

        # LRU eviction
        while len(self._cache) > self._max_entries:
            evicted_key, evicted_entry = self._cache.popitem(last=False)
            logger.debug(
                "Cache eviction (LRU): project=%s thread=%s",
                evicted_entry.project_id,
                evicted_entry.thread_id,
            )

        return context

    def invalidate(self, project_id: str) -> None:
        """Invalidate all cache entries for a project.

        Removes every entry whose ``project_id`` matches the given ID.
        """
        keys_to_remove = [
            k for k, v in self._cache.items() if v.project_id == project_id
        ]
        for k in keys_to_remove:
            del self._cache[k]
        if keys_to_remove:
            logger.info(
                "Cache invalidated: project=%s entries_removed=%d",
                project_id,
                len(keys_to_remove),
            )

    def clear(self) -> None:
        """Clear entire cache."""
        count = len(self._cache)
        self._cache.clear()
        logger.info("Cache cleared: entries_removed=%d", count)
    # ------------------------------------------------------------------
    # Version counter increment hooks (task 4.2)
    # ------------------------------------------------------------------

    def increment_task_version(self) -> int:
        """Increment the task version counter.

        Called when tasks are created, updated, or deleted.

        Returns:
            The new task_version value.
        """
        self._task_version += 1
        logger.debug("task_version incremented to %d", self._task_version)
        return self._task_version

    def increment_todo_version(self) -> int:
        """Increment the todo version counter.

        Called when todos are created, updated, or deleted.

        Returns:
            The new todo_version value.
        """
        self._todo_version += 1
        logger.debug("todo_version incremented to %d", self._todo_version)
        return self._todo_version

    def increment_project_files_version(self) -> int:
        """Increment the project files version counter.

        Called when project files change (create, delete, rename in
        workspace filesystem).

        Returns:
            The new project_files_version value.
        """
        self._project_files_version += 1
        logger.debug(
            "project_files_version incremented to %d",
            self._project_files_version,
        )
        return self._project_files_version

    def increment_memory_version(self) -> int:
        """Increment the memory version counter.

        Called when Memory/ files are written or deleted.

        Returns:
            The new memory_version value.
        """
        self._memory_version += 1
        logger.debug("memory_version incremented to %d", self._memory_version)
        return self._memory_version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _read_version_counters(
        self, project_id: str, thread_id: Optional[str]
    ) -> VersionCounters:
        """Read current version counters from DB and in-memory state.

        Architecture (PE Fix P1/D2):

        - ``thread_version`` is read from the ``context_version`` column
          on the ``chat_threads`` table (DB-canonical, per-thread).
        - ``task_version``, ``todo_version``, ``project_files_version``,
          and ``memory_version`` are in-memory integers on this singleton,
          incremented at mutation points by the respective managers.
        - This split is intentional: thread mutations are per-thread and
          DB-stored; entity mutations are global and in-memory because
          SwarmAI is a single-process sidecar.

        Returns:
            A ``VersionCounters`` instance with current values.
        """
        counters = VersionCounters()

        try:
            from database import db

            # Thread version from context_version column
            if thread_id:
                thread = await db.chat_threads.get(thread_id)
                if thread:
                    counters.thread_version = thread.get("context_version", 0)

            # Use in-memory counters maintained by manager hooks
            counters.task_version = self._task_version
            counters.todo_version = self._todo_version
            counters.project_files_version = self._project_files_version
            counters.memory_version = self._memory_version

        except Exception as exc:
            logger.warning("Failed to read version counters: %s", exc)

        return counters

    @staticmethod
    def _make_key(
        project_id: str,
        thread_id: Optional[str],
        token_budget: int,
        version_hash: str,
    ) -> str:
        """Build cache key from parameters.

        Format: ``{project_id}:{thread_id}:{token_budget}:{version_hash}``
        """
        tid = thread_id or "none"
        return f"{project_id}:{tid}:{token_budget}:{version_hash}"


# Module-level singleton for use by other modules
context_cache = ContextSnapshotCache()
