"""Tests for the DailyActivity distillation type-honoring fix (run_d4ba1c5b).

What is tested — the "source-side type declaration" root fix for the 82%
keyword-classifier skew:
  1. _parse_enrichment binds lessons↔lesson_types as normalized equal-length pairs
     (short→pad guideline, long→truncate, invalid→guideline, empty-lesson pairs
     dropped together — the index-alignment fragility Gate-1 flagged).
  2. _summary_to_jsonl_record emits a BOUND `lessons_typed` structure.
  3. distillation JSONL path prepends `[type]` from lessons_typed so
     route_lesson_type HONORS it (bypassing keyword guessing); process_reflection
     is tagged [correction]; legacy JSONL (no lessons_typed) falls back to keyword.
  4. _canonicalize_entry strips the leading [type] from the body AFTER routing —
     no double/triple tag (AC3), mutation-verified.

Methodology: unit-level on the pure static parsers/formatters (no LLM, no Bedrock).
"""
from __future__ import annotations

import pytest

from core.summarization import SummarizationPipeline, StructuredSummary, VALID_TYPE_SET
from core.daily_activity_writer import _summary_to_jsonl_record
from hooks.distillation_hook import DistillationTriggerHook


# ── AC1/AC2: parse binds + normalizes lessons↔lesson_types ──────────────────
class TestParseEnrichmentBinding:
    def _parse(self, obj: dict) -> dict:
        import json
        return SummarizationPipeline._parse_enrichment(json.dumps(obj))

    def test_equal_length_honored(self):
        out = self._parse({
            "lessons": ["A greedy classifier demotes principles", "Never trust char-count"],
            "lesson_types": ["principle", "pitfall"],
        })
        assert out["lessons"] == ["A greedy classifier demotes principles", "Never trust char-count"]
        assert out["lesson_types"] == ["principle", "pitfall"]

    def test_short_types_padded_to_guideline(self):
        out = self._parse({
            "lessons": ["one", "two", "three"],
            "lesson_types": ["pitfall"],  # shorter
        })
        assert len(out["lesson_types"]) == len(out["lessons"]) == 3
        assert out["lesson_types"] == ["pitfall", "guideline", "guideline"]

    def test_long_types_truncated(self):
        out = self._parse({
            "lessons": ["only one"],
            "lesson_types": ["principle", "pitfall", "model"],  # longer
        })
        assert len(out["lesson_types"]) == 1
        assert out["lesson_types"] == ["principle"]

    def test_invalid_type_falls_back_to_guideline(self):
        out = self._parse({
            "lessons": ["x", "y"],
            "lesson_types": ["bug", "insight"],  # neither in the 7-set
        })
        assert out["lesson_types"] == ["guideline", "guideline"]

    def test_empty_lesson_and_its_type_dropped_together(self):
        # The index-alignment crux: dropping an empty lesson must drop its paired
        # type too, or every subsequent pair shifts.
        out = self._parse({
            "lessons": ["keep me", "   ", "keep me too"],
            "lesson_types": ["principle", "pitfall", "correction"],
        })
        assert out["lessons"] == ["keep me", "keep me too"]
        assert out["lesson_types"] == ["principle", "correction"]  # NOT [principle, pitfall]

    def test_missing_lesson_types_key_all_guideline(self):
        out = self._parse({"lessons": ["a", "b"]})  # no lesson_types at all
        assert out["lesson_types"] == ["guideline", "guideline"]

    def test_all_types_in_valid_set(self):
        out = self._parse({
            "lessons": ["a", "b", "c"],
            "lesson_types": ["principle", "garbage", "correction"],
        })
        assert all(t in VALID_TYPE_SET for t in out["lesson_types"])


# ── AC4: writer emits bound lessons_typed ───────────────────────────────────
class TestJsonlRecordBinding:
    def _record(self, lessons, lesson_types):
        s = StructuredSummary()
        s.lessons = lessons
        s.lesson_types = lesson_types

        class _Ctx:
            session_id = "s1"
            session_start_time = "2026-08-15T00:00:00"
            message_count = 3
        return _summary_to_jsonl_record(s, _Ctx())

    def test_lessons_typed_bound(self):
        rec = self._record(["principle lesson", "trap lesson"], ["principle", "pitfall"])
        assert rec["lessons_typed"] == [
            {"text": "principle lesson", "type": "principle"},
            {"text": "trap lesson", "type": "pitfall"},
        ]
        # plain lessons retained for backward compat + markdown body stays prefix-free
        assert rec["lessons"] == ["principle lesson", "trap lesson"]

    def test_missing_types_padded_in_record(self):
        rec = self._record(["a", "b"], [])  # lesson_types empty
        assert [p["type"] for p in rec["lessons_typed"]] == ["guideline", "guideline"]


# ── AC5/AC6/AC3: distillation canonicalization honors + strips ──────────────
class TestCanonicalizeHonorsAndStrips:
    def test_declared_type_honored_and_stripped_from_body(self):
        # A lesson whose PROSE screams "pitfall" (bug/wrong) but declared [principle].
        # Honor path must route it as principle AND the body must NOT carry [principle].
        enriched = "- 2026-08-15: [principle] The bug was wrong but the rule is sound"
        out = DistillationTriggerHook._canonicalize_entry(enriched, "Principles")
        assert out.startswith("- [principle]"), out
        # AC3: no double/triple tag — the body after the title must not repeat [principle]
        assert out.count("[principle]") == 1, out

    def test_process_reflection_correction_tag(self):
        enriched = "- 2026-08-15: [correction] Same file edited 4 rounds — upfront thinking would help"
        out = DistillationTriggerHook._canonicalize_entry(enriched, "Corrections")
        assert out.startswith("- [correction]"), out
        assert out.count("[correction]") == 1, out

    def test_untagged_lesson_falls_back_no_crash(self):
        # Legacy / undeclared: no [type] → keyword classify, no crash, single tag.
        enriched = "- 2026-08-15: Never trust char-count estimation for tokens"
        out = DistillationTriggerHook._canonicalize_entry(enriched, "Guidelines")
        assert out.startswith("- ["), out
        # exactly one bracket-tag at the entry head
        assert out.count("**") >= 2  # has a title

    def test_non_type_bracket_lead_not_stripped(self):
        # A legitimate [TODO]-style lead is NOT in VALID_TYPES → must survive in body.
        enriched = "- 2026-08-15: [TODO] wire the retry path"
        out = DistillationTriggerHook._canonicalize_entry(enriched, "Guidelines")
        assert "TODO" in out, out
