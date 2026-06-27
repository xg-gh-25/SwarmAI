"""Tests for distillation WRITE-TARGET correctness (R3 write-governance).

Regression guard for the write-target drift bug: distillation wrote decisions
and lessons to section names ("Key Decisions" / "Lessons Learned") that no
longer exist after the PRI01 section rename (current sections: Decisions /
Guidelines / Pitfalls / ...). _modify_content falls back to a "## Distilled"
orphan section when the target is missing, so every distillation cycle was
misfiling knowledge into index-invisible, recall-invisible orphan sections.

These tests assert the write targets are CURRENT section names and that the
section-map / cap-key constants derive from the MEMORY_SECTIONS SSoT so the
drift cannot silently recur.
"""
from __future__ import annotations


class TestDistillWriteTargets:
    """Distillation must write to sections that actually exist in MEMORY.md."""

    def test_decision_lands_in_decisions_not_distilled(self):
        """A distilled decision must land in '## Decisions', not the '## Distilled' fallback."""
        from scripts.locked_write import _modify_content

        # MEMORY.md as it really is post-PRI01: has Decisions + Guidelines, no
        # "Key Decisions" / "Lessons Learned".
        content = "# Memory\n\n## Decisions\n- existing decision\n\n## Guidelines\n- existing guideline\n"

        result = _modify_content(content, "Decisions", "- 2026-06-28: **New** — a decision", "prepend")

        assert "## Distilled" not in result, "decision misfiled into orphan ## Distilled section"
        decisions_body = result.split("## Decisions")[1].split("##")[0]
        assert "a decision" in decisions_body, "decision did not land in ## Decisions"

    def test_lesson_lands_in_guidelines_not_distilled(self):
        """A distilled lesson must land in '## Guidelines', not the '## Distilled' fallback."""
        from scripts.locked_write import _modify_content

        content = "# Memory\n\n## Decisions\n- existing\n\n## Guidelines\n- existing\n"

        result = _modify_content(content, "Guidelines", "- 2026-06-28: **Lesson** — a lesson", "prepend")

        assert "## Distilled" not in result
        guidelines_body = result.split("## Guidelines")[1].split("##")[0]
        assert "a lesson" in guidelines_body

    def test_dead_section_names_would_misfile(self):
        """Negative control: the OLD targets DO fall through to ## Distilled.

        This locks in WHY the fix matters — if someone reverts the write target
        to a legacy name, _modify_content silently orphans the entry. The fix is
        to never pass a non-existent section name.
        """
        from scripts.locked_write import _modify_content

        content = "# Memory\n\n## Decisions\n- existing\n"
        result = _modify_content(content, "Key Decisions", "- orphaned", "prepend")
        assert "## Distilled" in result, "expected legacy section name to trigger orphan fallback"


class TestWriteTargetsDeriveFromSSoT:
    """The write-target constants must derive from / agree with the MEMORY_SECTIONS SSoT."""

    def test_section_caps_keys_are_valid_sections(self):
        """SECTION_CAPS keys must all be real current section names (no dead keys)."""
        from hooks.distillation_hook import SECTION_CAPS
        from core.ddd_entry_lifecycle import MEMORY_SECTION_NAMES

        invalid = [k for k in SECTION_CAPS if k not in MEMORY_SECTION_NAMES]
        assert not invalid, f"SECTION_CAPS has dead keys not in MEMORY_SECTIONS SSoT: {invalid}"

    def test_churning_sections_are_capped(self):
        """The high-churn non-evergreen sections (the ones that bloat) must have caps."""
        from hooks.distillation_hook import SECTION_CAPS

        # These are the membership-churn sections that were uncapped due to the drift.
        for must_cap in ("Decisions", "Guidelines", "Pitfalls"):
            assert must_cap in SECTION_CAPS, f"{must_cap} must have a cap (was uncapped due to drift)"

    def test_extractor_section_map_targets_valid_sections(self):
        """memory_extractor's section map must target current sections, not dead ones."""
        from core.memory_extractor import _SECTION_MAP
        from core.ddd_entry_lifecycle import MEMORY_SECTION_NAMES

        invalid = [v for v in _SECTION_MAP.values() if v not in MEMORY_SECTION_NAMES]
        assert not invalid, f"_SECTION_MAP targets dead sections: {invalid}"

    def test_permanent_sections_are_valid(self):
        """memory_decay.PERMANENT_SECTIONS must reference real current sections."""
        from core.memory_decay import PERMANENT_SECTIONS
        from core.ddd_entry_lifecycle import MEMORY_SECTION_NAMES

        invalid = [s for s in PERMANENT_SECTIONS if s not in MEMORY_SECTION_NAMES]
        assert not invalid, f"PERMANENT_SECTIONS references dead sections: {invalid}"
