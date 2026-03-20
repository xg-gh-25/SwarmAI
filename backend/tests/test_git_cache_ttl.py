"""Tests for git status TTL cache in ContextDirectoryLoader._is_l1_fresh().

Verifies Fix 3: _is_l1_fresh() uses a 15-second TTL cache to avoid
forking a `git status` subprocess on every chat message.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.context_directory_loader import ContextDirectoryLoader


def _make_loader(tmp_path: Path) -> ContextDirectoryLoader:
    """Create a loader with a real context_dir."""
    loader = ContextDirectoryLoader.__new__(ContextDirectoryLoader)
    loader.context_dir = tmp_path
    return loader


class TestGitCacheTTL:
    """Verify the TTL cache prevents redundant git subprocess forks."""

    def setup_method(self):
        """Clear class-level cache between tests."""
        ContextDirectoryLoader._git_fresh_cache.clear()

    def test_cache_prevents_second_subprocess_call(self, tmp_path):
        """Second call within TTL returns cached result, no subprocess."""
        loader = _make_loader(tmp_path)
        # Create a fake L1 cache file
        (tmp_path / "L1_SYSTEM_PROMPTS.md").write_text("cached")

        call_count = 0
        original_uncached = loader._is_l1_fresh_uncached

        def counting_uncached(l1_path):
            nonlocal call_count
            call_count += 1
            return True

        with patch.object(loader, "_is_l1_fresh_uncached", side_effect=counting_uncached):
            result1 = loader._is_l1_fresh()
            result2 = loader._is_l1_fresh()

        assert result1 is True
        assert result2 is True
        assert call_count == 1  # Only one actual check

    def test_cache_expires_after_ttl(self, tmp_path):
        """After TTL expires, a new subprocess call is made."""
        loader = _make_loader(tmp_path)
        (tmp_path / "L1_SYSTEM_PROMPTS.md").write_text("cached")

        call_count = 0

        def counting_uncached(l1_path):
            nonlocal call_count
            call_count += 1
            return True

        with patch.object(loader, "_is_l1_fresh_uncached", side_effect=counting_uncached), \
             patch("time.monotonic") as mock_time:
            mock_time.return_value = 1000.0
            loader._is_l1_fresh()
            # Advance past TTL
            mock_time.return_value = 1000.0 + 16.0
            loader._is_l1_fresh()

        assert call_count == 2  # Both calls hit the real check

    def test_cache_returns_false_when_stale(self, tmp_path):
        """Cache correctly caches False (stale) results too."""
        loader = _make_loader(tmp_path)
        (tmp_path / "L1_SYSTEM_PROMPTS.md").write_text("cached")

        call_count = 0

        def counting_uncached(l1_path):
            nonlocal call_count
            call_count += 1
            return False

        with patch.object(loader, "_is_l1_fresh_uncached", side_effect=counting_uncached):
            result1 = loader._is_l1_fresh()
            result2 = loader._is_l1_fresh()

        assert result1 is False
        assert result2 is False
        assert call_count == 1

    def test_no_cache_when_l1_file_missing(self, tmp_path):
        """No L1 file → returns False immediately, no caching."""
        loader = _make_loader(tmp_path)

        result = loader._is_l1_fresh()
        assert result is False

    def test_different_dirs_cache_independently(self, tmp_path):
        """Each context_dir gets its own cache entry."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "L1_SYSTEM_PROMPTS.md").write_text("cached")
        (dir_b / "L1_SYSTEM_PROMPTS.md").write_text("cached")

        loader_a = _make_loader(dir_a)
        loader_b = _make_loader(dir_b)

        calls = []

        def tracking_uncached(l1_path):
            calls.append(str(l1_path))
            return True

        with patch.object(ContextDirectoryLoader, "_is_l1_fresh_uncached",
                         side_effect=tracking_uncached):
            loader_a._is_l1_fresh()
            loader_b._is_l1_fresh()

        assert len(calls) == 2  # Both dirs checked independently
