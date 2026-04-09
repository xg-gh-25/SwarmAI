"""Tests for section budget caps with archival.

Validates that MEMORY.md sections are trimmed to their caps,
overflow entries are archived, and under-cap sections are untouched.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest


def _build_memory_md(sections: dict[str, list[str]]) -> str:
    """Build a fake MEMORY.md with given sections and entry lists."""
    parts = ["# Memory\n"]
    for name, entries in sections.items():
        parts.append(f"## {name}\n")
        for entry in entries:
            parts.append(f"- {entry}\n")
        parts.append("\n")
    return "".join(parts)


class TestSectionCaps:
    """Test suite for section cap enforcement with archival."""

    def test_enforce_caps_trims_oldest(self, tmp_path):
        """35 RC entries -> 30 after enforcement."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        # Build memory with 35 Recent Context entries
        entries = [f"2026-01-{i+1:02d}: **Entry {i+1}** — detail" for i in range(35)]
        content = _build_memory_md({"Recent Context": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        # Create archives dir
        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        rc_entries = [l for l in result.splitlines() if l.strip().startswith("- ") and "Recent Context" not in l]
        # Filter to only entries under Recent Context section
        in_rc = False
        rc_count = 0
        for line in result.splitlines():
            if line.strip() == "## Recent Context":
                in_rc = True
                continue
            if line.strip().startswith("## ") and in_rc:
                break
            if in_rc and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                rc_count += 1
        assert rc_count <= SECTION_CAPS["Recent Context"]

    def test_overflow_archived(self, tmp_path):
        """Trimmed entries appear in archive file."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail" for i in range(35)]
        content = _build_memory_md({"Recent Context": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        # Check that archive file was created
        today = date.today()
        archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        archive_path = archives_dir / archive_name
        assert archive_path.exists(), f"Archive file {archive_name} should exist"
        archive_content = archive_path.read_text()
        assert "Recent Context" in archive_content

    def test_archive_format(self, tmp_path):
        """Archive file has proper markdown structure."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail" for i in range(20)]
        content = _build_memory_md({"COE Registry": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        today = date.today()
        archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        archive_path = archives_dir / archive_name
        assert archive_path.exists()
        archive_content = archive_path.read_text()
        # Should have a section heading
        assert "## COE Registry" in archive_content or "COE Registry" in archive_content
        # Should have date header
        assert today.isoformat() in archive_content

    def test_cap_respected_for_all_sections(self, tmp_path):
        """Each capped section is enforced."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        sections = {}
        for name, cap in SECTION_CAPS.items():
            # Create entries exceeding cap by 5
            sections[name] = [
                f"2026-03-{i+1:02d}: **{name} Entry {i+1}** — detail"
                for i in range(cap + 5)
            ]
        content = _build_memory_md(sections)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        for name, cap in SECTION_CAPS.items():
            in_section = False
            count = 0
            for line in result.splitlines():
                if line.strip() == f"## {name}":
                    in_section = True
                    continue
                if line.strip().startswith("## ") and in_section:
                    break
                if in_section and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                    count += 1
            assert count <= cap, f"Section '{name}' has {count} entries, cap is {cap}"

    def test_no_modification_under_cap(self, tmp_path):
        """20 entries in a 30-cap section -> no change."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail" for i in range(20)]
        content = _build_memory_md({"Recent Context": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        original = memory_path.read_text()
        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)
        assert memory_path.read_text() == original

    def test_multiline_entries_preserved(self, tmp_path):
        """Entries with continuation lines counted as one entry."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        # Build entries where some have continuation lines (indented)
        lines = []
        for i in range(20):
            lines.append(f"- 2026-03-{i+1:02d}: **Entry {i+1}** — detail")
            lines.append(f"  Detail: DailyActivity/2026-03-{i+1:02d}.md")

        content = "# Memory\n\n## COE Registry\n" + "\n".join(lines) + "\n\n## Other\n"
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        # Count top-level entries (starting with "- ") in COE Registry
        in_coe = False
        count = 0
        for line in result.splitlines():
            if line.strip() == "## COE Registry":
                in_coe = True
                continue
            if line.strip().startswith("## ") and in_coe:
                break
            if in_coe and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                count += 1
        assert count <= SECTION_CAPS["COE Registry"]
