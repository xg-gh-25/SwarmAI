"""Tests for the improvement_writeback_hook UNIFIED intake (run_4c5f81ce)
+ C4a unified-gate routing (run_0d60e04e).

The hook routes each extracted lesson through the shared admission path. C4a change:
it no longer BLIND-calls apply_to_ddd — it goes through admission_band FIRST (the same
decision gate REFLECT uses). A writeback lesson is session-sourced (trust=n/a), so on a
non-protected doc (IMPROVEMENT.md) it gets the self_adversarial judge; only an `auto`
verdict applies, `review` escalates to the human queue, `discard` is noise.

Covers:
  AC1 — a duplicating lesson is deduped by the shared content_signature.
  AC3 — the sync gate+apply is offloaded via asyncio.to_thread (never blocks the loop).
  AC4 — a judge-`auto` novel lesson lands with honest source_stage="writeback".
  C4a — writeback routes through admission_band (not blind apply); review→escalate,
        discard→drop; the self_adversarial judge is invoked for trust=n/a lessons.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import core.ddd_cultivation as _dc
from core.session_hooks import HookContext
from hooks.improvement_writeback_hook import ImprovementWritebackHook


def _judge(verdict: str):
    """Patch the self_adversarial judge (Bedrock) to a fixed verdict — writeback lessons
    are trust=n/a, so admission_band runs the judge; tests must not hit the network."""
    return patch.object(_dc, "self_adversarial_judge", lambda *a, **k: (verdict, "test"))


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

    def test_novel_lesson_applied_when_gate_autos(self):
        """AC4 (C4a-updated): a novel lesson lands in IMPROVEMENT.md with honest
        'writeback' provenance — but ONLY when the gate returns `auto`. Writeback
        proposals are confidence=0.5 (< the 0.7 auto floor), so to exercise the
        APPLY path we bump confidence above the floor (the low-confidence path is
        covered by test_low_confidence_writeback_escalates below)."""
        import unittest.mock as m
        hook = ImprovementWritebackHook(workspace_path="/unused")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Failed\n\n")
            lessons = {"worked": [], "failed": ["retry loop reused a poisoned subprocess"]}

            # judge pass + a proposal that clears the 0.7 auto floor + clean magnitude
            # → admission_band returns auto → apply. (Patch confidence at construction
            #  by patching the gate's threshold read to accept 0.5 is fragile; instead
            #  raise the proposal confidence via a construction patch.)
            _orig_init = _dc.CultivationProposal.__init__
            def _hi_conf_init(self, *a, **k):
                k["confidence"] = 0.95
                _orig_init(self, *a, **k)
            with _judge("pass"), \
                 m.patch.object(_dc.CultivationProposal, "__init__", _hi_conf_init), \
                 m.patch("core.ddd_auto_approval.evaluate_auto_approval") as me:
                me.return_value = type("D", (), {"criteria_met": {
                    "small_magnitude": True, "circuit_breaker_ok": True}})()
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            text = doc.read_text()
            # INSERT-ONLY normalization splits the lesson with `**…**` markers; the
            # no-data-loss invariant is that stripping them restores the text verbatim.
            assert "retry loop reused a poisoned subprocess" in text.replace("**", "")
            assert "writeback" in text  # honest provenance, not 'auto-cultivated'

    def test_low_confidence_writeback_discarded_to_archive(self):
        """AUTONOMY-FIRST (run_86f44f35): DEFAULT writeback confidence (0.5) is below the
        0.7 auto floor, so even a judge-pass lesson does NOT auto-write — it's DISCARDED to
        the recoverable archive (NOT a human queue; queue = 0)."""
        hook = ImprovementWritebackHook(workspace_path="/unused")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# Lessons\n\n## What Failed\n\n")
            lessons = {"worked": [], "failed": ["retry loop reused a poisoned subprocess again"]}

            with _judge("pass"):  # judge passes, but confidence 0.5 < 0.7 floor
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            assert "retry loop reused a poisoned" not in doc.read_text().replace("**", "")
            queue = list((project_dir / ".artifacts" / "proposals").glob("*.json")) \
                if (project_dir / ".artifacts" / "proposals").is_dir() else []
            assert queue == [], "autonomy-first: no human queue"
            archive = project_dir / ".artifacts" / "discarded-writeback.jsonl"
            assert archive.is_file(), "below-floor lesson archived (recoverable), not vanished"

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

            with _judge("pass"):
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

            with _judge("pass"):
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

        assert calls["to_thread"] >= 1, "gate+apply must be offloaded via asyncio.to_thread"


class TestC4aUnifiedGateRouting:
    """C4a: writeback routes through admission_band, NOT a blind apply_to_ddd."""

    def test_judge_suspect_discarded_to_archive_not_queued(self):
        """AUTONOMY-FIRST (run_86f44f35): judge-suspect → DISCARD to the recoverable
        archive (discarded-writeback.jsonl), NOT written to the doc and NOT a human queue."""
        import json as _json
        hook = ImprovementWritebackHook(workspace_path="/unused")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# L\n\n## What Failed\n\n")
            lessons = {"worked": [], "failed": ["a dubious unverifiable claim about the world"]}

            with _judge("suspect"):
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            # NOT applied to the doc …
            assert "dubious unverifiable claim" not in doc.read_text().replace("**", "")
            # … NO human queue (autonomy-first: queue = 0) …
            queue = list((project_dir / ".artifacts" / "proposals").glob("*.json")) \
                if (project_dir / ".artifacts" / "proposals").is_dir() else []
            assert queue == [], "autonomy-first: no human proposal queue"
            # … but recoverable in the discard archive (not silently lost).
            archive = project_dir / ".artifacts" / "discarded-writeback.jsonl"
            assert archive.is_file(), "discarded lesson must land in the recoverable archive"
            rec = _json.loads(archive.read_text().strip().splitlines()[0])
            assert "dubious unverifiable claim" in rec["content"]

    def test_judge_noise_discarded_to_archive(self):
        """A judge-`noise` → discard → not applied, no queue, archived (recoverable)."""
        hook = ImprovementWritebackHook(workspace_path="/unused")
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# L\n\n## What Failed\n\n")
            lessons = {"worked": [], "failed": ["some genuine-looking lesson text here now"]}

            with _judge("noise"):
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

            assert "some genuine-looking lesson" not in doc.read_text().replace("**", "")
            archive = project_dir / ".artifacts" / "discarded-writeback.jsonl"
            assert archive.is_file(), "noise discard is archived (recoverable), not silently gone"

    def test_judge_is_actually_invoked_for_writeback(self):
        """The whole point of C4a: a trust=n/a writeback lesson MUST reach the judge
        (it was blind-applied before). Prove the judge is called."""
        hook = ImprovementWritebackHook(workspace_path="/unused")
        calls = {"n": 0}

        def _spy(*a, **k):
            calls["n"] += 1
            return ("pass", "spied")

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            doc = project_dir / "IMPROVEMENT.md"
            doc.write_text("# L\n\n## What Worked\n\n")
            lessons = {"worked": ["a real reusable engineering lesson worth keeping here"], "failed": []}

            with patch.object(_dc, "self_adversarial_judge", _spy):
                asyncio.run(hook._append_lessons(doc, lessons, _ctx()))

        assert calls["n"] == 1, "writeback (trust=n/a) must go through the self_adversarial judge"
