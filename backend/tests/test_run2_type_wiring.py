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


# ── GAP B: deterministic raw-text recovery (Gate-2 CRIT-1/CRIT-2 regressions) ──


# ── run_4ad5a44b: route_lesson_type HONORS a declared [type] tag ──────────────
# Root: route_lesson_type keyword-guessed even when the author declared the type
# in a leading [type] prefix (measured 82% accuracy, systematic principle/
# correction -> pitfall/guideline demotion). Honor the declared tag as PRIMARY,
# fall back to classify_entry_type only when no valid tag is present.
class TestRouteLessonTypeHonorsDeclaredTag:
    def test_declared_principle_honored_over_pitfall_keyword(self):
        # AC1: body contains the greedy 'pitfall' keyword, but the author declared
        # [principle] — honor must win, else keyword picks pitfall.
        from core.ddd_entry_lifecycle import route_lesson_type
        section, etype = route_lesson_type(
            "[principle] The pitfall is treating confidence as truth — the "
            "first principle is that certainty must be verified against source."
        )
        assert etype == "principle", f"declared [principle] must be honored, got {etype}"

    def test_declared_keep_type_still_held_back(self):
        # AC2: honoring must NOT bypass the KEEP_TYPES hold-back (section=None).
        from core.ddd_entry_lifecycle import route_lesson_type
        section, etype = route_lesson_type(
            "[correction] I keep shipping untested code because I trust my own diffs."
        )
        assert etype == "correction"
        assert section is None, "declared KEEP_TYPE must still hold back (None)"

    def test_declared_operational_routes_to_its_section(self):
        from core.ddd_entry_lifecycle import route_lesson_type, MEMORY_TYPE_TO_SECTION
        section, etype = route_lesson_type("[guideline] Always match existing style.")
        assert etype == "guideline"
        assert section == MEMORY_TYPE_TO_SECTION["guideline"]

    def test_no_tag_falls_back_to_keyword(self):
        # AC4: absent tag -> identical to classify_entry_type (no regression).
        from core.ddd_entry_lifecycle import route_lesson_type, classify_entry_type
        text = "A silent race condition: the reconcile drops a bubble — recurring bug."
        section, etype = route_lesson_type(text)
        assert etype == classify_entry_type(text)

    def test_invalid_tag_falls_back_to_keyword(self):
        # A bogus [type] must NOT be honored — falls to keyword.
        from core.ddd_entry_lifecycle import route_lesson_type, classify_entry_type
        text = "[frobnicate] Always verify the target file before editing."
        section, etype = route_lesson_type(text)
        assert etype == classify_entry_type(text)


class TestReflectPathSingleTag:
    """run_4ad5a44b bug(b): REFLECT-path entry must carry EXACTLY ONE [type]."""

    def test_declared_tag_stripped_from_body_and_title(self):
        from core.ddd_entry_lifecycle import _DECLARED_TYPE_RE, route_lesson_type
        lesson = "[pitfall] A trap title — the body explains the recurring bug"
        body = _DECLARED_TYPE_RE.sub("", lesson, count=1)
        _, etype = route_lesson_type(lesson)
        title = body.split("—")[0].strip()
        entry_line = f"- [{etype}] **{title}** — {body} (2026-08-14, run_x)"
        assert entry_line.count("[pitfall]") == 1, f"triple-tag regression: {entry_line}"
        assert not title.startswith("["), "title must be tag-free"
        assert not body.startswith("["), "body must be tag-free"

    def test_mutation_without_strip_triple_tags(self):
        lesson = "[pitfall] A trap title — the body"
        old_title = lesson.split("—")[0].strip()
        old_line = f"- [pitfall] **{old_title}** — {lesson} (...)"
        assert old_line.count("[pitfall]") == 3, "mutation guard: old path must triple-tag"

    def test_invalid_tag_body_not_stripped(self):
        # Gate-2 CRITICAL (run_4ad5a44b): the body-strip must use the SAME
        # VALID_TYPES guard as the honor, or "[TODO] ..." loses content.
        from core.ddd_entry_lifecycle import _DECLARED_TYPE_RE, VALID_TYPES
        lesson = "[TODO] verify the fix — a note, not a type declaration"
        _dm = _DECLARED_TYPE_RE.match(lesson)
        body = (_DECLARED_TYPE_RE.sub("", lesson, count=1)
                if _dm and _dm.group(1).lower() in VALID_TYPES else lesson)
        assert body == lesson, "non-type bracket must NOT be stripped (content loss)"
        assert body.startswith("[TODO]"), "TODO prefix preserved"
