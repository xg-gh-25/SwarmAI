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
        """Files >180 days are archived unconditionally (even without distilled frontmatter)."""
        da_dir = ws / "Knowledge" / "DailyActivity"
        old_date = datetime.now() - timedelta(days=200)
        old_file = _create_dated_file(da_dir, old_date)

        hook._enforce_retention_policies(str(ws))

        assert not old_file.exists()
        assert (ws / "Knowledge" / "Archives" / old_file.name).exists()

    def test_archive_distilled_file_after_90_days(self, hook, ws):
        """Distilled files >90 days are archived normally."""
        da_dir = ws / "Knowledge" / "DailyActivity"
        old_date = datetime.now() - timedelta(days=100)
        old_file = da_dir / f"{old_date.strftime('%Y-%m-%d')}.md"
        old_file.write_text("---\ndistilled: true\n---\n# Activity\n")

        hook._enforce_retention_policies(str(ws))

        assert not old_file.exists()
        assert (ws / "Knowledge" / "Archives" / old_file.name).exists()

    def test_protect_undistilled_file_between_90_and_180_days(self, hook, ws):
        """Undistilled files between 90-180 days are protected from archival."""
        da_dir = ws / "Knowledge" / "DailyActivity"
        old_date = datetime.now() - timedelta(days=100)
        old_file = _create_dated_file(da_dir, old_date)

        hook._enforce_retention_policies(str(ws))

        # File should NOT be archived — it's undistilled and within the protection window
        assert old_file.exists()


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
        today_date = datetime.now().strftime("%Y-%m-%d")
        # Use real unicode characters
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            f"- {old_date}: \u2705 **Resolved task** \u2014 This was done\n"
            f"- {today_date}: \U0001f535 **Active task** \u2014 Still working\n"
        )
        memory_path.write_text(memory_content)

        hook._enforce_retention_policies(str(ws))

        # Resolved entry should be removed from MEMORY.md
        content = memory_path.read_text()
        assert "Resolved task" not in content
        # Active entry should remain
        assert "Active task" in content
        # Archived entry should be in archive file
        archive_dir = ws / "Knowledge" / "Archives"
        archive_files = list(archive_dir.glob("MEMORY-archive-*.md"))
        assert len(archive_files) >= 1
        archive_content = archive_files[0].read_text()
        assert "Resolved task" in archive_content


class TestKeepUnresolvedOpenThreads:
    def test_keep_unresolved_open_threads(self, hook, ws):
        memory_path = ws / ".context" / "MEMORY.md"
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        # Use real unicode characters
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            f"- {old_date}: \U0001f535 **Active task** \u2014 Still working on this\n"
        )
        memory_path.write_text(memory_content)

        hook._enforce_retention_policies(str(ws))
        # Unresolved threads should remain untouched
        content = memory_path.read_text()
        assert "Active task" in content


class TestOpenThreadDateFormats:
    """Tests for robust date parsing in Open Threads retention policy."""

    def test_short_date_in_parens(self, hook, ws):
        """Handle '- ✅ CompactionGuard bugfix (3/22)' format."""
        memory_path = ws / ".context" / "MEMORY.md"
        # Use a month/day from >7 days ago
        old = datetime.now() - timedelta(days=30)
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            f"- \u2705 CompactionGuard bugfix ({old.month}/{old.day})\n"
        )
        memory_path.write_text(memory_content)
        # Should not crash; should detect the resolved entry
        hook._enforce_retention_policies(str(ws))

    def test_no_date_at_all(self, hook, ws):
        """Handle '- ✅ Some item' with no date — should skip, not crash."""
        memory_path = ws / ".context" / "MEMORY.md"
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            "- \u2705 Some item with no date\n"
        )
        memory_path.write_text(memory_content)
        # Should not crash, should skip entry (no date = don't archive)
        hook._enforce_retention_policies(str(ws))

    def test_iso_date_in_parens(self, hook, ws):
        """Handle '- ✅ Task done (2026-03-01)' format."""
        memory_path = ws / ".context" / "MEMORY.md"
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            "- \u2705 Task done (2026-03-01)\n"
        )
        memory_path.write_text(memory_content)
        hook._enforce_retention_policies(str(ws))

    def test_mixed_formats(self, hook, ws):
        """Handle a mix of date formats without crashing."""
        memory_path = ws / ".context" / "MEMORY.md"
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        memory_content = (
            "# MEMORY\n\n"
            "## Open Threads\n"
            f"- {old_date}: \u2705 **ISO start** \u2014 done\n"
            "- \u2705 No date item\n"
            "- \u2705 Short date item (3/15)\n"
            "- \u2705 Parens ISO (2026-01-01)\n"
            "- \U0001f535 **Active** \u2014 not resolved\n"
        )
        memory_path.write_text(memory_content)
        hook._enforce_retention_policies(str(ws))
