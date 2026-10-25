"""Tests for scripts/locked_write.py write behaviour.

Write-time line dedup (``entry_dedup_keys`` / ``filter_duplicate_entries``) was
deleted (design docs/design/2026-10-25-memory-write-entry-dedup.md): it judged
duplicates one line at a time and wrote half entries. A write now writes exactly the
text it is given; exact duplicates are removed by the lifecycle sweep
(``reclaim_duplicate_entries``, covered in test_memory_entry_spans.py). ``dedup`` is
an accepted, inert keyword.
"""
from pathlib import Path


class TestDedupKeywordInert:
    def _mem(self, tmp_path: Path) -> Path:
        p = tmp_path / "MEMORY.md"
        p.write_text("## Decisions\n- 2026-06-01: alpha decision\n", encoding="utf-8")
        return p

    def test_dedup_true_writes_text_verbatim(self, tmp_path):
        from scripts.locked_write import locked_read_modify_write
        mem = self._mem(tmp_path)
        locked_read_modify_write(
            mem, "Decisions",
            "- 2026-06-01: alpha decision\n- 2026-06-28: beta decision",
            mode="prepend", dedup=True,
        )
        body = mem.read_text(encoding="utf-8")
        assert body.count("alpha decision") == 2
        assert "beta decision" in body

    def test_dedup_false_unchanged(self, tmp_path):
        from scripts.locked_write import locked_read_modify_write
        mem = self._mem(tmp_path)
        locked_read_modify_write(mem, "Decisions", "- 2026-06-01: alpha decision", mode="prepend")
        assert mem.read_text(encoding="utf-8").count("alpha decision") == 2

    def test_dedup_helpers_are_gone(self):
        import scripts.locked_write as lw
        assert not hasattr(lw, "filter_duplicate_entries")
        assert not hasattr(lw, "entry_dedup_keys")


# Return the body of ONE section: from its header to the next `## ` header, or to
# EOF if it is the last section. A dedup assertion built on this counts occurrences
# inside the section it targets, so an echo of the same text in a LATER section
# cannot satisfy it.
#
# It used to slice header-to-EOF, justified by skipping past a prepended
# `<!-- MEMORY_INDEX -->` block whose entries echoed section text and so
# double-counted. That block is no longer written (the in-prompt MEMORY index was
# deleted; live MEMORY.md is full-injected and recall scans the body), which left
# the slice unbounded on the far side — a pass-through on every current fixture,
# where the target header happens to sit at byte 0. Bounding it at the next header
# makes the stated guarantee true rather than merely reworded: today's fixtures are
# single-section so nothing changes, but a fixture that grows a second section gets
# the scoping the assertions below assume it already had.
def _section_body(content: str, section: str) -> str:
    marker = f"## {section}"
    idx = content.rfind(marker)
    if idx == -1:
        return content
    end = content.find("\n## ", idx + len(marker))
    return content[idx:] if end == -1 else content[idx:end]


def test_section_body_stops_at_the_next_header():
    """The helper must bound the far side of the slice, not just the near side.

    Every fixture in this file is single-section with its header at byte 0, so an
    unbounded header-to-EOF slice is a pass-through and looks correct. That is
    exactly why this is asserted directly: the assertions below state they count
    "within the section", and without this the first fixture to grow a second
    section would silently start counting the whole file — a weakening no existing
    test could observe.
    """
    doc = (
        "## Decisions\n- alpha decision\n\n"
        "## Guidelines\n- echo of alpha decision\n\n"
        "## Open Threads\n- another alpha decision\n"
    )
    assert doc.count("alpha decision") == 3, "fixture must have decoys to be meaningful"

    first = _section_body(doc, "Decisions")
    assert first.count("alpha decision") == 1
    assert "## Guidelines" not in first

    # A section with no following header still runs to EOF.
    last = _section_body(doc, "Open Threads")
    assert last.count("alpha decision") == 1

    # An absent section falls back to the whole document rather than raising.
    assert _section_body(doc, "Nope") == doc


class TestExtractorWritePath:
    """memory_extractor._write_entries writes each extracted entry as one entry."""

    def test_write_entries_collapses_multiline_entry(self, tmp_path):
        # Gate-2 LOW: an LLM entry with an embedded newline must NOT be split
        # into independent physical lines (which dedup/_modify_content would
        # treat separately, orphaning half). It is collapsed to one line.
        from core import memory_extractor
        mem = tmp_path / "MEMORY.md"
        mem.write_text("## Decisions\n", encoding="utf-8")
        ok = memory_extractor._write_entries(
            ["- 2026-06-28: header part\n  continued detail part"],
            "Decisions", mem,
        )
        assert ok is True
        # Scope to the Decisions section body, so the one-line assertion is about
        # this section's entries and not an incidental match elsewhere.
        section = _section_body(mem.read_text(encoding="utf-8"), "Decisions")
        lines = [ln for ln in section.splitlines() if "header part" in ln]
        assert len(lines) == 1
        assert "continued detail part" in lines[0]


class TestMemoryIndexStrippedOnWrite:
    """run_3cb6b9ae (#6): the write chokepoint strips the orphan `## Memory Index`
    block from MEMORY.md on write-back — the DURABLE removal (a one-time data edit
    reverted because this read-modify-write path re-carried the block the live daemon
    read forward). Strips only for MEMORY.md; other files untouched."""

    def _write(self, tmp_path, existing: str, section: str, text: str, name: str = "MEMORY.md") -> str:
        from scripts.locked_write import locked_read_modify_write
        f = tmp_path / name
        f.write_text(existing, encoding="utf-8")
        locked_read_modify_write(f, section, text, mode="prepend", dedup=True)
        return f.read_text(encoding="utf-8")

    def test_index_block_stripped_on_memory_write(self, tmp_path):
        existing = (
            "<!-- MEMORY_INDEX_START -->\n## Memory Index\n- [PRI01] stale | x\n"
            "<!-- MEMORY_INDEX_END -->\n\n"
            "## Decisions\n- [decision] **old** — body (2026-01-01)\n\n"
            "## Open Threads\n- one\n"
        )
        out = self._write(tmp_path, existing, "Decisions",
                          "- [decision] **new** — fresh (2026-08-14)")
        assert "MEMORY_INDEX_START" not in out and "## Memory Index" not in out
        assert "**new**" in out          # the write landed
        assert "**old**" in out          # existing entry preserved
        assert "## Decisions" in out and "## Open Threads" in out

    def test_non_memory_file_index_block_untouched(self, tmp_path):
        """A non-MEMORY.md file with a look-alike block is NOT stripped (scoped to MEMORY.md)."""
        existing = (
            "<!-- MEMORY_INDEX_START -->\n## Memory Index\n- x\n<!-- MEMORY_INDEX_END -->\n\n"
            "## Guidelines\n- [guideline] **old** — body (2026-01-01)\n"
        )
        out = self._write(tmp_path, existing, "Guidelines",
                          "- [guideline] **new** — body (2026-08-14)", name="OTHER.md")
        assert "MEMORY_INDEX_START" in out  # NOT stripped for a non-MEMORY file

    def test_no_block_write_unaffected(self, tmp_path):
        """A MEMORY.md with NO index block writes normally (strip is a no-op)."""
        existing = "## Decisions\n- [decision] **old** — body (2026-01-01)\n\n## Open Threads\n- one\n"
        out = self._write(tmp_path, existing, "Decisions",
                          "- [decision] **new** — fresh (2026-08-14)")
        assert "**new**" in out and "**old**" in out
        assert "MEMORY_INDEX" not in out


