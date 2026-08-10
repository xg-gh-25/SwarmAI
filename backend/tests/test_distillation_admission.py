"""C5 (run_0d60e04e): MEMORY auto-distillation now routes each lesson through the
unified ingestion_gate BEFORE writing to MEMORY.md — the survey's #1 rating-5 hole
(fully-automated, no adversarial gate). The gate runs noise→keep_type_holdback→judge;
only an "auto" lesson is written, "review" is sedimented to a recoverable failsafe
(NOT lost when the DailyActivity file is marked distilled), "discard" is real noise.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import core.ingestion_gate as _ig
from hooks.distillation_hook import DistillationTriggerHook


class TestMemoryDistillAdmission:
    def _admit(self, text):
        return DistillationTriggerHook._admit_memory_lesson(text)

    def test_structural_noise_discarded(self):
        # a table fragment → discard (real noise, safe to drop), no judge needed
        verdict, section = self._admit("| col | col |")
        assert verdict == "discard"

    def test_keep_type_lesson_held_for_review(self):
        # a KEEP_TYPE (principle/correction/decision/model) → review (held, recoverable),
        # NOT discard — permanent knowledge a human should see, not noise.
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section = self._admit(
                "Principle: confidence is a counter-signal; verify before asserting always.")
        assert verdict == "review"

    def test_judge_suspect_is_review_not_discard(self):
        # judge suspect → review (held, recoverable), NOT dropped as noise
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("suspect", "dubious")):
            verdict, section = self._admit(
                "The streaming reconcile sometimes duplicates a bubble on tab switch here.")
        assert verdict == "review"

    def test_judge_noise_is_discard(self):
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("noise", "junk")):
            verdict, section = self._admit(
                "The streaming reconcile sometimes duplicates a bubble on tab switch here.")
        assert verdict == "discard"

    def test_judge_pass_admits_with_section(self):
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "ok")):
            verdict, section = self._admit(
                "A dedicated ThreadPoolExecutor avoids blocking the shared event loop pool.")
        assert verdict == "auto"
        assert section  # a real MEMORY section name

    def test_short_memory_fragment_survives_noise_tier(self):
        # MEMORY must NOT apply the DDD ≥5-word value floor (Gate-1 ⓐ) — a short
        # decision fragment is not structural noise, so it reaches the judge.
        called = {"n": 0}
        def _spy(*a, **k):
            called["n"] += 1
            return ("pass", "ok")
        with patch.object(_ig, "self_adversarial_judge", _spy):
            self._admit("enableMCP = always true")
        assert called["n"] == 1, "short MEMORY fragment must reach the judge, not be floored"


class TestHeldLessonSediment:
    """Gate-2 HIGH: a review-held lesson must be sedimented to a recoverable sink,
    because the DailyActivity source is marked distilled=True after the cycle."""

    def test_sediment_writes_recoverable_jsonl(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx_dir = Path(tmpdir) / ".context"
            ok = DistillationTriggerHook._sediment_held_lesson(
                ctx_dir, "a held raw lesson", "- 2026-08-10: a held raw lesson\n  Detail: x")
            assert ok is True
            sink = ctx_dir / "memory-held-lessons.jsonl"
            assert sink.is_file()
            rec = json.loads(sink.read_text().strip().splitlines()[0])
            assert rec["raw"] == "a held raw lesson"

    def test_sediment_failure_is_best_effort(self):
        # a bad path (mkdir under a FILE) → OSError → returns False, never raises
        with tempfile.TemporaryDirectory() as tmpdir:
            clash = Path(tmpdir) / "clash"
            clash.write_text("i am a file, not a dir")
            ok = DistillationTriggerHook._sediment_held_lesson(clash / "sub", "x", "y")
            assert ok is False


class TestC6EvolutionGate:
    """C6: the EVOLUTION auto-write path (finding D format-hole) is gated. An auto-
    extracted correction is trust=n/a + KEEP_TYPE → held for review (sedimented), NOT
    silently appended to the constitutional store."""

    def test_correction_is_held_not_auto_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"
            ctx.mkdir()
            with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
                admitted = DistillationTriggerHook._gate_evolution_entries(
                    [("2026-08-10", "Correction: never auto-deploy without approval, verify first.")],
                    ctx, "correction")
            # KEEP_TYPE correction → held → NOT admitted for auto-write
            assert admitted == []
            # … but sedimented to the recoverable sink (not lost)
            sink = ctx / "memory-held-lessons.jsonl"
            assert sink.is_file()
            assert "never auto-deploy" in sink.read_text()

    def test_structural_noise_correction_discarded_not_sedimented(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"
            ctx.mkdir()
            admitted = DistillationTriggerHook._gate_evolution_entries(
                [("2026-08-10", "| junk | table |")], ctx, "correction")
            assert admitted == []
            # discard (noise) → NOT sedimented (only review-held lessons are)
            sink = ctx / "memory-held-lessons.jsonl"
            assert not sink.is_file()

    def test_decisions_path_gated_same_as_lessons(self):
        """E2E-review fix: the all_decisions → MEMORY path is gated by _admit_memory_lesson
        (it was the sibling rating-5 hole the first C5 pass left ungated). A distilled
        decision is a KEEP_TYPE → review → held (not auto-written)."""
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section = DistillationTriggerHook._admit_memory_lesson(
                "chose single-writer MessageStore to kill the reconcile race for good")
        # a genuine decision routes to KEEP_TYPE (section None) → review, never auto
        assert verdict == "review"

    def test_format_hole_closed_at_extraction(self):
        """The format-hole: a bullet-ised **Corrections:** section with a table fragment
        must be filtered by structural_noise at extraction (defense-in-depth)."""
        body = (
            "**Corrections:**\n"
            "- | junk | table | fragment |\n"
            "- a real user correction of agent behavior that is long enough\n"
        )
        got = DistillationTriggerHook._extract_corrections(body)
        assert not any("junk | table" in c for c in got), "structural noise must be filtered"
        assert any("real user correction" in c for c in got)
