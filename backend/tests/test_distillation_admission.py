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
        # _admit_memory_lesson returns (verdict, section, reason); these tests assert
        # on verdict/section only, so drop the reason token here to keep call sites 2-tuple.
        verdict, section, _reason = DistillationTriggerHook._admit_memory_lesson(text)
        return verdict, section

    def test_structural_noise_discarded(self):
        # a table fragment → discard (real noise, safe to drop), no judge needed
        verdict, section = self._admit("| col | col |")
        assert verdict == "discard"

    def test_keep_type_lesson_judge_pass_is_AUTO(self):
        # AUTONOMY-FIRST (run_86f44f35): keep_type_holdback removed — a KEEP_TYPE
        # (principle/decision) that the judge PASSES now AUTO-writes to its type section.
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section = self._admit(
                "Principle: confidence is a counter-signal; verify before asserting always.")
        assert verdict == "auto"
        assert section == "Principles"  # KEEP_TYPE routes to its type's MEMORY section

    def test_judge_suspect_is_discard_not_review(self):
        # AUTONOMY-FIRST: judge suspect → discard (recoverable archive), never review.
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("suspect", "dubious")):
            verdict, section = self._admit(
                "The streaming reconcile sometimes duplicates a bubble on tab switch here.")
        assert verdict == "discard"

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


class TestDiscardArchive:
    """AUTONOMY-FIRST (run_86f44f35): a discarded entry (judge non-pass) goes to a
    RECOVERABLE archive (discarded-lessons.jsonl) — NOT a human-review sink. The
    DailyActivity source is marked distilled=True after the cycle, so the archive is the
    only recovery path for a fallible-judge drop."""

    def test_archive_writes_recoverable_jsonl(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx_dir = Path(tmpdir) / ".context"
            ok = DistillationTriggerHook._archive_discarded(
                ctx_dir, "a discarded raw lesson", "- 2026-08-10: a discarded raw lesson", "judge:suspect")
            assert ok is True
            sink = ctx_dir / "discarded-lessons.jsonl"
            assert sink.is_file()
            rec = json.loads(sink.read_text().strip().splitlines()[0])
            assert rec["raw"] == "a discarded raw lesson"
            assert rec["reason"] == "judge:suspect"

    def test_archive_failure_is_best_effort(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            clash = Path(tmpdir) / "clash"
            clash.write_text("i am a file, not a dir")
            ok = DistillationTriggerHook._archive_discarded(clash / "sub", "x", "y", "r")
            assert ok is False


class TestC6EvolutionGate:
    """C6 + AUTONOMY-FIRST (run_86f44f35): the EVOLUTION auto-write path is gated by the
    judge. Pass → admitted (auto-write, incl corrections — no keep-type holdback); non-pass
    → discard to the recoverable archive (never a human sink)."""

    def test_correction_judge_pass_is_admitted(self):
        # AUTONOMY-FIRST: a correction the judge PASSES now auto-writes (keep-type holdback gone).
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"; ctx.mkdir()
            with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
                admitted = DistillationTriggerHook._gate_evolution_entries(
                    [("2026-08-10", "Correction: never auto-deploy without approval, verify first.")],
                    ctx, "correction")
            assert len(admitted) == 1, "judge-pass correction must be admitted (no keep-type hold)"

    def test_correction_judge_suspect_is_discarded_archived(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"; ctx.mkdir()
            with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("suspect", "t")):
                admitted = DistillationTriggerHook._gate_evolution_entries(
                    [("2026-08-10", "Correction: some dubious unverifiable claim about the world here.")],
                    ctx, "correction")
            assert admitted == []
            sink = ctx / "discarded-lessons.jsonl"
            assert sink.is_file(), "non-pass → recoverable archive"

    def test_structural_noise_discarded_archived(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"; ctx.mkdir()
            admitted = DistillationTriggerHook._gate_evolution_entries(
                [("2026-08-10", "| junk | table |")], ctx, "correction")
            assert admitted == []
            # discarded (structural noise) → archived (autonomy-first: one path, recoverable)
            sink = ctx / "discarded-lessons.jsonl"
            assert sink.is_file()

    def test_decision_judge_pass_is_auto(self):
        """AUTONOMY-FIRST: a distilled decision (KEEP_TYPE) that passes the judge → AUTO
        (routes to Decisions), NOT held."""
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section, _reason = DistillationTriggerHook._admit_memory_lesson(
                "chose single-writer MessageStore to kill the reconcile race for good")
        assert verdict == "auto"
        assert section == "Decisions"

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
