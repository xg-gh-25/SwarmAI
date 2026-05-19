"""Tests for ddd_entry_lifecycle — per-entry knowledge tracking + decay.

Tests cover: parsing, type classification, reference bumping, decay assessment,
metadata injection, and archival. Uses real IMPROVEMENT.md format.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch

from core.ddd_entry_lifecycle import (
    EntryMetadata,
    classify_entry_type,
    parse_entries,
    inject_entry_metadata,
    bump_references,
    assess_decay,
    DecayTransition,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_CONTENT = """\
## What Worked
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 45 | trust: high | promoted: 2026-05-01 -->

- [guideline] **Adversarial review caught dead code** — builder's own review found 0 findings. Sub-agent found 14. (2026-05-16, run_0bbba678)
  <!-- ref:3 | last:2026-05-18 | decay:active -->

- [pitfall] **5 rounds of review = process failure** — not thoroughness. Root cause: never ran full suite. (2026-04-28, run_91a6fb7e)
  <!-- ref:7 | last:2026-05-14 | decay:active -->

- [decision] **Copy-then-own > fork-and-depend** — copied 73 files from video-podcast-maker. (2026-04-26, run_d72c69f9)
  <!-- ref:1 | last:2026-04-26 | decay:active -->

- [guideline] **Old entry no refs** — something old with no references ever. (2025-11-01, run_old)
  <!-- ref:0 | last:none | decay:active -->

## What Failed
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [pitfall] **dry_run=True that never flips** — feature permanently disabled. (2026-05-03, run_a27acb60)
  <!-- ref:2 | last:2026-05-10 | decay:active -->
"""

SAMPLE_NO_METADATA = """\
## What Worked
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- **Adversarial review caught dead code** — builder's own review found 0 findings. (2026-05-16, run_0bbba678)

- **5 rounds of review = process failure** — Root cause: never ran full suite. (2026-04-28, run_91a6fb7e)
"""


# ── AC2: parse_entries extracts metadata correctly ───────────────────────────

class TestParseEntries:
    def test_parses_all_entries(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert len(entries) == 5

    def test_extracts_type(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].entry_type == "guideline"
        assert entries[1].entry_type == "pitfall"
        assert entries[2].entry_type == "decision"

    def test_extracts_title(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].title == "Adversarial review caught dead code"
        assert entries[1].title == "5 rounds of review = process failure"

    def test_extracts_ref_count(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].ref_count == 3
        assert entries[1].ref_count == 7
        assert entries[3].ref_count == 0

    def test_extracts_last_referenced(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].last_referenced == date(2026, 5, 18)
        assert entries[3].last_referenced is None  # "none"

    def test_extracts_decay_state(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].decay_state == "active"

    def test_extracts_created_date(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].created_date == date(2026, 5, 16)
        assert entries[2].created_date == date(2026, 4, 26)

    def test_entries_without_metadata_get_defaults(self):
        entries = parse_entries(SAMPLE_NO_METADATA)
        assert len(entries) == 2
        assert entries[0].ref_count == 0
        assert entries[0].last_referenced is None
        assert entries[0].decay_state == "active"
        assert entries[0].title == "Adversarial review caught dead code"

    def test_entries_without_type_default_guideline(self):
        entries = parse_entries(SAMPLE_NO_METADATA)
        # No [type] prefix → classified by heuristic or default
        assert entries[0].entry_type in ("guideline", "pitfall", "decision")

    def test_empty_content_returns_empty(self):
        assert parse_entries("") == []
        assert parse_entries("# No sections here") == []


# ── AC6: classify_entry_type returns correct type ────────────────────────────

class TestClassifyEntryType:
    def test_guideline_signals(self):
        assert classify_entry_type("Pattern: always use atomic commits") == "guideline"
        assert classify_entry_type("Rule: never skip adversarial review") == "guideline"

    def test_pitfall_signals(self):
        assert classify_entry_type("The bug was a race condition in async path") == "pitfall"
        assert classify_entry_type("This broke CI because of failed import") == "pitfall"

    def test_decision_signals(self):
        assert classify_entry_type("We chose httpx over requests because of async") == "decision"
        assert classify_entry_type("Selected event-driven approach vs polling") == "decision"

    def test_ambiguous_defaults_to_guideline(self):
        assert classify_entry_type("Something happened in the code") == "guideline"


# ── AC3: inject_entry_metadata round-trip ────────────────────────────────────

class TestInjectMetadata:
    def test_roundtrip_preserves_entries(self):
        entries = parse_entries(SAMPLE_CONTENT)
        result = inject_entry_metadata(SAMPLE_CONTENT, entries)
        re_parsed = parse_entries(result)
        assert len(re_parsed) == len(entries)
        for orig, reparsed in zip(entries, re_parsed):
            assert orig.title == reparsed.title
            assert orig.ref_count == reparsed.ref_count
            assert orig.decay_state == reparsed.decay_state

    def test_inject_updates_ref_count(self):
        entries = parse_entries(SAMPLE_CONTENT)
        entries[0].ref_count = 10
        result = inject_entry_metadata(SAMPLE_CONTENT, entries)
        re_parsed = parse_entries(result)
        assert re_parsed[0].ref_count == 10

    def test_inject_adds_metadata_to_entries_without(self):
        entries = parse_entries(SAMPLE_NO_METADATA)
        entries[0].ref_count = 1
        entries[0].last_referenced = date(2026, 5, 19)
        result = inject_entry_metadata(SAMPLE_NO_METADATA, entries)
        re_parsed = parse_entries(result)
        assert re_parsed[0].ref_count == 1


# ── AC4: bump_references increments ref count ────────────────────────────────

class TestBumpReferences:
    def test_bumps_matching_title(self):
        entries = parse_entries(SAMPLE_CONTENT)
        text = "We applied the Adversarial review caught dead code pattern here."
        today = date(2026, 5, 19)
        bumped = bump_references(entries, text, today)
        assert bumped == 1
        assert entries[0].ref_count == 4  # was 3
        assert entries[0].last_referenced == today

    def test_no_match_no_bump(self):
        entries = parse_entries(SAMPLE_CONTENT)
        text = "Something completely unrelated to any entry."
        bumped = bump_references(entries, text, date(2026, 5, 19))
        assert bumped == 0

    def test_multiple_matches(self):
        entries = parse_entries(SAMPLE_CONTENT)
        text = "Both Adversarial review caught dead code and Copy-then-own > fork-and-depend apply."
        bumped = bump_references(entries, text, date(2026, 5, 19))
        assert bumped == 2

    def test_short_titles_are_skipped(self):
        """Titles < 15 chars should not match to prevent false positives."""
        entries = parse_entries(SAMPLE_CONTENT)
        # Override one entry to have a short title
        entries[0].title = "Build"  # 5 chars — too short
        text = "We need to Build the entire system from scratch."
        bumped = bump_references(entries, text, date(2026, 5, 19))
        # "Build" should NOT match (too short)
        assert entries[0].ref_count == 3  # unchanged from original


# ── AC5: assess_decay transitions correctly ──────────────────────────────────

class TestAssessDecay:
    def test_active_to_dormant_at_90d(self):
        entries = parse_entries(SAMPLE_CONTENT)
        # Entry[3] has ref:0, last:none, created 2025-11-01
        # At 2026-05-19, that's > 180 days
        today = date(2026, 5, 19)
        transitions = assess_decay(entries, today)
        # Entry with no refs and old → should transition
        old_entry_transitions = [t for t in transitions if t.entry.title == "Old entry no refs"]
        assert len(old_entry_transitions) >= 1
        assert old_entry_transitions[0].new_state in ("dormant", "archived")

    def test_recently_referenced_stays_active(self):
        entries = parse_entries(SAMPLE_CONTENT)
        today = date(2026, 5, 19)
        transitions = assess_decay(entries, today)
        # Entry[0] was referenced 2026-05-18 (1 day ago) → stays active
        adversarial_transitions = [t for t in transitions if "Adversarial" in t.entry.title]
        assert len(adversarial_transitions) == 0  # No transition

    def test_grace_period_for_new_entries(self):
        """AC9: entries created < 30 days ago are immune."""
        entries = parse_entries(SAMPLE_CONTENT)
        # Override entry[0] to be brand new with no refs
        entries[0].created_date = date(2026, 5, 10)  # 9 days old
        entries[0].ref_count = 0
        entries[0].last_referenced = None
        today = date(2026, 5, 19)
        transitions = assess_decay(entries, today)
        new_entry_transitions = [t for t in transitions if "Adversarial" in t.entry.title]
        assert len(new_entry_transitions) == 0  # Grace period

    def test_high_ref_gets_extended_grace(self):
        """AC10: ref >= 10 gets 180d before dormant (2x normal)."""
        entries = parse_entries(SAMPLE_CONTENT)
        # Entry[1] has ref:7, set to ref:10 and last_referenced 100 days ago
        entries[1].ref_count = 10
        entries[1].last_referenced = date(2026, 2, 8)  # 100 days before 2026-05-19
        today = date(2026, 5, 19)
        transitions = assess_decay(entries, today)
        # 100 days < 180 day grace → should NOT transition
        high_ref_transitions = [t for t in transitions if "5 rounds" in t.entry.title]
        assert len(high_ref_transitions) == 0


# ── AC7: evaluate_demotion ───────────────────────────────────────────────────

class TestEvaluateDemotion:
    """Tests for section-level demotion (in ddd_maturity.py)."""

    def test_mature_demotes_on_low_health_and_time(self):
        from core.ddd_maturity import MaturityState, evaluate_demotion
        state = MaturityState(level="mature", source_count=3,
                             verified_by_production=True, used_in_decision=True,
                             days_at_level=200)
        result = evaluate_demotion(state, health_score=35)
        assert result == "growing"

    def test_mature_no_demotion_healthy(self):
        from core.ddd_maturity import MaturityState, evaluate_demotion
        state = MaturityState(level="mature", source_count=3,
                             verified_by_production=True, used_in_decision=True,
                             days_at_level=200)
        result = evaluate_demotion(state, health_score=60)
        assert result is None

    def test_growing_demotes_to_sparse(self):
        from core.ddd_maturity import MaturityState, evaluate_demotion
        state = MaturityState(level="growing", source_count=2,
                             verified_by_production=True, days_at_level=100)
        result = evaluate_demotion(state, health_score=25)
        assert result == "sparse"

    def test_evergreen_never_demotes(self):
        from core.ddd_maturity import MaturityState, evaluate_demotion
        state = MaturityState(level="evergreen", source_count=5,
                             verified_by_production=True, days_at_level=365)
        result = evaluate_demotion(state, health_score=10)
        assert result is None

    def test_sparse_cannot_demote(self):
        from core.ddd_maturity import MaturityState, evaluate_demotion
        state = MaturityState(level="sparse", source_count=0,
                             days_at_level=999)
        result = evaluate_demotion(state, health_score=5)
        assert result is None


# ── AC1/AC2: archive_entries moves dormant entries to archive file ────────────

class TestArchiveEntries:
    def test_archives_dormant_entries(self, tmp_path):
        from core.ddd_entry_lifecycle import archive_entries

        # Create a source file with one dormant entry
        src = tmp_path / "IMPROVEMENT.md"
        src.write_text("""\
## What Worked
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Good entry still active** — stays. (2026-05-01, run_x)
  <!-- ref:5 | last:2026-05-18 | decay:active -->

- [pitfall] **Old dormant entry** — should be archived. (2025-01-01, run_old)
  <!-- ref:0 | last:none | decay:dormant -->
""")

        entries = parse_entries(src.read_text())
        # Mark the dormant one for archival
        dormant_entries = [e for e in entries if e.decay_state == "dormant"]
        assert len(dormant_entries) == 1

        archived_count = archive_entries(tmp_path, dormant_entries)
        assert archived_count == 1

        # Archive file should exist with the entry
        archive_path = tmp_path / "IMPROVEMENT-archive.md"
        assert archive_path.exists()
        archive_content = archive_path.read_text()
        assert "Old dormant entry" in archive_content
        assert "<!-- ref:0" in archive_content

    def test_archive_appends_to_existing_archive(self, tmp_path):
        from core.ddd_entry_lifecycle import archive_entries, EntryMetadata

        # Create existing archive
        archive_path = tmp_path / "IMPROVEMENT-archive.md"
        archive_path.write_text("# Archived Knowledge Entries\n\n- [guideline] **Previous** — old. (2024-01-01)\n  <!-- ref:0 | last:none | decay:archived -->\n")

        entry = EntryMetadata(
            title="New archived entry",
            entry_type="pitfall",
            ref_count=0,
            decay_state="dormant",
            raw_text="- [pitfall] **New archived entry** — something. (2025-06-01, run_y)",
            created_date=date(2025, 6, 1),
        )
        archived_count = archive_entries(tmp_path, [entry])
        assert archived_count == 1

        content = archive_path.read_text()
        assert "Previous" in content  # Existing preserved
        assert "New archived entry" in content  # New appended


# ── AC3/AC4/AC5: get_stage_knowledge ─────────────────────────────────────────

class TestGetStageKnowledge:
    def test_returns_filtered_by_type_and_stage(self):
        from core.ddd_entry_lifecycle import get_stage_knowledge

        entries = parse_entries(SAMPLE_CONTENT)
        # BUILD stage should get guideline + pitfall entries
        result = get_stage_knowledge(entries, "build")
        types_returned = {e.entry_type for e in result}
        # BUILD affinity: guideline(7) + pitfall(5) + decision(2)
        assert "guideline" in types_returned or "pitfall" in types_returned

    def test_respects_max_per_type(self):
        from core.ddd_entry_lifecycle import get_stage_knowledge

        entries = parse_entries(SAMPLE_CONTENT)
        result = get_stage_knowledge(entries, "build")
        # Should not exceed total budget (guideline:7 + pitfall:5 + decision:2 = 14 max)
        assert len(result) <= 14

    def test_excludes_dormant_entries(self):
        from core.ddd_entry_lifecycle import get_stage_knowledge

        entries = parse_entries(SAMPLE_CONTENT)
        # Mark one as dormant
        entries[0].decay_state = "dormant"
        result = get_stage_knowledge(entries, "build")
        dormant_in_result = [e for e in result if e.decay_state == "dormant"]
        assert len(dormant_in_result) == 0

    def test_sorts_by_ref_count_descending(self):
        from core.ddd_entry_lifecycle import get_stage_knowledge

        entries = parse_entries(SAMPLE_CONTENT)
        result = get_stage_knowledge(entries, "review")
        if len(result) >= 2:
            # Higher ref_count should come first
            for i in range(len(result) - 1):
                assert result[i].ref_count >= result[i + 1].ref_count

    def test_unknown_stage_returns_empty(self):
        from core.ddd_entry_lifecycle import get_stage_knowledge

        entries = parse_entries(SAMPLE_CONTENT)
        result = get_stage_knowledge(entries, "nonexistent_stage")
        assert result == []
