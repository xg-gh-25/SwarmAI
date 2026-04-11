"""Tests for P2: Temporal Validity Windows on MEMORY.md entries.

Validates HTML comment metadata (valid_from, superseded_by), scoring
weight reduction for superseded entries, distillation hook integration,
and memory_health superseded_by linking.
"""
import re
import textwrap
from unittest.mock import patch, MagicMock

import pytest


# ── Sample MEMORY.md content ─────────────────────────────────────────

SAMPLE_MEMORY_WITH_TEMPORAL = textwrap.dedent("""\
## Key Decisions

- [KD01] 2026-04-01 Single-agent with role-switching > multi-agent
  <!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->

- [KD02] 2026-03-19 Multi-session re-architecture v7 adopted
  <!-- valid_from: 2026-03-19 | superseded_by: null | confidence: high -->

- [KD03] 2026-03-15 Old architecture — monolithic AgentManager
  <!-- valid_from: 2026-03-15 | superseded_by: KD02 | confidence: high -->

## Lessons Learned

- [LL01] 2026-04-02 Incremental scope creep triggers mode check
  <!-- valid_from: 2026-04-02 | superseded_by: null | confidence: high -->
""")

SAMPLE_MEMORY_WITHOUT_TEMPORAL = textwrap.dedent("""\
## Key Decisions

- [KD01] 2026-04-01 Single-agent with role-switching > multi-agent

- [KD02] 2026-03-19 Multi-session re-architecture v7 adopted

## Lessons Learned

- [LL01] 2026-04-02 Incremental scope creep triggers mode check
""")


# ── Test: Parse temporal metadata ────────────────────────────────────


class TestParseTemporalMetadata:
    """Test extraction of valid_from/superseded_by from HTML comments."""

    def test_parse_valid_metadata(self):
        from core.memory_index import parse_temporal_metadata

        meta = parse_temporal_metadata(
            "<!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->"
        )
        assert meta["valid_from"] == "2026-04-01"
        assert meta["superseded_by"] is None
        assert meta["confidence"] == "high"

    def test_parse_superseded_entry(self):
        from core.memory_index import parse_temporal_metadata

        meta = parse_temporal_metadata(
            "<!-- valid_from: 2026-03-15 | superseded_by: KD02 | confidence: high -->"
        )
        assert meta["superseded_by"] == "KD02"

    def test_parse_missing_metadata_returns_none(self):
        from core.memory_index import parse_temporal_metadata

        meta = parse_temporal_metadata("No HTML comment here")
        assert meta is None

    def test_parse_empty_string(self):
        from core.memory_index import parse_temporal_metadata

        meta = parse_temporal_metadata("")
        assert meta is None


# ── Test: Temporal scoring in select_memory_sections ─────────────────


class TestTemporalScoring:
    """Test that superseded entries are scored at 0.1x weight."""

    def test_superseded_entry_scores_low(self):
        from core.memory_index import _entry_temporal_weight

        # Superseded entry
        weight = _entry_temporal_weight(
            "<!-- valid_from: 2026-03-15 | superseded_by: KD02 | confidence: high -->"
        )
        assert weight == pytest.approx(0.1, abs=0.01)

    def test_active_entry_scores_full(self):
        from core.memory_index import _entry_temporal_weight

        # Active entry
        weight = _entry_temporal_weight(
            "<!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->"
        )
        assert weight == pytest.approx(1.0, abs=0.01)

    def test_no_metadata_scores_full(self):
        """Entries without temporal metadata should have full weight (backward compat)."""
        from core.memory_index import _entry_temporal_weight

        weight = _entry_temporal_weight("No metadata here")
        assert weight == pytest.approx(1.0, abs=0.01)


# ── Test: Format temporal metadata ───────────────────────────────────


class TestFormatTemporalMetadata:
    """Test generating HTML comment metadata string."""

    def test_format_new_entry(self):
        from core.memory_index import format_temporal_metadata

        meta = format_temporal_metadata(valid_from="2026-04-11")
        assert "valid_from: 2026-04-11" in meta
        assert "superseded_by: null" in meta
        assert meta.startswith("<!--")
        assert meta.endswith("-->")

    def test_format_superseded_entry(self):
        from core.memory_index import format_temporal_metadata

        meta = format_temporal_metadata(
            valid_from="2026-03-15", superseded_by="KD02"
        )
        assert "superseded_by: KD02" in meta

    def test_format_with_confidence(self):
        from core.memory_index import format_temporal_metadata

        meta = format_temporal_metadata(
            valid_from="2026-04-01", confidence="medium"
        )
        assert "confidence: medium" in meta


# ── Test: Distillation adds valid_from ───────────────────────────────


class TestDistillationTemporalMetadata:
    """Test that distillation hook adds valid_from to new entries."""

    def test_add_temporal_to_new_entry(self):
        from core.memory_index import add_temporal_metadata_to_entry

        entry = "- [KD28] 2026-04-11 New decision about memory architecture"
        result = add_temporal_metadata_to_entry(entry, valid_from="2026-04-11")
        assert "<!-- valid_from: 2026-04-11" in result
        assert "superseded_by: null" in result
        # Original entry text preserved
        assert "New decision about memory architecture" in result

    def test_does_not_double_add(self):
        """If entry already has temporal metadata, don't add again."""
        from core.memory_index import add_temporal_metadata_to_entry

        entry = textwrap.dedent("""\
        - [KD01] 2026-04-01 Existing decision
          <!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->""")
        result = add_temporal_metadata_to_entry(entry, valid_from="2026-04-11")
        # Should NOT have two metadata comments
        assert result.count("<!-- valid_from:") == 1


# ── Test: Memory health superseded_by linking ────────────────────────


class TestMemoryHealthSupersede:
    """Test that memory_health marks stale entries with superseded_by."""

    def test_mark_superseded(self):
        from core.memory_index import mark_entry_superseded

        content = SAMPLE_MEMORY_WITHOUT_TEMPORAL
        result = mark_entry_superseded(content, old_key="KD02", new_key="KD99")
        # Should add superseded_by metadata after the entry
        assert "superseded_by: KD99" in result
        # Original entry text preserved
        assert "Multi-session re-architecture v7 adopted" in result

    def test_mark_superseded_preserves_other_entries(self):
        from core.memory_index import mark_entry_superseded

        content = SAMPLE_MEMORY_WITHOUT_TEMPORAL
        result = mark_entry_superseded(content, old_key="KD02", new_key="KD99")
        # KD01 should be untouched
        assert "Single-agent with role-switching" in result
        # LL01 should be untouched
        assert "Incremental scope creep" in result

    def test_mark_nonexistent_key_returns_unchanged(self):
        from core.memory_index import mark_entry_superseded

        content = SAMPLE_MEMORY_WITHOUT_TEMPORAL
        result = mark_entry_superseded(content, old_key="KD_NONEXISTENT", new_key="KD99")
        assert result == content

    def test_update_existing_superseded_metadata(self):
        """If entry already has temporal metadata, update superseded_by field."""
        from core.memory_index import mark_entry_superseded

        content = SAMPLE_MEMORY_WITH_TEMPORAL
        # KD01 currently has superseded_by: null — mark it superseded
        result = mark_entry_superseded(content, old_key="KD01", new_key="KD99")
        # Should now show superseded_by: KD99
        assert "superseded_by: KD99" in result


# ── Test: locked_write preserves temporal metadata ───────────────────


class TestLockedWritePreservesMetadata:
    """Test that locked_write operations don't strip HTML comments."""

    def test_html_comments_survive_write(self, tmp_path):
        """Writing to MEMORY.md should preserve temporal HTML comments."""
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(SAMPLE_MEMORY_WITH_TEMPORAL, encoding="utf-8")

        content = memory_path.read_text(encoding="utf-8")
        assert "<!-- valid_from: 2026-04-01" in content
        assert "<!-- valid_from: 2026-03-15 | superseded_by: KD02" in content
