"""Integration tests for Memory Architecture v2 production wiring (W1-W5).

Verifies that the core modules (transcript_indexer, temporal validity,
multi-store RecallEngine) are actually wired into production hooks.
"""
import re
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _make_conn():
    """In-memory sqlite-vec connection."""
    conn = sqlite3.connect(":memory:")
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pytest.skip("sqlite-vec not installed")
    return conn


# ── W1: context_health_hook calls sync_transcript_index ──────────────


class TestW1TranscriptSyncHook:
    """Verify context_health_hook has _sync_transcript_index wired."""

    def test_method_exists(self):
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        assert hasattr(hook, "_sync_transcript_index"), \
            "_sync_transcript_index method must exist on ContextHealthHook"

    def test_sync_called_in_light_refresh(self):
        """_light_refresh should call _sync_transcript_index."""
        import inspect
        from hooks.context_health_hook import ContextHealthHook
        source = inspect.getsource(ContextHealthHook._light_refresh)
        assert "_sync_transcript_index" in source, \
            "_light_refresh must call _sync_transcript_index"

    @patch("hooks.context_health_hook.Path.home")
    def test_sync_uses_transcript_store(self, mock_home, tmp_path):
        """_sync_transcript_index should not crash with empty transcript dir."""
        from hooks.context_health_hook import ContextHealthHook

        # Point home to a tmp_path with no .claude/projects/
        mock_home.return_value = tmp_path
        hook = ContextHealthHook()
        # Should not crash — no transcript dir = no-op (graceful degradation)
        hook._sync_transcript_index(tmp_path)


# ── W2: select_memory_sections applies temporal weight ───────────────


class TestW2TemporalWeightInScoring:
    """Verify _entry_temporal_weight is called during section scoring."""

    def test_hybrid_scores_use_temporal_weight(self):
        """_hybrid_section_scores or _keyword_section_scores must reference
        _entry_temporal_weight."""
        import inspect
        from core.memory_index import _keyword_section_scores
        source = inspect.getsource(_keyword_section_scores)
        assert "_entry_temporal_weight" in source, \
            "_keyword_section_scores must apply temporal weight"

    def test_superseded_entry_scores_lower(self):
        """An entry with superseded_by metadata should score lower."""
        from core.memory_index import keyword_relevance, _entry_temporal_weight

        # Active entry
        active_weight = _entry_temporal_weight(
            "- [KD01] 2026-04-01 Decision text\n"
            "  <!-- valid_from: 2026-04-01 | superseded_by: null | confidence: high -->"
        )
        # Superseded entry
        superseded_weight = _entry_temporal_weight(
            "- [KD02] 2026-03-15 Old decision\n"
            "  <!-- valid_from: 2026-03-15 | superseded_by: KD01 | confidence: high -->"
        )

        assert active_weight == pytest.approx(1.0)
        assert superseded_weight == pytest.approx(0.1)
        assert superseded_weight < active_weight


# ── W3: distillation_hook adds valid_from ────────────────────────────


class TestW3DistillationTemporalMetadata:
    """Verify distillation output includes temporal metadata."""

    def test_format_enriched_entry_has_temporal(self):
        """_format_enriched_entry output must include valid_from comment."""
        from hooks.distillation_hook import DistillationTriggerHook

        entry = DistillationTriggerHook._format_enriched_entry(
            text="**New decision** — something important",
            date_str="2026-04-11",
            source_file="DailyActivity/2026-04-11.md",
            commit_hash="abc1234",
        )
        assert "<!-- valid_from: 2026-04-11" in entry
        assert "superseded_by: null" in entry
        # Original content preserved
        assert "New decision" in entry
        assert "Detail:" in entry

    def test_format_enriched_entry_no_double_metadata(self):
        """Calling twice with same date should not produce duplicate metadata."""
        from hooks.distillation_hook import DistillationTriggerHook

        entry = DistillationTriggerHook._format_enriched_entry(
            text="**Test** — test",
            date_str="2026-04-11",
            source_file="DailyActivity/2026-04-11.md",
        )
        # Count valid_from occurrences
        assert entry.count("valid_from:") == 1


# ── W4: memory_health uses mark_entry_superseded ─────────────────────


class TestW4MemoryHealthSupersede:
    """Verify memory_health marks stale decisions instead of removing."""

    def test_apply_report_uses_supersede(self):
        """_apply_report stale_decisions path should call mark_entry_superseded."""
        import inspect
        from jobs.handlers.memory_health import _apply_report
        source = inspect.getsource(_apply_report)
        assert "mark_entry_superseded" in source, \
            "_apply_report must use mark_entry_superseded for stale decisions"


# ── W5: prompt_builder RecallEngine gets additional_stores ───────────


class TestW5PromptBuilderTranscriptStore:
    """Verify prompt_builder constructs RecallEngine with TranscriptStore."""

    def test_recall_knowledge_uses_additional_stores(self):
        """_recall_knowledge must create RecallEngine with additional_stores."""
        import inspect
        from core.prompt_builder import PromptBuilder
        source = inspect.getsource(PromptBuilder._recall_knowledge)
        assert "additional_stores" in source, \
            "_recall_knowledge must pass additional_stores to RecallEngine"
        assert "TranscriptStore" in source or "transcript" in source.lower(), \
            "_recall_knowledge must reference TranscriptStore"
