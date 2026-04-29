"""Tests for MEMORY.md stale RC auto-archival in DistillationTriggerHook.

TDD tests for _archive_stale_rc_entries():
1. 35-day-old RC entry gets archived
2. 35-day-old KD entry is NOT archived
3. 25-day-old RC entry is NOT archived (too recent)
4. RC with "Birthday" keyword is NOT archived (protected)
5. Archived entry appears in archive file
6. MEMORY.md body shrinks after archival
"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from hooks.distillation_hook import DistillationTriggerHook


def _days_ago_str(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _make_memory_md(entries: list[str]) -> str:
    """Build a minimal MEMORY.md with Recent Context entries."""
    lines = [
        "# MEMORY\n\n",
        "## Key Decisions\n\n_None._\n\n",
        "## Lessons Learned\n\n_None._\n\n",
        "## Recent Context\n\n",
    ]
    for e in entries:
        lines.append(f"{e}\n")
    lines.append("\n## Open Threads\n\n_None._\n")
    return "".join(lines)


class TestStaleRCArchive35DayOld:
    """35-day-old RC entry gets archived."""

    def test_old_rc_archived(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        archive_dir = ws / "Knowledge" / "Archives"

        memory_path = ctx / "MEMORY.md"
        old_date = _days_ago_str(35)
        memory_path.write_text(_make_memory_md([
            f"- {old_date}: [RC01] Some old context about deployment status",
        ]))

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        content = memory_path.read_text()
        assert "RC01" not in content, "35-day-old RC entry should be removed from MEMORY.md"


class TestStaleRCArchiveKDNotArchived:
    """35-day-old KD entry is NOT archived."""

    def test_old_kd_not_archived(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)

        memory_path = ctx / "MEMORY.md"
        old_date = _days_ago_str(35)
        memory_path.write_text(_make_memory_md([
            f"- {old_date}: [KD01] Important decision about architecture",
        ]))

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        content = memory_path.read_text()
        assert "KD01" in content, "35-day-old KD entry should NOT be archived"


class TestStaleRCArchiveRecentNotArchived:
    """25-day-old RC entry is NOT archived (too recent)."""

    def test_recent_rc_not_archived(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)

        memory_path = ctx / "MEMORY.md"
        recent_date = _days_ago_str(25)
        memory_path.write_text(_make_memory_md([
            f"- {recent_date}: [RC01] Recent context about current sprint",
        ]))

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        content = memory_path.read_text()
        assert "RC01" in content, "25-day-old RC should NOT be archived"


class TestStaleRCArchiveProtected:
    """RC with 'Birthday' keyword is NOT archived (protected)."""

    def test_birthday_rc_not_archived(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)

        memory_path = ctx / "MEMORY.md"
        old_date = _days_ago_str(35)
        memory_path.write_text(_make_memory_md([
            f"- {old_date}: [RC01] Birthday celebration planned for team",
        ]))

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        content = memory_path.read_text()
        assert "RC01" in content, "Protected RC with 'Birthday' should NOT be archived"


class TestStaleRCArchiveAppearsInArchive:
    """Archived entry appears in archive file."""

    def test_archived_entry_in_file(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        archive_dir = ws / "Knowledge" / "Archives"

        memory_path = ctx / "MEMORY.md"
        old_date = _days_ago_str(35)
        memory_path.write_text(_make_memory_md([
            f"- {old_date}: [RC01] Some old context about deployment status",
        ]))

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        today = date.today()
        archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        archive_path = archive_dir / archive_name
        assert archive_path.exists(), "Archive file should be created"
        archive_content = archive_path.read_text()
        assert "RC01" in archive_content, "Archived entry should appear in archive file"


class TestStaleRCArchiveBodyShrinks:
    """MEMORY.md body shrinks after archival."""

    def test_body_shrinks(self, tmp_path):
        ws = tmp_path / "ws"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)

        memory_path = ctx / "MEMORY.md"
        old_date = _days_ago_str(35)
        recent_date = _days_ago_str(5)
        memory_path.write_text(_make_memory_md([
            f"- {old_date}: [RC01] Old context that should be archived",
            f"- {recent_date}: [RC02] Recent context that should stay",
        ]))

        original_size = len(memory_path.read_text())

        hook = DistillationTriggerHook()
        hook._archive_stale_rc_entries(memory_path, ws)

        new_size = len(memory_path.read_text())
        assert new_size < original_size, "MEMORY.md should be smaller after archival"
        content = memory_path.read_text()
        assert "RC02" in content, "Recent entry should remain"
        assert "RC01" not in content, "Old entry should be removed"
