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




class TestWriteTargetsDeriveFromSSoT:
    """The write-target constants must derive from / agree with the MEMORY_SECTIONS SSoT."""



    def test_extractor_has_no_destination_map(self):
        """memory_extractor emits candidates; CDF is the only routing authority."""
        import core.memory_extractor as extractor

        assert not hasattr(extractor, "_SECTION_MAP")

    def test_permanent_sections_are_valid(self):
        """memory_decay.PERMANENT_SECTIONS must reference real current sections."""
        from core.memory_decay import PERMANENT_SECTIONS
        from core.ddd_entry_lifecycle import MEMORY_SECTION_NAMES

        invalid = [s for s in PERMANENT_SECTIONS if s not in MEMORY_SECTION_NAMES]
        assert not invalid, f"PERMANENT_SECTIONS references dead sections: {invalid}"
