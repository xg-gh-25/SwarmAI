"""Tests for the improvement_writeback_hook UNIFIED intake (run_4c5f81ce).

After unification, the hook no longer writes IMPROVEMENT.md with its own
_insert_after_header + bespoke dedup. It routes each extracted lesson through
ddd_cultivation.apply_to_ddd (the shared admission chokepoint) via
asyncio.to_thread, tagged source_stage="writeback".

Covers:
  AC1 — a writeback lesson duplicating an existing entry (either format) is
        rejected by the shared content_signature dedup (not written twice).
  AC3 — the sync apply_to_ddd is offloaded via asyncio.to_thread (never blocks
        the event loop the hook is awaited on).
  AC4 — a novel writeback lesson lands in IMPROVEMENT.md with honest
        source_stage="writeback" attribution (not "auto-cultivated").
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from core.session_hooks import HookContext
from hooks.improvement_writeback_hook import ImprovementWritebackHook


def _ctx(session_id: str = "f1f7201b1234") -> HookContext:
    return HookContext(
        session_id=session_id,
        agent_id="agent_default",
        message_count=20,
        session_start_time="2026-07-20T00:00:00Z",
        session_title="test session",
    )


class TestWritebackRoutesThroughAdmission:
    """AC1 + AC4: writeback lessons go through apply_to_ddd (shared dedup +
    honest provenance), NOT the old _insert_after_header."""

    def test_novel_lesson_lands_with_writeback_attribution(self):
        """AC4: a new lesson is written to IMPROVEMENT.md via the shared path,
        tagged 'writeback' (honest provenance, not 'auto-cultivated')."""
        hook = ImprovementWritebackHook(workspace_path="/unused")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Failed\n\n")
            lessons = {"worked": [], "failed": ["retry loop reused a poisoned subprocess"]}

            asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            text = doc.read_text()
            assert "retry loop reused a poisoned subprocess" in text
            # honest provenance: attribution carries the writeback source label,
            # NOT reflect/auto-cultivated
            assert "writeback" in text

    def test_duplicate_against_cultivation_format_is_rejected(self):
        """AC1: a writeback lesson whose text already exists as a CULTIVATION-
        format entry is deduped (not written a second time)."""
        hook = ImprovementWritebackHook(workspace_path="/unused")
        text = "prevention over recovery beats runtime error handling"
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text(
                "# L\n\n## What Failed\n\n"
                f"- {text} (2026-01-01, run_x, auto-cultivated)\n"
            )
            lessons = {"worked": [], "failed": [text]}

            asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            # still exactly one occurrence of the lesson text
            assert doc.read_text().count(text) == 1


class TestWritebackOffloadsEventLoop:
    """AC3: the sync apply_to_ddd is called via asyncio.to_thread so it never
    blocks the event loop the hook is awaited on (session_hooks.py:577)."""

    def test_apply_to_ddd_runs_via_to_thread(self, monkeypatch):
        hook = ImprovementWritebackHook(workspace_path="/unused")
        calls = {"to_thread": 0}
        real_to_thread = asyncio.to_thread

        async def spy_to_thread(fn, *args, **kwargs):
            calls["to_thread"] += 1
            return await real_to_thread(fn, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy_to_thread)

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# L\n\n## What Worked\n\n")
            lessons = {"worked": ["clean single-writer intake works"], "failed": []}

            asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

        assert calls["to_thread"] >= 1, "apply_to_ddd must be offloaded via asyncio.to_thread"
