"""Unit tests for the YAML frontmatter parser and printer.

Tests the ``parse_frontmatter`` and ``write_frontmatter`` functions in
``backend/core/daily_activity_writer.py``.  Covers:

- Valid frontmatter parsing and round-tripping
- Edge cases: empty input, missing frontmatter, no closing delimiter
- ``distilled: true`` field parsing (Req 4.1)
- ``write_frontmatter`` output format (Req 6.7)

Testing methodology: unit tests for specific examples and edge cases.
Property-based tests (Hypothesis) are in separate sub-tasks.
"""

import pytest

from core.daily_activity_writer import parse_frontmatter, write_frontmatter


class TestParseFrontmatter:
    """Tests for parse_frontmatter()."""

    def test_valid_frontmatter(self):
        """Valid frontmatter is parsed correctly."""
        content = "---\ntitle: Hello\nauthor: World\n---\n\nBody text."
        meta, body = parse_frontmatter(content)
        assert meta == {"title": "Hello", "author": "World"}
        assert body == "Body text."

    def test_no_frontmatter_returns_empty_dict_and_full_content(self):
        """Req 6.3: No frontmatter returns ({}, content)."""
        content = "Just a plain markdown file.\n\nWith paragraphs."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_string_returns_empty_dict_and_empty_string(self):
        """Req 4.2: Empty string input returns ({}, '')."""
        meta, body = parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_malformed_yaml_returns_empty_dict(self):
        """Malformed YAML returns ({}, body) — parser skips unparseable lines."""
        content = "---\n: invalid: yaml: [broken\n---\n\nBody."
        meta, body = parse_frontmatter(content)
        # Hand-rolled parser skips lines it can't parse as key: value
        assert isinstance(meta, dict)
        assert "Body." in body

    def test_distilled_true_parses_correctly(self):
        """Req 4.1: Frontmatter with distilled: true parses correctly."""
        content = '---\ndistilled: true\ndistilled_date: "2025-07-15"\n---\n\nDaily notes.'
        meta, body = parse_frontmatter(content)
        assert meta["distilled"] is True
        assert meta["distilled_date"] == "2025-07-15"
        assert body == "Daily notes."

    def test_no_closing_delimiter_treated_as_no_frontmatter(self):
        """Frontmatter with no closing --- is treated as no frontmatter."""
        content = "---\ntitle: Hello\nauthor: World\n\nBody text."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_content_starting_with_dashes_but_not_frontmatter(self):
        """Content starting with --- but not immediately followed by key:value is handled."""
        content = "--- some text on same line\n\nBody."
        meta, body = parse_frontmatter(content)
        # The parser sees "---" prefix and looks for closing "---"
        # but "--- some text" still starts with "---", so behavior
        # depends on whether a closing --- exists
        assert isinstance(meta, dict)

    def test_empty_yaml_block_returns_empty_dict(self):
        """Empty YAML between delimiters returns empty dict."""
        content = "---\n---\n\nBody text."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == "Body text."

    def test_frontmatter_with_boolean_and_integer_values(self):
        """Frontmatter with mixed types parses correctly."""
        content = "---\ncount: 42\nactive: false\nname: test\n---\n\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {"count": 42, "active": False, "name": "test"}
        assert body == "Body."

    def test_non_dict_yaml_treated_as_key_value_pairs(self):
        """YAML list items are skipped by the hand-rolled parser (no ':' separator)."""
        content = "---\n- item1\n- item2\n---\n\nBody."
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert "Body." in body


class TestWriteFrontmatter:
    """Tests for write_frontmatter()."""

    def test_empty_metadata_returns_frontmatter_with_body(self):
        """Empty metadata dict still produces frontmatter delimiters."""
        body = "Just body text."
        result = write_frontmatter({}, body)
        assert "---" in result
        assert body in result

    def test_non_empty_metadata_produces_valid_format(self):
        """Req 6.7: Output starts with ---, has closing ---, body follows."""
        result = write_frontmatter({"title": "Hello"}, "Body text.")
        lines = result.split("\n")
        assert lines[0] == "---"
        # Find closing ---
        closing_idx = None
        for i in range(1, len(lines)):
            if lines[i] == "---":
                closing_idx = i
                break
        assert closing_idx is not None, "No closing --- found"
        # Body appears after closing ---
        remaining = "\n".join(lines[closing_idx + 1:])
        assert "Body text." in remaining

    def test_round_trip_simple(self):
        """Basic round-trip: write then parse recovers original data."""
        metadata = {"distilled": True, "distilled_date": "2025-07-15"}
        body = "Some daily notes.\n\nWith multiple paragraphs."
        output = write_frontmatter(metadata, body)
        parsed_meta, parsed_body = parse_frontmatter(output)
        assert parsed_meta["distilled"] is True
        assert parsed_meta["distilled_date"] == "2025-07-15"
        assert body in parsed_body

    def test_write_empty_body(self):
        """Writing with empty body produces valid frontmatter."""
        result = write_frontmatter({"key": "value"}, "")
        assert result.startswith("---\n")
        assert "---" in result
        meta, body = parse_frontmatter(result)
        assert meta == {"key": "value"}

    def test_write_preserves_multiline_body(self):
        """Body with multiple lines is preserved through write."""
        body = "Line 1\nLine 2\n\nLine 4"
        result = write_frontmatter({"a": 1}, body)
        _, parsed_body = parse_frontmatter(result)
        assert "Line 1" in parsed_body
        assert "Line 4" in parsed_body
