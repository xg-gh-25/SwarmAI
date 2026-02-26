"""Property-based and unit tests for ContextSnapshotCache.

This module tests the context snapshot cache defined in
``backend/core/context_snapshot_cache.py``, verifying cache correctness
with version-based invalidation.

Testing methodology: property-based testing with Hypothesis for
Property 11 (cache correctness), plus unit tests for cache hit/miss,
LRU eviction, and ``VersionCounters.compute_hash()`` determinism.

Key properties and invariants verified:

- **Property 11 — Cache correctness**: Cache returns cached result when
  all version counters are unchanged; triggers fresh assembly when any
  counter changes.
- ``VersionCounters.compute_hash()`` is deterministic (same counters →
  same hash).
- LRU eviction removes oldest entries when max capacity exceeded.
- ``invalidate()`` removes all entries for a given project.
- ``clear()`` empties the entire cache.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck, assume
import hypothesis.strategies as st

from core.context_snapshot_cache import (
    ContextSnapshotCache,
    VersionCounters,
    CacheEntry,
)
from core.context_assembler import (
    AssembledContext,
    ContextAssembler,
    ContextLayer,
)


# ── Hypothesis strategies ──────────────────────────────────────────────


@st.composite
def version_counters_strategy(draw: st.DrawFn) -> VersionCounters:
    """Draw random VersionCounters with non-negative integer fields."""
    return VersionCounters(
        thread_version=draw(st.integers(min_value=0, max_value=10_000)),
        task_version=draw(st.integers(min_value=0, max_value=10_000)),
        todo_version=draw(st.integers(min_value=0, max_value=10_000)),
        project_files_version=draw(st.integers(min_value=0, max_value=10_000)),
        memory_version=draw(st.integers(min_value=0, max_value=10_000)),
    )


def _make_assembled_context(marker: str = "default") -> AssembledContext:
    """Create a minimal AssembledContext with a distinguishable marker."""
    return AssembledContext(
        layers=[
            ContextLayer(
                layer_number=1,
                name="System Prompt",
                source_path="system-prompts.md",
                content=f"content-{marker}",
                token_count=10,
            )
        ],
        total_token_count=10,
        budget_exceeded=False,
        token_budget=10_000,
    )


# ── Property 11: Cache correctness ────────────────────────────────────


class TestPropertyCacheCorrectness:
    """Property 11: Cache correctness.

    *For any* two consecutive assembly requests with identical version
    counters (thread_version, task_version, todo_version,
    project_files_version, memory_version), the cache SHALL return the
    previously computed result without re-reading the database or
    filesystem.  If any version counter has changed, the cache SHALL
    trigger a fresh assembly.

    Feature: swarmws-intelligence, Property 11: Cache correctness

    **Validates: Requirements 34.3, 34.4**
    """

    @given(counters=version_counters_strategy())
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_cache_returns_cached_result_when_counters_unchanged(
        self, counters: VersionCounters
    ) -> None:
        """Cache hit: identical counters → assembler called only once.

        **Validates: Requirements 34.3**
        """
        cache = ContextSnapshotCache(max_entries=50)
        project_id = "proj-001"
        thread_id = "thread-001"
        token_budget = 10_000

        assembled = _make_assembled_context("first-call")
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(return_value=assembled)

        async def _run() -> None:
            # Patch _read_version_counters to return the same counters
            with patch.object(
                cache,
                "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                # First call — cache miss, assembler invoked
                result1 = await cache.get_or_assemble(
                    assembler, project_id, thread_id, token_budget
                )
                # Second call — same counters, should be cache hit
                result2 = await cache.get_or_assemble(
                    assembler, project_id, thread_id, token_budget
                )

            # Assembler should have been called exactly once (first call)
            assert assembler.assemble.call_count == 1, (
                f"Expected assembler called once, got {assembler.assemble.call_count}. "
                f"Cache should return cached result on second call with "
                f"unchanged counters: {counters}"
            )
            # Both results should be the same object
            assert result1 is result2, (
                "Cache should return the exact same object on cache hit"
            )

        asyncio.get_event_loop().run_until_complete(_run())

    @given(
        counters_a=version_counters_strategy(),
        counters_b=version_counters_strategy(),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_cache_triggers_fresh_assembly_when_any_counter_changes(
        self, counters_a: VersionCounters, counters_b: VersionCounters
    ) -> None:
        """Cache miss: changed counters → assembler called again.

        **Validates: Requirements 34.4**
        """
        # Ensure the two counter sets produce different hashes
        assume(counters_a.compute_hash() != counters_b.compute_hash())

        cache = ContextSnapshotCache(max_entries=50)
        project_id = "proj-002"
        thread_id = "thread-002"
        token_budget = 10_000

        assembled_first = _make_assembled_context("first")
        assembled_second = _make_assembled_context("second")
        call_count = 0

        async def mock_assemble(pid: str, tid: Optional[str] = None) -> AssembledContext:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return assembled_first
            return assembled_second

        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(side_effect=mock_assemble)

        counter_sequence = iter([counters_a, counters_b])

        async def _read_counters(pid: str, tid: Optional[str]) -> VersionCounters:
            return next(counter_sequence)

        async def _run() -> None:
            with patch.object(
                cache,
                "_read_version_counters",
                new=AsyncMock(side_effect=_read_counters),
            ):
                # First call with counters_a
                result1 = await cache.get_or_assemble(
                    assembler, project_id, thread_id, token_budget
                )
                # Second call with counters_b (different hash)
                result2 = await cache.get_or_assemble(
                    assembler, project_id, thread_id, token_budget
                )

            # Assembler should have been called twice (both misses)
            assert assembler.assemble.call_count == 2, (
                f"Expected assembler called twice, got {assembler.assemble.call_count}. "
                f"Changed counters should trigger fresh assembly. "
                f"hash_a={counters_a.compute_hash()}, hash_b={counters_b.compute_hash()}"
            )
            # Results should be different objects
            assert result1 is not result2, (
                "Changed counters should produce different result objects"
            )

        asyncio.get_event_loop().run_until_complete(_run())


# ── Unit Tests: VersionCounters.compute_hash() determinism ─────────────


class TestComputeHashDeterminism:
    """Unit tests for ``VersionCounters.compute_hash()`` determinism.

    Verifies that identical counter states always produce the same hash
    and that different counter states produce different hashes.

    **Validates: Requirements 34.5**
    """

    def test_same_counters_produce_same_hash(self) -> None:
        """Identical counters → identical hash."""
        vc1 = VersionCounters(
            thread_version=1,
            task_version=2,
            todo_version=3,
            project_files_version=4,
            memory_version=5,
        )
        vc2 = VersionCounters(
            thread_version=1,
            task_version=2,
            todo_version=3,
            project_files_version=4,
            memory_version=5,
        )
        assert vc1.compute_hash() == vc2.compute_hash()

    def test_hash_is_16_char_hex(self) -> None:
        """Hash output is a 16-character hex string."""
        vc = VersionCounters()
        h = vc.compute_hash()
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_counters_produce_different_hash(self) -> None:
        """Changing any single counter changes the hash."""
        base = VersionCounters(
            thread_version=1,
            task_version=2,
            todo_version=3,
            project_files_version=4,
            memory_version=5,
        )
        base_hash = base.compute_hash()

        variants = [
            VersionCounters(thread_version=99, task_version=2, todo_version=3, project_files_version=4, memory_version=5),
            VersionCounters(thread_version=1, task_version=99, todo_version=3, project_files_version=4, memory_version=5),
            VersionCounters(thread_version=1, task_version=2, todo_version=99, project_files_version=4, memory_version=5),
            VersionCounters(thread_version=1, task_version=2, todo_version=3, project_files_version=99, memory_version=5),
            VersionCounters(thread_version=1, task_version=2, todo_version=3, project_files_version=4, memory_version=99),
        ]
        for variant in variants:
            assert variant.compute_hash() != base_hash, (
                f"Changing a counter should change the hash: {variant}"
            )

    def test_default_counters_hash_is_stable(self) -> None:
        """Default (all-zero) counters produce a repeatable hash."""
        h1 = VersionCounters().compute_hash()
        h2 = VersionCounters().compute_hash()
        assert h1 == h2

    def test_hash_called_multiple_times_is_idempotent(self) -> None:
        """Calling compute_hash() multiple times on the same instance is stable."""
        vc = VersionCounters(thread_version=7, task_version=3, todo_version=1, project_files_version=0, memory_version=42)
        results = [vc.compute_hash() for _ in range(10)]
        assert len(set(results)) == 1


# ── Unit Tests: Cache hit and cache miss ───────────────────────────────


class TestCacheHitMiss:
    """Unit tests for cache hit and cache miss behaviour.

    Verifies that ``get_or_assemble()`` returns cached results on hit
    and calls the assembler on miss.

    **Validates: Requirements 34.1, 34.3**
    """

    def test_first_call_is_cache_miss(self) -> None:
        """First call for a key always triggers assembly."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembled = _make_assembled_context("miss")
        assembler.assemble = AsyncMock(return_value=assembled)

        counters = VersionCounters(thread_version=1)

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                result = await cache.get_or_assemble(
                    assembler, "proj-a", "thread-a", 10_000
                )
            assert assembler.assemble.call_count == 1
            assert result is assembled

        asyncio.get_event_loop().run_until_complete(_run())

    def test_second_call_same_counters_is_cache_hit(self) -> None:
        """Second call with unchanged counters returns cached result."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembled = _make_assembled_context("hit")
        assembler.assemble = AsyncMock(return_value=assembled)

        counters = VersionCounters(thread_version=5, task_version=3)

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                r1 = await cache.get_or_assemble(
                    assembler, "proj-b", "thread-b", 10_000
                )
                r2 = await cache.get_or_assemble(
                    assembler, "proj-b", "thread-b", 10_000
                )
            assert assembler.assemble.call_count == 1
            assert r1 is r2

        asyncio.get_event_loop().run_until_complete(_run())

    def test_different_project_ids_are_separate_entries(self) -> None:
        """Different project_ids produce separate cache entries."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(
            side_effect=[
                _make_assembled_context("proj-x"),
                _make_assembled_context("proj-y"),
            ]
        )
        counters = VersionCounters()

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                r1 = await cache.get_or_assemble(
                    assembler, "proj-x", None, 10_000
                )
                r2 = await cache.get_or_assemble(
                    assembler, "proj-y", None, 10_000
                )
            assert assembler.assemble.call_count == 2
            assert r1 is not r2

        asyncio.get_event_loop().run_until_complete(_run())

    def test_none_thread_id_is_valid_cache_key(self) -> None:
        """thread_id=None produces a valid, cacheable key."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembled = _make_assembled_context("no-thread")
        assembler.assemble = AsyncMock(return_value=assembled)
        counters = VersionCounters()

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                r1 = await cache.get_or_assemble(
                    assembler, "proj-c", None, 10_000
                )
                r2 = await cache.get_or_assemble(
                    assembler, "proj-c", None, 10_000
                )
            assert assembler.assemble.call_count == 1
            assert r1 is r2

        asyncio.get_event_loop().run_until_complete(_run())


# ── Unit Tests: Cache invalidation on version change ───────────────────


class TestCacheInvalidation:
    """Unit tests for cache invalidation when version counters change.

    Verifies that changing any version counter causes a cache miss,
    and that ``invalidate()`` and ``clear()`` remove entries correctly.

    **Validates: Requirements 34.1, 34.4, 34.5**
    """

    def test_changed_counters_trigger_fresh_assembly(self) -> None:
        """Changing counters between calls triggers re-assembly."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(
            side_effect=[
                _make_assembled_context("v1"),
                _make_assembled_context("v2"),
            ]
        )
        counters_v1 = VersionCounters(thread_version=1)
        counters_v2 = VersionCounters(thread_version=2)
        call_idx = 0

        async def read_counters(pid: str, tid: Optional[str]) -> VersionCounters:
            nonlocal call_idx
            call_idx += 1
            return counters_v1 if call_idx == 1 else counters_v2

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(side_effect=read_counters),
            ):
                r1 = await cache.get_or_assemble(
                    assembler, "proj-d", "thread-d", 10_000
                )
                r2 = await cache.get_or_assemble(
                    assembler, "proj-d", "thread-d", 10_000
                )
            assert assembler.assemble.call_count == 2
            assert r1 is not r2

        asyncio.get_event_loop().run_until_complete(_run())

    def test_invalidate_removes_project_entries(self) -> None:
        """``invalidate(project_id)`` removes all entries for that project."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(return_value=_make_assembled_context("inv"))
        counters = VersionCounters()

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                await cache.get_or_assemble(
                    assembler, "proj-e", "thread-e1", 10_000
                )
                await cache.get_or_assemble(
                    assembler, "proj-e", "thread-e2", 10_000
                )
                assert assembler.assemble.call_count == 2

                # Invalidate project
                cache.invalidate("proj-e")

                # Next calls should be cache misses
                await cache.get_or_assemble(
                    assembler, "proj-e", "thread-e1", 10_000
                )
            assert assembler.assemble.call_count == 3

        asyncio.get_event_loop().run_until_complete(_run())

    def test_invalidate_does_not_affect_other_projects(self) -> None:
        """``invalidate()`` only removes entries for the specified project."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(
            side_effect=[
                _make_assembled_context("keep"),
                _make_assembled_context("remove"),
                _make_assembled_context("re-remove"),
            ]
        )
        counters = VersionCounters()

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                await cache.get_or_assemble(
                    assembler, "proj-keep", None, 10_000
                )
                await cache.get_or_assemble(
                    assembler, "proj-remove", None, 10_000
                )
                cache.invalidate("proj-remove")

                # proj-keep should still be cached (no new assembly)
                r = await cache.get_or_assemble(
                    assembler, "proj-keep", None, 10_000
                )
            # Only 2 assemblies: initial for keep + initial for remove
            # The third call for proj-keep is a cache hit
            assert assembler.assemble.call_count == 2

        asyncio.get_event_loop().run_until_complete(_run())

    def test_clear_removes_all_entries(self) -> None:
        """``clear()`` empties the entire cache."""
        cache = ContextSnapshotCache(max_entries=10)
        assembler = MagicMock(spec=ContextAssembler)
        assembler.assemble = AsyncMock(return_value=_make_assembled_context("clr"))
        counters = VersionCounters()

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(return_value=counters),
            ):
                await cache.get_or_assemble(
                    assembler, "proj-f", None, 10_000
                )
                assert assembler.assemble.call_count == 1

                cache.clear()

                await cache.get_or_assemble(
                    assembler, "proj-f", None, 10_000
                )
            assert assembler.assemble.call_count == 2

        asyncio.get_event_loop().run_until_complete(_run())


# ── Unit Tests: LRU eviction ──────────────────────────────────────────


class TestLRUEviction:
    """Unit tests for LRU eviction when max entries exceeded.

    Verifies that the cache evicts the least recently used entry when
    the maximum capacity is reached.

    **Validates: Requirements 34.5**
    """

    def test_eviction_when_max_entries_exceeded(self) -> None:
        """Oldest entry is evicted when cache exceeds max_entries."""
        max_entries = 3
        cache = ContextSnapshotCache(max_entries=max_entries)
        assembler = MagicMock(spec=ContextAssembler)

        call_count = 0

        async def mock_assemble(pid: str, tid: Optional[str] = None) -> AssembledContext:
            nonlocal call_count
            call_count += 1
            return _make_assembled_context(f"entry-{call_count}")

        assembler.assemble = AsyncMock(side_effect=mock_assemble)

        # Each project gets unique counters so they have different cache keys
        project_counters = {
            f"proj-{i}": VersionCounters(thread_version=i)
            for i in range(5)
        }

        async def read_counters(pid: str, tid: Optional[str]) -> VersionCounters:
            return project_counters.get(pid, VersionCounters())

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(side_effect=read_counters),
            ):
                # Fill cache to capacity (3 entries)
                for i in range(3):
                    await cache.get_or_assemble(
                        assembler, f"proj-{i}", None, 10_000
                    )
                assert assembler.assemble.call_count == 3
                assert len(cache._cache) == 3

                # Add a 4th entry — should evict proj-0 (oldest)
                await cache.get_or_assemble(
                    assembler, "proj-3", None, 10_000
                )
                assert assembler.assemble.call_count == 4
                assert len(cache._cache) == 3

                # proj-0 should now be evicted — accessing it triggers re-assembly
                await cache.get_or_assemble(
                    assembler, "proj-0", None, 10_000
                )
                assert assembler.assemble.call_count == 5

        asyncio.get_event_loop().run_until_complete(_run())

    def test_accessing_entry_refreshes_lru_position(self) -> None:
        """Accessing a cached entry moves it to most-recently-used."""
        max_entries = 3
        cache = ContextSnapshotCache(max_entries=max_entries)
        assembler = MagicMock(spec=ContextAssembler)

        call_count = 0

        async def mock_assemble(pid: str, tid: Optional[str] = None) -> AssembledContext:
            nonlocal call_count
            call_count += 1
            return _make_assembled_context(f"lru-{call_count}")

        assembler.assemble = AsyncMock(side_effect=mock_assemble)

        project_counters = {
            f"proj-{i}": VersionCounters(thread_version=i)
            for i in range(5)
        }

        async def read_counters(pid: str, tid: Optional[str]) -> VersionCounters:
            return project_counters.get(pid, VersionCounters())

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(side_effect=read_counters),
            ):
                # Fill cache: proj-0, proj-1, proj-2
                for i in range(3):
                    await cache.get_or_assemble(
                        assembler, f"proj-{i}", None, 10_000
                    )
                assert assembler.assemble.call_count == 3

                # Access proj-0 to refresh its LRU position
                await cache.get_or_assemble(
                    assembler, "proj-0", None, 10_000
                )
                # Should be a cache hit — no new assembly
                assert assembler.assemble.call_count == 3

                # Add proj-3 — should evict proj-1 (now oldest), NOT proj-0
                await cache.get_or_assemble(
                    assembler, "proj-3", None, 10_000
                )
                assert assembler.assemble.call_count == 4

                # proj-0 should still be cached (was refreshed)
                await cache.get_or_assemble(
                    assembler, "proj-0", None, 10_000
                )
                assert assembler.assemble.call_count == 4  # still a hit

                # proj-1 should be evicted — triggers re-assembly
                await cache.get_or_assemble(
                    assembler, "proj-1", None, 10_000
                )
                assert assembler.assemble.call_count == 5

        asyncio.get_event_loop().run_until_complete(_run())

    def test_max_entries_of_one(self) -> None:
        """Cache with max_entries=1 only keeps the most recent entry."""
        cache = ContextSnapshotCache(max_entries=1)
        assembler = MagicMock(spec=ContextAssembler)

        call_count = 0

        async def mock_assemble(pid: str, tid: Optional[str] = None) -> AssembledContext:
            nonlocal call_count
            call_count += 1
            return _make_assembled_context(f"single-{call_count}")

        assembler.assemble = AsyncMock(side_effect=mock_assemble)

        project_counters = {
            "proj-a": VersionCounters(thread_version=1),
            "proj-b": VersionCounters(thread_version=2),
        }

        async def read_counters(pid: str, tid: Optional[str]) -> VersionCounters:
            return project_counters.get(pid, VersionCounters())

        async def _run() -> None:
            with patch.object(
                cache, "_read_version_counters",
                new=AsyncMock(side_effect=read_counters),
            ):
                await cache.get_or_assemble(
                    assembler, "proj-a", None, 10_000
                )
                assert len(cache._cache) == 1

                # Adding proj-b evicts proj-a
                await cache.get_or_assemble(
                    assembler, "proj-b", None, 10_000
                )
                assert len(cache._cache) == 1
                assert assembler.assemble.call_count == 2

                # proj-a is evicted — re-assembly needed
                await cache.get_or_assemble(
                    assembler, "proj-a", None, 10_000
                )
                assert assembler.assemble.call_count == 3

        asyncio.get_event_loop().run_until_complete(_run())


# ── Unit Tests: _make_key ──────────────────────────────────────────────


class TestMakeKey:
    """Unit tests for ``ContextSnapshotCache._make_key()`` static method.

    **Validates: Requirements 34.1**
    """

    def test_key_format(self) -> None:
        """Key follows expected format."""
        key = ContextSnapshotCache._make_key("proj-1", "thread-1", 10_000, "abc123")
        assert key == "proj-1:thread-1:10000:abc123"

    def test_none_thread_id_uses_none_string(self) -> None:
        """None thread_id is represented as 'none' in the key."""
        key = ContextSnapshotCache._make_key("proj-1", None, 10_000, "abc123")
        assert key == "proj-1:none:10000:abc123"

    def test_different_budgets_produce_different_keys(self) -> None:
        """Different token budgets produce different cache keys."""
        k1 = ContextSnapshotCache._make_key("p", "t", 5_000, "hash")
        k2 = ContextSnapshotCache._make_key("p", "t", 10_000, "hash")
        assert k1 != k2
