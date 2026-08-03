"""Tests for Code Intelligence FS Watcher (backend/core/code_intel/watcher.py).

TDD: tests written first, implementation follows.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Filter tests ────────────────────────────────────────────────────────────

class TestWatcherFilter:
    """Unit tests for the watcher's file filter logic."""

    def _make_watcher(self):
        from core.code_intel.watcher import CodeIntelWatcher
        graph = MagicMock()
        return CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)

    def test_watcher_filter_accepts_source_files(self):
        """Source files (.py, .ts, .js, .go, etc.) pass the filter."""
        watcher = self._make_watcher()
        # watchfiles filter signature: (change_type, path) -> bool
        assert watcher._filter("modified", "/tmp/repo/src/main.py") is True
        assert watcher._filter("modified", "/tmp/repo/lib/index.ts") is True
        assert watcher._filter("modified", "/tmp/repo/cmd/server.go") is True
        assert watcher._filter("modified", "/tmp/repo/app.jsx") is True
        assert watcher._filter("modified", "/tmp/repo/deep/nested/file.rs") is True

    def test_watcher_filter_rejects_node_modules(self):
        """Files inside node_modules/ are rejected."""
        watcher = self._make_watcher()
        assert watcher._filter("modified", "/tmp/repo/node_modules/pkg/index.js") is False
        assert watcher._filter("modified", "/tmp/repo/frontend/node_modules/react/index.js") is False

    def test_watcher_filter_rejects_non_source(self):
        """Non-source files (.json, .md, .txt, .lock, etc.) are rejected."""
        watcher = self._make_watcher()
        assert watcher._filter("modified", "/tmp/repo/package.json") is False
        assert watcher._filter("modified", "/tmp/repo/README.md") is False
        assert watcher._filter("modified", "/tmp/repo/data.csv") is False
        assert watcher._filter("modified", "/tmp/repo/yarn.lock") is False
        assert watcher._filter("modified", "/tmp/repo/image.png") is False

    def test_watcher_filter_rejects_git_dir(self):
        """Files inside .git/ are rejected."""
        watcher = self._make_watcher()
        assert watcher._filter("modified", "/tmp/repo/.git/objects/abc123") is False
        assert watcher._filter("modified", "/tmp/repo/.git/HEAD") is False

    def test_watcher_filter_rejects_pycache(self):
        """Files inside __pycache__/ are rejected."""
        watcher = self._make_watcher()
        assert watcher._filter("modified", "/tmp/repo/__pycache__/mod.cpython-311.pyc") is False

    def test_watcher_filter_rejects_venv(self):
        """Files inside venv/ or .venv/ are rejected."""
        watcher = self._make_watcher()
        assert watcher._filter("modified", "/tmp/repo/.venv/lib/site-packages/foo.py") is False
        assert watcher._filter("modified", "/tmp/repo/venv/lib/bar.py") is False

    def test_watcher_filter_rejects_knowledge_dirs(self):
        """Non-source workspace dirs (Knowledge, .context, DailyActivity) are excluded.

        These directories contain markdown/JSON written by hooks, not source code.
        Bulk writes (200+ files from context_health_hook) previously caused memory
        spikes that killed the daemon via jetsam.
        """
        watcher = self._make_watcher()
        # Knowledge directories — hooks write DailyActivity, Signals, etc.
        assert watcher._filter("modified", "/tmp/repo/Knowledge/DailyActivity/2026-06-20.md") is False
        assert watcher._filter("modified", "/tmp/repo/Knowledge/Signals/signal.md") is False
        assert watcher._filter("modified", "/tmp/repo/Knowledge/DailyBriefs/2026-06-20.md") is False
        assert watcher._filter("modified", "/tmp/repo/Knowledge/JobResults/result.md") is False
        # .context directory — context_health_hook refreshes these
        assert watcher._filter("modified", "/tmp/repo/.context/MEMORY.md") is False
        assert watcher._filter("modified", "/tmp/repo/.context/KNOWLEDGE.md") is False
        # Attachments — user uploads
        assert watcher._filter("modified", "/tmp/repo/Attachments/2026-06-20/image.png") is False
        # .artifacts — pipeline artifacts
        assert watcher._filter("modified", "/tmp/repo/.artifacts/runs/run_123/run.json") is False
        # Services — job configs
        assert watcher._filter("modified", "/tmp/repo/Services/swarm-jobs/state.json") is False
        # .swarm-ai — data directory
        assert watcher._filter("modified", "/Users/gawan/.swarm-ai/data.db") is False

    def test_watcher_filter_still_accepts_source_in_projects(self):
        """Source code inside Projects/ subdirs still passes (only non-source dirs blocked)."""
        watcher = self._make_watcher()
        # Python source in project directories should still be watched
        assert watcher._filter("modified", "/tmp/repo/backend/core/session_unit.py") is True
        assert watcher._filter("modified", "/tmp/repo/desktop/src/App.tsx") is True


# ── Start/Stop lifecycle tests ──────────────────────────────────────────────

class TestWatcherLifecycle:
    """Test start/stop idempotency."""

    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self):
        """Calling start() twice doesn't create duplicate tasks; stop() twice is safe."""
        from core.code_intel.watcher import CodeIntelWatcher

        graph = MagicMock()
        watcher = CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)

        # Patch _watch_loop to avoid needing watchfiles installed
        with patch.object(watcher, '_watch_loop', new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = None  # coroutine that returns immediately

            await watcher.start()
            assert watcher.is_running is True
            task1 = watcher._task

            # Second start should be no-op
            await watcher.start()
            assert watcher._task is task1  # Same task, not a new one

            await watcher.stop()
            assert watcher.is_running is False
            assert watcher._task is None

            # Second stop should be safe (no-op)
            await watcher.stop()
            assert watcher.is_running is False

    @pytest.mark.asyncio
    async def test_watcher_graceful_when_watchfiles_missing(self):
        """If watchfiles is not installed, watcher logs warning and stops."""
        from core.code_intel.watcher import CodeIntelWatcher

        graph = MagicMock()
        watcher = CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)

        # Mock the import to raise ImportError
        with patch.dict('sys.modules', {'watchfiles': None}):
            with patch('builtins.__import__', side_effect=ImportError("no watchfiles")):
                # Directly call _watch_loop to test import handling
                await watcher._watch_loop()
                # After import failure, running should be False
                assert watcher.is_running is False


# ── Batch size and chunking tests ──────────────────────────────────────────

class TestWatcherBatchHandling:
    """Tests for batch size gating and chunked processing."""

    @pytest.mark.asyncio
    async def test_watcher_processes_large_batch_with_warning(self):
        """Large batches (>_MAX_BATCH_SIZE) are still processed (not dropped).

        The filter excludes non-source dirs, so anything reaching the batch gate
        IS source code. We log a warning but don't drop legitimate refactors.
        Previously this skipped entirely — adversarial review (H1) caught that
        legitimate large refactors would be silently dropped.
        """
        from core.code_intel.watcher import CodeIntelWatcher, _MAX_BATCH_SIZE

        graph = MagicMock()
        watcher = CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)

        # Create a batch larger than _MAX_BATCH_SIZE (all .py = pass filter)
        large_batch = [
            ("modified", f"/tmp/repo/src/file_{i}.py")
            for i in range(_MAX_BATCH_SIZE + 10)
        ]

        mock_watchfiles = MagicMock()

        async def mock_awatch(*args, **kwargs):
            yield large_batch

        mock_watchfiles.awatch = mock_awatch

        with patch.dict("sys.modules", {"watchfiles": mock_watchfiles}):
            watcher._running = True
            # _trigger_incremental SHOULD be called (not skipped)
            with patch.object(watcher, '_trigger_incremental', new_callable=AsyncMock) as mock_trigger:
                await watcher._watch_loop()
                mock_trigger.assert_called_once()
                # Verify all 60 source files are passed through
                called_files = mock_trigger.call_args[0][0]
                assert len(called_files) == _MAX_BATCH_SIZE + 10

    @pytest.mark.asyncio
    async def test_watcher_processes_small_batch(self):
        """Batches within _MAX_BATCH_SIZE are processed normally."""
        from core.code_intel.watcher import CodeIntelWatcher

        graph = MagicMock()
        watcher = CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)

        # Create a small batch (within limit)
        small_batch = [
            ("modified", f"/tmp/repo/src/file_{i}.py")
            for i in range(5)
        ]

        mock_watchfiles = MagicMock()

        async def mock_awatch(*args, **kwargs):
            yield small_batch

        mock_watchfiles.awatch = mock_awatch

        with patch.dict("sys.modules", {"watchfiles": mock_watchfiles}):
            watcher._running = True
            with patch.object(watcher, '_trigger_incremental', new_callable=AsyncMock) as mock_trigger:
                await watcher._watch_loop()
                mock_trigger.assert_called_once()

    @pytest.mark.asyncio
    async def test_watcher_chunks_route_extraction(self):
        """Route extraction is chunked (_CHUNK_SIZE), graph update is single call."""
        from core.code_intel.watcher import CodeIntelWatcher, _CHUNK_SIZE

        graph = MagicMock()
        graph.incremental_update = MagicMock()
        watcher = CodeIntelWatcher("test-project", Path("/tmp/repo"), graph)
        watcher._running = True

        # Create 35 files — route extraction should be 2 chunks (20 + 15)
        changed_files = [Path(f"/tmp/repo/src/file_{i}.py") for i in range(35)]

        with patch.object(watcher, '_extract_routes_only') as mock_routes:
            await watcher._trigger_incremental(changed_files)
            # Graph update called ONCE (single FTS rebuild)
            graph.incremental_update.assert_called_once()
            assert len(graph.incremental_update.call_args[0][1]) == 35
            # Route extraction chunked: ceil(35/20) = 2 calls
            assert mock_routes.call_count == 2
            # First chunk: 20 files
            first_call_rel = mock_routes.call_args_list[0][0][0]
            assert len(first_call_rel) == _CHUNK_SIZE
            # Second chunk: 15 files
            second_call_rel = mock_routes.call_args_list[1][0][0]
            assert len(second_call_rel) == 15


# ── Registry / capacity tests ───────────────────────────────────────────────

class TestWatcherRegistry:
    """Test the module-level watcher registry."""

    @pytest.mark.asyncio
    async def test_max_watchers_capacity(self):
        """Once at _MAX_WATCHERS, new watchers are rejected unless idle ones exist."""
        from core.code_intel import watcher as watcher_mod
        from core.code_intel.watcher import (
            CodeIntelWatcher,
            start_watcher,
            _MAX_WATCHERS,
        )

        # Clear registry
        watcher_mod._watchers.clear()
        watcher_mod._watcher_lock = None

        graph = MagicMock()

        # Patch CodeIntelWatcher.start to avoid actual FS watching
        with patch.object(CodeIntelWatcher, 'start', new_callable=AsyncMock):
            # Fill up to max capacity
            for i in range(_MAX_WATCHERS):
                result = await start_watcher(f"project-{i}", Path(f"/tmp/repo{i}"), graph)
                assert result is True

            # Next one should fail (all are "running")
            for name, w in watcher_mod._watchers.items():
                w._running = True  # Simulate running state

            result = await start_watcher("overflow-project", Path("/tmp/overflow"), graph)
            assert result is False

        # Cleanup
        watcher_mod._watchers.clear()

    @pytest.mark.asyncio
    async def test_start_watcher_idempotent(self):
        """Starting a watcher for the same project twice returns True without duplication."""
        from core.code_intel import watcher as watcher_mod
        from core.code_intel.watcher import (
            CodeIntelWatcher,
            start_watcher,
            stop_watcher,
        )

        watcher_mod._watchers.clear()
        watcher_mod._watcher_lock = None

        graph = MagicMock()

        with patch.object(CodeIntelWatcher, 'start', new_callable=AsyncMock):
            result1 = await start_watcher("my-project", Path("/tmp/myrepo"), graph)
            assert result1 is True
            # Mark as running
            watcher_mod._watchers["my-project"]._running = True

            result2 = await start_watcher("my-project", Path("/tmp/myrepo"), graph)
            assert result2 is True
            # Still only one entry
            assert len(watcher_mod._watchers) == 1

        # Cleanup
        await stop_watcher("my-project")
        watcher_mod._watchers.clear()
