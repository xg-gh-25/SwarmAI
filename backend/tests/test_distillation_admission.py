"""C5 (run_0d60e04e): MEMORY auto-distillation routes each lesson through the unified
ingestion_gate BEFORE writing to MEMORY.md — the survey's #1 rating-5 hole (fully-
automated, no adversarial gate). memory_distill runs deterministic HARD-DENY floors
FIRST (noise→thin→content_floor→episodic) so the brain is guarded even when the judge
is down, THEN the judge. "auto" → written; "pending" → deferred to distill-pending.jsonl
(judge unavailable — budget/infra — re-judged next cycle, CONVERGENT, never dropped);
"discard" → a real rejection (a floor, or the judge online refusing).
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

    def test_keep_type_lesson_judge_pass_writes_to_its_section(self):
        # CONVERGENCE (adversarial fix): a KEEP_TYPE is NOT held by a pre-judge
        # short-circuit — that could never be re-judged (keep_type_holdback is a pure
        # text function → same verdict forever → infinite requeue). Instead a keep-type
        # flows through the judge normally: judge PASS → auto-write to its type section
        # (autonomy-first, as 2c8fc37f intended). XG 乙's "don't DROP when the judge is
        # unavailable" is delivered by the CONVERGENT judge-down→pending path (tested
        # separately), not by an infinite pre-judge hold.
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section = self._admit(
                "Principle: confidence is a counter-signal; verify before asserting always.")
        assert verdict == "auto", f"keep-type + judge-pass must auto-write, got {verdict}"
        assert section == "Principles", f"keep-type must route to its section, got {section}"

    def test_keep_type_lesson_judge_down_defers_to_pending(self):
        # XG 乙: when the judge is UNAVAILABLE (infra error), a keep-type is NOT dropped —
        # it defers to pending, re-judged when the judge recovers (CONVERGENT: recovery
        # yields a real pass/discard, so it cannot loop forever).
        with patch.object(_ig, "self_adversarial_judge",
                          lambda *a, **k: ("suspect", "judge_error:EndpointConnectionError")):
            verdict, section = self._admit(
                "Principle: confidence is a counter-signal; verify before asserting always.")
        assert verdict == "pending", f"keep-type + judge-down must defer, got {verdict}"

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
        # MEMORY must NOT apply the DDD ≥5-word value floor (Gate-1 ⓐ) — a SHORT fragment
        # that carries real signal reaches the judge. Fixture must be short (proves no
        # word-floor) AND clear the restored content_floor (a 0.1-confidence fragment like
        # "enableMCP = always true" is correctly floored now — the hole the judge-only
        # path left). This still proves the point: shortness alone does not floor.
        called = {"n": 0}
        def _spy(*a, **k):
            called["n"] += 1
            return ("pass", "ok")
        with patch.object(_ig, "self_adversarial_judge", _spy):
            self._admit("Use single-writer MessageStore to end the reconcile race")
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


class TestBudgetDeferral:
    """judge-budget starvation fix: a 'judge:budget_exhausted' verdict means the judge
    ran out of budget THIS cycle (un-judged), NOT that the entry is noise. It must be
    DEFERRED to a re-ingestible pending queue and re-judged next cycle — never dropped
    to the discard archive. This is what stops a big session-close from silently losing
    real lessons past the 60-call budget."""

    def test_requeue_then_drain_roundtrip(self):
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"
            assert DistillationTriggerHook._requeue_pending(ctx, "lesson", "- 2026-08-10: L1") is True
            assert DistillationTriggerHook._requeue_pending(ctx, "decision", "- 2026-08-10: D1") is True
            pending = ctx / "distill-pending.jsonl"
            assert pending.is_file()
            decisions, lessons = DistillationTriggerHook._drain_pending(ctx)
            assert lessons == ["- 2026-08-10: L1"]
            assert decisions == ["- 2026-08-10: D1"]
            # drain DELETES the file so overflow re-defer converges (no infinite regrow)
            assert not pending.is_file(), "drain must delete the pending file"

    def test_drain_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"
            assert DistillationTriggerHook._drain_pending(ctx) == ([], [])

    def test_drain_ignores_malformed_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = Path(tmpdir) / ".context"
            ctx.mkdir(parents=True)
            (ctx / "distill-pending.jsonl").write_text(
                'not json\n{"kind":"lesson","enriched":"- ok"}\n{"kind":"lesson"}\n')
            decisions, lessons = DistillationTriggerHook._drain_pending(ctx)
            assert lessons == ["- ok"] and decisions == []  # bad + empty-enriched skipped

    def test_budget_exhausted_defers_not_discards(self, monkeypatch):
        """The load-bearing behavior: when the gate returns judge:budget_exhausted, the
        entry lands in the pending queue, NOT discarded-lessons.jsonl. Drive the real
        gate with budget=0 so every candidate is budget-exhausted."""
        import core.ingestion_gate as ig
        ig._judge_call_times.clear()
        monkeypatch.setattr(ig, "_JUDGE_BUDGET_MAX", 0)  # 0 → always budget_exhausted
        try:
            v, section, reason = DistillationTriggerHook._admit_memory_lesson(
                "A real reusable lesson about dedicated executors for blocking IO here.")
            assert reason == "judge:budget_exhausted", f"expected budget-exhausted, got {reason}"
            # And the caller routes THAT reason to requeue, not archive — assert the
            # branch condition the loops use.
            assert reason == "judge:budget_exhausted"
        finally:
            ig._judge_call_times.clear()


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

    def test_decision_keep_type_judge_pass_is_auto(self):
        """CONVERGENCE (adversarial fix): a distilled decision (KEEP_TYPE) flows through
        the judge — judge PASS → AUTO to Decisions (autonomy-first). It is NOT held by a
        pre-judge short-circuit (that never re-judges → infinite requeue). judge-down→
        pending (XG 乙) is the convergent deferral, tested on the principle case."""
        with patch.object(_ig, "self_adversarial_judge", lambda *a, **k: ("pass", "t")):
            verdict, section, _reason = DistillationTriggerHook._admit_memory_lesson(
                "chose single-writer MessageStore to kill the reconcile race for good")
        assert verdict == "auto", f"keep-type decision + judge-pass must auto-write, got {verdict}"
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
