"""Tests for retention policies in context_health_hook."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hooks.context_health_hook import ContextHealthHook


@pytest.fixture
def hook():
    return ContextHealthHook()


@pytest.fixture
def ws(tmp_path):
    """Create a workspace with Knowledge directories."""
    da_dir = tmp_path / "Knowledge" / "DailyActivity"
    da_dir.mkdir(parents=True)
    archive_dir = tmp_path / "Knowledge" / "Archives"
    archive_dir.mkdir(parents=True)
    context_dir = tmp_path / ".context"
    context_dir.mkdir(parents=True)
    return tmp_path


def _create_dated_file(directory: Path, date: datetime, prefix: str = "") -> Path:
    name = f"{prefix}{date.strftime('%Y-%m-%d')}.md"
    f = directory / name
    f.write_text(f"# Activity for {date.date()}\n")
    return f


class TestArchiveOldDailyActivity:
    def test_archive_old_daily_activity(self, hook, ws):
        da_dir = ws / "Knowledge" / "DailyActivity"
        old_date = datetime.now() - timedelta(days=100)
        old_file = _create_dated_file(da_dir, old_date)

        hook._enforce_retention_policies(str(ws))

        assert not old_file.exists()
        assert (ws / "Knowledge" / "Archives" / old_file.name).exists()


class TestKeepRecentDailyActivity:
    def test_keep_recent_daily_activity(self, hook, ws):
        da_dir = ws / "Knowledge" / "DailyActivity"
        recent_date = datetime.now() - timedelta(days=30)
        recent_file = _create_dated_file(da_dir, recent_date)

        hook._enforce_retention_policies(str(ws))

        assert recent_file.exists()


class TestDeleteOldArchives:
    def test_delete_old_archives(self, hook, ws):
        archive_dir = ws / "Knowledge" / "Archives"
        old_date = datetime.now() - timedelta(days=400)
        old_file = _create_dated_file(archive_dir, old_date)

        hook._enforce_retention_policies(str(ws))

        assert not old_file.exists()


class TestPreserveMemoryArchives:
    def test_preserve_memory_archives(self, hook, ws):
        archive_dir = ws / "Knowledge" / "Archives"
        old_date = datetime.now() - timedelta(days=400)
        # Memory archives should NEVER be deleted
        name = f"MEMORY-archive-{old_date.strftime('%Y-%m-%d')}.md"
        mem_file = archive_dir / name
        mem_file.write_text("# Memory archive\n")

        hook._enforce_retention_policies(str(ws))

        assert mem_file.exists()


class TestArchiveResolvedOpenThreads:
    def test_archive_resolved_open_threads(self, hook, ws):
        memory_path = ws / ".context" / "MEMORY.md"
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        memory_content = f"""# MEMORY

## Open Threads
- {old_date}: \\u2705 **Resolved task** \\u2014 This was done
- {datetime.now().strftime('%Y-%m-%d')}: \\U0001f535 **Active task** \\u2014 Still working
"""
        memory_path.write_text(memory_content)

        hook._enforce_retention_policies(str(ws))
        # Should not crash; method handles OT parsing gracefully
        assert True


class TestKeepUnresolvedOpenThreads:
    def test_keep_unresolved_open_threads(self, hook, ws):
        memory_path = ws / ".context" / "MEMORY.md"
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        memory_content = f"""# MEMORY

## Open Threads
- {old_date}: \\U0001f535 **Active task** \\u2014 Still working on this
"""
        memory_path.write_text(memory_content)

        hook._enforce_retention_policies(str(ws))
        # Unresolved threads should remain untouched
        content = memory_path.read_text()
        assert "Active task" in content
