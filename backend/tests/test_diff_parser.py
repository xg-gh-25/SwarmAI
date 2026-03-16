"""Unit tests for ``utils/diff_parser.py``.

Tests the unified diff parser and section-aware human summary generator
used by the ``GET /workspace/file/diff`` endpoint (L2 auto-diff feature).

Testing methodology: unit tests with deterministic inputs.
Key properties verified:
- Empty/invalid diffs produce empty results
- Hunk header parsing extracts correct line numbers
- Added/removed/modified lines are classified correctly
- Section-aware summary references nearest markdown headings
- Edge cases: single-line hunks, binary markers, unicode content
"""

import pytest
from utils.diff_parser import parse_unified_diff, format_human_summary, DiffHunk


class TestParseUnifiedDiff:
    """Tests for parse_unified_diff()."""

    def test_empty_input_returns_empty(self):
        assert parse_unified_diff("") == []
        assert parse_unified_diff("   ") == []
        assert parse_unified_diff(None) == []

    def test_no_hunks_returns_empty(self):
        raw = "diff --git a/file.md b/file.md\nindex abc..def 100644\n"
        assert parse_unified_diff(raw) == []

    def test_single_added_line(self):
        raw = (
            "diff --git a/file.md b/file.md\n"
            "--- a/file.md\n"
            "+++ b/file.md\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            " line2\n"
            "+new line\n"
            " line3\n"
        )
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 1
        assert hunks[0].old_start == 1
        assert hunks[0].old_count == 3
        assert hunks[0].new_start == 1
        assert hunks[0].new_count == 4
        assert hunks[0].added_lines == ["new line"]
        assert hunks[0].removed_lines == []

    def test_single_removed_line(self):
        raw = (
            "@@ -1,4 +1,3 @@\n"
            " line1\n"
            "-removed line\n"
            " line2\n"
            " line3\n"
        )
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 1
        assert hunks[0].removed_lines == ["removed line"]
        assert hunks[0].added_lines == []

    def test_modification_both_added_and_removed(self):
        raw = (
            "@@ -5,3 +5,3 @@\n"
            " context\n"
            "-old text\n"
            "+new text\n"
            " context\n"
        )
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 1
        assert hunks[0].removed_lines == ["old text"]
        assert hunks[0].added_lines == ["new text"]

    def test_multiple_hunks(self):
        raw = (
            "@@ -1,3 +1,4 @@\n"
            " a\n"
            "+b\n"
            " c\n"
            "@@ -10,3 +11,2 @@\n"
            " x\n"
            "-y\n"
            " z\n"
        )
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 2
        assert hunks[0].new_start == 1
        assert hunks[0].added_lines == ["b"]
        assert hunks[1].old_start == 10
        assert hunks[1].removed_lines == ["y"]

    def test_hunk_header_without_count(self):
        """Single-line hunks omit the count: @@ -1 +1 @@"""
        raw = "@@ -1 +1 @@\n-old\n+new\n"
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 1
        assert hunks[0].old_count == 1
        assert hunks[0].new_count == 1

    def test_ignores_diff_header_markers(self):
        """Lines starting with +++ or --- are file markers, not content."""
        raw = (
            "--- a/file.md\n"
            "+++ b/file.md\n"
            "@@ -1,2 +1,2 @@\n"
            "-old\n"
            "+new\n"
        )
        hunks = parse_unified_diff(raw)
        assert len(hunks) == 1
        assert hunks[0].added_lines == ["new"]
        assert hunks[0].removed_lines == ["old"]

    def test_unicode_content(self):
        raw = "@@ -1,2 +1,2 @@\n-旧内容\n+新内容\n"
        hunks = parse_unified_diff(raw)
        assert hunks[0].removed_lines == ["旧内容"]
        assert hunks[0].added_lines == ["新内容"]


class TestFormatHumanSummary:
    """Tests for format_human_summary()."""

    def test_empty_hunks_returns_empty(self):
        assert format_human_summary([], "") == ""

    def test_single_line_addition_with_heading(self):
        hunks = [DiffHunk(
            old_start=5, old_count=3, new_start=5, new_count=4,
            added_lines=["new bullet point"],
        )]
        content = "# Title\n\n## Section A\n\nSome text\nnew bullet point\n"
        result = format_human_summary(hunks, content)
        assert "Section A" in result
        assert "lines 5-8" in result
        assert 'Added: "new bullet point"' in result

    def test_single_line_deletion(self):
        hunks = [DiffHunk(
            old_start=3, old_count=2, new_start=3, new_count=1,
            removed_lines=["deleted text"],
        )]
        result = format_human_summary(hunks, "# Top\n\ndeleted text\n")
        assert "Top" in result
        assert 'Deleted: "deleted text"' in result

    def test_single_line_modification(self):
        hunks = [DiffHunk(
            old_start=2, old_count=1, new_start=2, new_count=1,
            removed_lines=["old word"],
            added_lines=["new word"],
        )]
        result = format_human_summary(hunks, "# Heading\nold word\n")
        assert 'Changed "old word" -> "new word"' in result

    def test_multi_line_modification(self):
        hunks = [DiffHunk(
            old_start=2, old_count=3, new_start=2, new_count=4,
            removed_lines=["a", "b"],
            added_lines=["x", "y", "z"],
        )]
        result = format_human_summary(hunks, "# H\na\nb\n")
        assert "Modified 2 line(s), added 3 line(s)" in result

    def test_no_heading_uses_top(self):
        hunks = [DiffHunk(
            old_start=1, old_count=1, new_start=1, new_count=2,
            added_lines=["new"],
        )]
        result = format_human_summary(hunks, "no heading here\n")
        assert "[top," in result

    def test_empty_file_content(self):
        hunks = [DiffHunk(
            old_start=1, old_count=0, new_start=1, new_count=1,
            added_lines=["first line"],
        )]
        result = format_human_summary(hunks, "")
        assert "top" in result
        assert 'Added: "first line"' in result

    def test_line_range_display(self):
        hunks = [DiffHunk(
            old_start=10, old_count=5, new_start=10, new_count=8,
            added_lines=["a", "b", "c"],
        )]
        result = format_human_summary(hunks, "# H\n" + "\n" * 15)
        assert "lines 10-17" in result

    def test_truncates_long_preview_text(self):
        long_text = "x" * 100
        hunks = [DiffHunk(
            old_start=1, old_count=1, new_start=1, new_count=1,
            removed_lines=[long_text],
            added_lines=[long_text + "y"],
        )]
        result = format_human_summary(hunks, long_text + "\n")
        # Changed preview truncates at 40 chars
        assert len(result) < 200

    def test_hunk_with_no_changes_skipped(self):
        hunks = [DiffHunk(
            old_start=1, old_count=3, new_start=1, new_count=3,
        )]
        result = format_human_summary(hunks, "a\nb\nc\n")
        assert result == ""
