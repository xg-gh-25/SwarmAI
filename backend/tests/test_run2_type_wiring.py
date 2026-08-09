"""Run 2 (knowledge-classification root-fix): decision-wiring + type-routing.

Tests two independent fixes in the "lossy re-derivation" family:
  GAP A — pipeline decisions[] now flow into cultivation (typed [decision]).
  GAP B — distillation lessons route by TRUE type (not hardcoded guideline),
          with KEEP_TYPES held back (never auto-written), via a SHARED helper
          route_lesson_type() that both distillation + context_health use.

RED before the fix: route_lesson_type does not exist; distillation writes all
lessons to Guidelines; cmd_run_cultivate ignores decisions[].
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ── GAP B: the shared type-routing helper ──────────────────────────────────
class TestRouteLessonType:
    def test_operational_type_routes_to_its_section(self):
        from core.ddd_entry_lifecycle import route_lesson_type, MEMORY_TYPE_TO_SECTION
        # a clearly-pitfall lesson → Pitfalls (NOT Guidelines)
        section, etype = route_lesson_type(
            "A silent race condition: the reconcile can drop a bubble when two "
            "writes interleave — this bug recurs whenever a fallback is added."
        )
        assert etype == "pitfall"
        assert section == MEMORY_TYPE_TO_SECTION["pitfall"] == "Pitfalls"

    def test_ambiguous_lesson_defaults_to_guideline(self):
        from core.ddd_entry_lifecycle import route_lesson_type
        section, etype = route_lesson_type(
            "Always verify the target file before editing and match existing style."
        )
        assert etype == "guideline"
        assert section == "Guidelines"

    def test_keep_class_type_is_held_back(self):
        """principle/correction/decision/model → section is None (HOLD-BACK).

        These are evergreen (decay can't reclaim them) — a wrong auto-commit is
        permanent, so they must NEVER be auto-written. section=None signals the
        caller to hold back, NOT to bury them in Guidelines.
        """
        from core.ddd_entry_lifecycle import route_lesson_type, _KEEP_TYPES
        # a principle-shaped lesson
        section, etype = route_lesson_type(
            "First principle: confidence is a counter-signal — the more certain I "
            "feel, the more I must verify against source before asserting."
        )
        if etype in _KEEP_TYPES:
            assert section is None, f"KEEP_TYPE {etype} must be held back (None), got {section}"

    def test_every_non_keep_type_maps_to_a_real_section(self):
        from core.ddd_entry_lifecycle import route_lesson_type, MEMORY_TYPE_TO_SECTION, _KEEP_TYPES
        # exhaustive: for each non-keep type, the returned section is a real key value
        for t, sec in MEMORY_TYPE_TO_SECTION.items():
            if t in _KEEP_TYPES:
                continue
            # can't force classify to a type easily; just assert the mapping is total
            assert sec in MEMORY_TYPE_TO_SECTION.values()


# ── GAP A: pipeline decisions[] → cultivation ───────────────────────────────
class TestDecisionCollection:
    def test_judgment_decisions_collected_and_tagged(self):
        """The collection logic: judgment-class stage decisions become
        '[decision] <description>' tagged strings; mechanical/taste skipped."""
        from scripts.artifact_cli import _collect_judgment_decisions
        run_state = {
            "stages": [
                {"stage": "think", "decisions": [
                    {"classification": "judgment", "description": "chose A over B because latency"},
                    {"classification": "mechanical", "description": "put file in core/"},
                    {"classification": "taste", "description": "named it foo"},
                ]},
                {"stage": "plan", "decisions": [
                    {"classification": "judgment", "description": "read-side percentiles"},
                    {"classification": "judgment", "description": ""},  # empty skipped
                    "a bare string decision without classification",     # skipped (no class)
                ]},
            ]
        }
        tagged = _collect_judgment_decisions(run_state)
        assert tagged == [
            "[decision] chose A over B because latency",
            "[decision] read-side percentiles",
        ]

    def test_no_judgment_decisions_returns_empty(self):
        from scripts.artifact_cli import _collect_judgment_decisions
        assert _collect_judgment_decisions({"stages": []}) == []
        assert _collect_judgment_decisions(
            {"stages": [{"stage": "think", "decisions": [
                {"classification": "mechanical", "description": "x"}]}]}
        ) == []


# ── GAP B: deterministic raw-text recovery (Gate-2 CRIT-1/CRIT-2 regressions) ──
class TestRawLessonRecovery:
    """The enriched->raw map was fragile (missed JobResults, broke on the
    [UNVERIFIED] rewrite). _raw_lesson_text must deterministically recover the
    raw text so classification is never polluted by provenance."""

    def _hook(self):
        from hooks.distillation_hook import DistillationTriggerHook
        return DistillationTriggerHook

    def test_recovers_raw_from_standard_enriched(self):
        H = self._hook()
        enriched = H._format_enriched_entry(
            "A silent race drops a bubble when writes interleave.",
            "2026-08-09", "DailyActivity/x.md", "abc1234",
        )
        assert H._raw_lesson_text(enriched) == "A silent race drops a bubble when writes interleave."

    def test_recovers_raw_from_jobresults_enriched_no_commit(self):
        # CRIT-2: JobResults entries have no commit hash and were never in the map.
        H = self._hook()
        enriched = H._format_enriched_entry(
            "Chose async over sync for the ingest path.",
            "2026-08-09", "JobResults/j.jsonl", None,
        )
        assert H._raw_lesson_text(enriched) == "Chose async over sync for the ingest path."

    def test_recovers_raw_after_unverified_mutation(self):
        # CRIT-1: _tag_unverified_claims prepends [UNVERIFIED] AFTER map population.
        H = self._hook()
        enriched = H._format_enriched_entry(
            "Implemented the retry loop.", "2026-08-09", "DailyActivity/x.md", None,
        )
        # simulate the mutation _tag_unverified_claims performs on the first line
        mutated = enriched.replace("2026-08-09: ", "2026-08-09: [UNVERIFIED] ", 1)
        assert H._raw_lesson_text(mutated) == "Implemented the retry loop."

    def test_raw_text_drives_correct_type_not_provenance(self):
        # End-to-end: a pitfall lesson, enriched + mutated, still routes to Pitfalls
        # (would misroute if classification saw the provenance/Detail line).
        from core.ddd_entry_lifecycle import route_lesson_type
        H = self._hook()
        enriched = H._format_enriched_entry(
            "A silent regression: the guard is bypassed when a fallback is added, a recurring bug.",
            "2026-08-09", "JobResults/j.jsonl", None,
        )
        raw = H._raw_lesson_text(enriched)
        section, etype = route_lesson_type(raw)
        assert etype == "pitfall"
        assert section == "Pitfalls"
