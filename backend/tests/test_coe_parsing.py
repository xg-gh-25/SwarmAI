"""Tests for COE entry parsing in distillation_hook.

Verifies that _extract_coe_entries handles well-formed and malformed
COE lines gracefully — the regex-based parser should never raise
ValueError on missing backticks or unexpected delimiters.
"""
from __future__ import annotations


class TestExtractCoeEntries:
    """Test _extract_coe_entries regex-based parsing."""

    def test_well_formed_coe_line(self):
        """Standard format: **COE:** `signal` — topic."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `resolution` — streaming not working after tab switch"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 1
        assert entries[0] == ("resolution", "streaming not working after tab switch")

    def test_candidate_signal(self):
        """Candidate signal type is also extracted."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `candidate` — memory leak in session pool"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 1
        assert entries[0][0] == "candidate"

    def test_malformed_no_backticks(self):
        """Missing backticks should not raise ValueError — just skip the line."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** resolution — streaming not working"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 0  # Gracefully skipped, no crash

    def test_malformed_single_backtick(self):
        """Single backtick (unclosed) should not raise ValueError."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `resolution — streaming not working"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 0  # Gracefully skipped

    def test_malformed_wrong_delimiter(self):
        """Wrong delimiter (: instead of —) should not crash."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `resolution`: streaming not working"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 0  # Delimiter doesn't match

    def test_unknown_signal_type(self):
        """Unknown signal types (not candidate/resolution) are skipped."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `info` — just an observation"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 0

    def test_multiple_coe_lines(self):
        """Multiple COE lines in one body are all extracted."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = (
            "Some text\n"
            "**COE:** `candidate` — tab crash on restore\n"
            "More text\n"
            "**COE:** `resolution` — tab crash on restore\n"
        )
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 2
        assert entries[0][0] == "candidate"
        assert entries[1][0] == "resolution"

    def test_empty_topic(self):
        """Empty topic after delimiter is skipped."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `resolution` —  "
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 0

    def test_hyphen_delimiter(self):
        """Hyphen delimiter (- instead of —) also works."""
        from hooks.distillation_hook import DistillationTriggerHook

        body = "**COE:** `candidate` - memory leak in pool"
        entries = DistillationTriggerHook._extract_coe_entries(body)
        assert len(entries) == 1
        assert entries[0][1] == "memory leak in pool"
