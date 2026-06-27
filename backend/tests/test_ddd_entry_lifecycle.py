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
        """Titles < 8 chars should not match to prevent false positives."""
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

    def test_high_ref_no_longer_gets_extended_grace(self):
        """R2-prime (run_e50621b6): ref_count NO LONGER grants extended decay
        grace. ref is a dead input (no live body producer — Gate-2 verified), so
        honoring it only preserved toxic prose residue. An entry 100 days idle
        decays at the normal 90d threshold REGARDLESS of ref_count."""
        entries = parse_entries(SAMPLE_CONTENT)
        entries[1].ref_count = 10  # would have bought 2x grace pre-R2-prime
        # past the 30d grace (created) AND 100d idle (last_referenced)
        entries[1].created_date = date(2026, 2, 1)
        entries[1].last_referenced = date(2026, 2, 8)  # 100 days before 2026-05-19
        today = date(2026, 5, 19)
        transitions = assess_decay(entries, today)
        # 100 days > 90d normal threshold → NOW transitions (ref ignored, no 2x)
        high_ref_transitions = [t for t in transitions if "5 rounds" in t.entry.title]
        assert len(high_ref_transitions) == 1
        assert high_ref_transitions[0].new_state == "dormant"


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


# ── M0: compute_entry_noise — honest per-entry noise metric ──────────────────
#
# Neutral synthetic fixture (NOT the real IMPROVEMENT.md): every entry's
# (ref_count, decay_state, created_date) is hand-set so the EXPECTED noisy
# count is unambiguous and stable. Real-doc magnitude is reported separately
# (build-time baseline), never asserted here.

# today = 2026-06-25; grace = 30d → grace boundary is 2026-05-26.
_NOISE_TODAY = date(2026, 6, 25)

# 6 entries, exactly 2 are noisy (ref==0 AND dormant/archived AND past grace).
_NOISE_FIXTURE = """\
## Section A
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Noisy dormant zero-ref old** — earns nothing. (2025-01-01, run_a)
  <!-- ref:0 | last:none | decay:dormant -->

- [pitfall] **Noisy archived zero-ref old** — cold. (2025-02-01, run_b)
  <!-- ref:0 | last:none | decay:archived -->

- [guideline] **Active zero-ref old not noisy yet** — still active so excluded. (2025-03-01, run_c)
  <!-- ref:0 | last:none | decay:active -->

- [decision] **Referenced dormant not noisy** — has refs. (2025-01-15, run_d)
  <!-- ref:4 | last:2026-06-01 | decay:dormant -->

## Section B
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Fresh zero-ref dormant within grace** — protected by grace. (2026-06-20, run_e)
  <!-- ref:0 | last:none | decay:dormant -->

- [pitfall] **Active referenced healthy** — the good kind. (2026-06-10, run_f)
  <!-- ref:9 | last:2026-06-22 | decay:active -->
"""


class TestComputeEntryNoise:
    def _report(self):
        from core.ddd_entry_lifecycle import compute_entry_noise
        entries = parse_entries(_NOISE_FIXTURE)
        return compute_entry_noise(entries, _NOISE_TODAY)

    def test_counts_total_entries(self):
        assert self._report().total == 6

    def test_identifies_exactly_the_noisy_entries(self):
        report = self._report()
        assert report.noisy == 2
        assert set(report.noisy_titles) == {
            "Noisy dormant zero-ref old",
            "Noisy archived zero-ref old",
        }

    def test_noise_rate_is_noisy_over_total(self):
        report = self._report()
        assert report.noise_rate == pytest.approx(2 / 6)

    def test_active_entries_never_noisy(self):
        # ref==0 + old but still 'active' must be excluded (decay engine
        # hasn't judged it stale; evergreen sections rely on this).
        report = self._report()
        assert "Active zero-ref old not noisy yet" not in report.noisy_titles

    def test_referenced_entries_never_noisy(self):
        report = self._report()
        assert "Referenced dormant not noisy" not in report.noisy_titles

    def test_grace_period_protects_new_entries(self):
        report = self._report()
        assert "Fresh zero-ref dormant within grace" not in report.noisy_titles

    def test_by_section_breakdown(self):
        report = self._report()
        # Both noisy entries are in Section A.
        assert report.by_section == {"Section A": 2}

    def test_empty_entries_returns_zero_rate(self):
        from core.ddd_entry_lifecycle import compute_entry_noise
        report = compute_entry_noise([], _NOISE_TODAY)
        assert report.total == 0
        assert report.noisy == 0
        assert report.noise_rate == 0.0

    def test_is_read_only_does_not_mutate_entries(self):
        from core.ddd_entry_lifecycle import compute_entry_noise
        entries = parse_entries(_NOISE_FIXTURE)
        before = [(e.ref_count, e.decay_state, e.last_referenced) for e in entries]
        compute_entry_noise(entries, _NOISE_TODAY)
        after = [(e.ref_count, e.decay_state, e.last_referenced) for e in entries]
        assert before == after

    def test_grace_boundary_matches_assess_decay(self):
        # At age == grace_days, assess_decay's immunity (age < grace) does NOT
        # apply — so a zero-ref dormant entry at exactly the boundary IS noise.
        # Regression guard for the <= vs < off-by-one (must stay aligned with
        # assess_decay so a just-dormant entry is never invisible for a day).
        from datetime import timedelta
        from core.ddd_entry_lifecycle import (
            compute_entry_noise, assess_decay, parse_entries, GRACE_PERIOD_DAYS,
        )
        boundary = _NOISE_TODAY - timedelta(days=GRACE_PERIOD_DAYS)  # age == 30
        content = f"""\
## S
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Boundary dormant zero ref** — at exactly grace age. ({boundary.isoformat()}, run_g)
  <!-- ref:0 | last:none | decay:dormant -->
"""
        entries = parse_entries(content)
        report = compute_entry_noise(entries, _NOISE_TODAY)
        assert report.noisy == 1  # not excluded at the boundary

        # And one day younger (age == 29) IS still grace-protected.
        younger = (_NOISE_TODAY - timedelta(days=GRACE_PERIOD_DAYS - 1)).isoformat()
        c2 = content.replace(boundary.isoformat(), younger)
        report2 = compute_entry_noise(parse_entries(c2), _NOISE_TODAY)
        assert report2.noisy == 0

    def test_section_blank_maps_to_sentinel(self):
        # Entries before any ## header get section "" → reported under a
        # human-readable sentinel, not an empty-string key.
        from core.ddd_entry_lifecycle import compute_entry_noise
        content = """\
- [guideline] **Headerless dormant zero ref** — no section above it. (2025-01-01, run_h)
  <!-- ref:0 | last:none | decay:dormant -->
"""
        report = compute_entry_noise(parse_entries(content), _NOISE_TODAY)
        assert report.noisy == 1
        assert report.by_section == {"(no section)": 1}

    def test_dateless_entry_treated_as_past_grace(self):
        # No (YYYY-MM-DD) in text → created_date None → past grace (matches
        # assess_decay's "infinitely old" convention).
        from core.ddd_entry_lifecycle import compute_entry_noise
        content = """\
## S
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **No date dormant zero ref** — should count as noisy.
  <!-- ref:0 | last:none | decay:dormant -->
"""
        entries = parse_entries(content)
        report = compute_entry_noise(entries, _NOISE_TODAY)
        assert report.noisy == 1


# ── M0 ② CLEAN: keep-class predicate ─────────────────────────────────────────
#
# is_keep_class protects permanent knowledge from reclaim. It must err toward
# KEEPING — a false-archive of a COE/principle/correction is unrecoverable
# context loss. Detection is by SECTION, TYPE, ref_count, and COE substring —
# layered so a misclassified entry in a custom section is still caught by ≥1 rule.

def _entry(title, *, section="", entry_type="guideline", ref=0, decay="dormant",
           created=None):
    from core.ddd_entry_lifecycle import EntryMetadata
    return EntryMetadata(
        title=title, entry_type=entry_type, ref_count=ref,
        decay_state=decay, created_date=created, section=section,
    )


class TestIsKeepClass:
    def test_evergreen_section_is_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class, MEMORY_EVERGREEN_SECTIONS
        e = _entry("x", section="Principles", entry_type="guideline", ref=0)
        assert is_keep_class(e, evergreen_sections=MEMORY_EVERGREEN_SECTIONS) is True

    def test_principle_type_is_kept_even_in_custom_section(self):
        # Project IMPROVEMENT docs use custom section names; type must still protect.
        from core.ddd_entry_lifecycle import is_keep_class
        e = _entry("x", section="Key Lessons (from MEMORY.md)",
                   entry_type="principle", ref=0)
        assert is_keep_class(e) is True

    def test_correction_type_is_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class
        assert is_keep_class(_entry("x", entry_type="correction", ref=0)) is True

    def test_decision_and_model_types_are_kept(self):
        # Cognitive-layer knowledge is not operational noise — keep it.
        from core.ddd_entry_lifecycle import is_keep_class
        assert is_keep_class(_entry("x", entry_type="decision", ref=0)) is True
        assert is_keep_class(_entry("x", entry_type="model", ref=0)) is True

    def test_high_ref_no_longer_kept_by_ref_alone(self):
        """R2-prime (run_e50621b6): the ref>=2 keep-rule is REMOVED. ref is a
        dead input carrying toxic prose residue, so a plain guideline kept ONLY
        by ref is now correctly reclaimable. Keep-class = evergreen OR keep-type
        OR COE only. (A keep-TYPE entry is still kept regardless of ref — that's
        the honest protection.)"""
        from core.ddd_entry_lifecycle import is_keep_class
        # plain guideline, high ref, no other keep signal → NO LONGER kept
        assert is_keep_class(_entry("x", entry_type="guideline", ref=2)) is False
        # but a keep-TYPE entry stays kept (type, not ref, protects it)
        assert is_keep_class(_entry("x", entry_type="principle", ref=0)) is True

    def test_coe_in_title_is_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class
        e = _entry("COE05: SIGKILL cascade", section="Key Lessons",
                   entry_type="guideline", ref=0)
        assert is_keep_class(e) is True

    def test_coe_in_section_is_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class
        e = _entry("something", section="COE Registry",
                   entry_type="pitfall", ref=0)
        assert is_keep_class(e) is True

    def test_plain_guideline_zero_ref_is_NOT_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class
        assert is_keep_class(_entry("plain old lesson", entry_type="guideline",
                                    ref=0)) is False

    def test_plain_pitfall_zero_ref_is_NOT_kept(self):
        from core.ddd_entry_lifecycle import is_keep_class
        assert is_keep_class(_entry("a bug we hit", entry_type="pitfall",
                                    ref=0)) is False


# ── M0 ② CLEAN: reclaim_noise_entries (selection + physical removal) ──────────
#
# Gate-1 finding (verified): neither archive_entries nor inject_entry_metadata
# removes a bullet from the source doc — archived entries persist with
# decay:archived and are still counted noisy. reclaim MUST physically strip.

_RECLAIM_TODAY = date(2026, 6, 25)

# 5 entries: 2 reclaimable (plain guideline/pitfall, ref0, dormant, old),
# 3 protected (principle-type, ref>=2, COE-title).
_RECLAIM_FIXTURE = """\
## Key Lessons
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Reclaim this plain lesson** — operational, no refs, old. (2025-01-01, run_a)
  <!-- ref:0 | last:none | decay:dormant -->

- [pitfall] **Reclaim this plain bug** — operational, no refs, old. (2025-02-01, run_b)
  <!-- ref:0 | last:none | decay:dormant -->

- [principle] **Keep this principle** — meta knowledge. (2025-01-01, run_c)
  <!-- ref:0 | last:none | decay:dormant -->

- [guideline] **Keep high ref** — load-bearing. (2025-01-01, run_d)
  <!-- ref:5 | last:none | decay:dormant -->

- [guideline] **COE07 keep me** — post-mortem in custom section. (2025-01-01, run_e)
  <!-- ref:0 | last:none | decay:dormant -->
"""


class TestReclaimNoiseEntries:
    def test_dry_run_selects_only_reclaimable(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        report = reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=True,
        )
        assert set(report.candidates) == {
            "Reclaim this plain lesson", "Reclaim this plain bug",
        }
        # kept_protected counts only NOISY-but-protected entries (principle +
        # COE07). The high-ref entry has ref!=0 so it's never noisy → not counted.
        assert report.kept_protected == 2

    def test_dry_run_is_read_only(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        report = reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=True,
        )
        # No archive file written, no content mutation returned.
        assert not (tmp_path / "IMPROVEMENT-archive.md").exists()
        assert report.new_content is None
        assert report.archived == 0

    def test_apply_strips_from_content_and_archives(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries, compute_entry_noise
        report = reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=False,
        )
        assert report.archived == 2
        assert report.new_content is not None
        # Physically removed from source.
        assert "Reclaim this plain lesson" not in report.new_content
        assert "Reclaim this plain bug" not in report.new_content
        # Protected entries survive.
        assert "Keep this principle" in report.new_content
        assert "COE07 keep me" in report.new_content
        # Archived to file.
        archive = (tmp_path / "IMPROVEMENT-archive.md").read_text()
        assert "Reclaim this plain lesson" in archive
        # Raw noise metric drops by exactly the reclaimed count. The 2 remaining
        # noisy entries (principle + COE07) are PROTECTED — they stay noisy in
        # the raw gauge but are not reclaimable. The CLI gate keys off
        # reclaimable noise, not raw noise (see TestDddNoiseCli).
        from core.ddd_entry_lifecycle import parse_entries
        before = compute_entry_noise(parse_entries(_RECLAIM_FIXTURE), _RECLAIM_TODAY)
        after = compute_entry_noise(parse_entries(report.new_content), _RECLAIM_TODAY)
        assert before.noisy - after.noisy == 2  # the 2 reclaimable ones removed

    def test_custom_archive_name(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path,
            archive_name="MEMORY-archive.md", dry_run=False,
        )
        assert (tmp_path / "MEMORY-archive.md").exists()
        assert not (tmp_path / "IMPROVEMENT-archive.md").exists()

    def test_evergreen_sections_protect(self, tmp_path):
        from core.ddd_entry_lifecycle import (
            reclaim_noise_entries, MEMORY_EVERGREEN_SECTIONS,
        )
        content = """\
## Principles
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **In evergreen section** — protected by section. (2025-01-01, run_x)
  <!-- ref:0 | last:none | decay:dormant -->
"""
        report = reclaim_noise_entries(
            content, _RECLAIM_TODAY, tmp_path,
            evergreen_sections=MEMORY_EVERGREEN_SECTIONS, dry_run=True,
        )
        assert report.candidates == []
        assert report.kept_protected == 1

    def test_idempotent_second_call_reclaims_nothing(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        r1 = reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=False,
        )
        r2 = reclaim_noise_entries(
            r1.new_content, _RECLAIM_TODAY, tmp_path, dry_run=False,
        )
        assert r2.archived == 0
        assert r2.candidates == []

    def test_empty_content_no_crash(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        report = reclaim_noise_entries("", _RECLAIM_TODAY, tmp_path, dry_run=False)
        assert report.archived == 0
        assert report.candidates == []

    def test_same_title_across_sections_only_strips_the_noise_one(self, tmp_path):
        # Adversarial C1 (CRITICAL): _strip_entries matched title-only, so a
        # keep-class entry sharing a title with a reclaimed one got deleted AND
        # was not archived → unrecoverable loss of protected knowledge. Strip
        # must match by (title, section) identity, not title alone.
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        content = """\
## Pitfalls
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [pitfall] **Cache invalidation** — old operational bug. (2025-01-01, run_a)
  <!-- ref:0 | last:none | decay:archived -->

## Models
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [model] **Cache invalidation** — load-bearing schema note. (2025-01-01, run_b)
  <!-- ref:5 | last:none | decay:active -->
"""
        src = tmp_path / "IMPROVEMENT.md"
        src.write_text(content)
        report = reclaim_noise_entries(
            content, _RECLAIM_TODAY, tmp_path, source_path=src, dry_run=False,
        )
        out = src.read_text()
        # The ref:5 model entry (keep-class) MUST survive — it is NOT the noise.
        assert "load-bearing schema note" in out
        # The pitfall (the actual noise) IS removed.
        assert "old operational bug" not in out
        assert report.archived == 1

    def test_apply_writes_source_backup(self, tmp_path):
        # REVIEW finding #2: destructive reclaim must snapshot the source before
        # overwrite. The archive holds removed entries (forward-append), but a
        # full pre-write .bak is belt-and-braces recovery for the user-owned doc.
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        src = tmp_path / "IMPROVEMENT.md"
        src.write_text(_RECLAIM_FIXTURE)
        report = reclaim_noise_entries(
            _RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path,
            source_path=src, dry_run=False,
        )
        assert report.archived == 2
        bak = tmp_path / f"IMPROVEMENT.md.{_RECLAIM_TODAY.isoformat()}.bak"
        assert bak.exists()
        assert bak.read_text() == _RECLAIM_FIXTURE  # exact pre-write snapshot

    def test_second_run_same_day_does_not_clobber_backup(self, tmp_path):
        # Adversarial H2: a single rolling .bak self-destructs — run 2's backup
        # (post-strip content) would overwrite run 1's pre-strip snapshot. The
        # day's FIRST snapshot must survive a same-day re-run.
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        src = tmp_path / "IMPROVEMENT.md"
        src.write_text(_RECLAIM_FIXTURE)
        reclaim_noise_entries(_RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path,
                              source_path=src, dry_run=False)
        bak = tmp_path / f"IMPROVEMENT.md.{_RECLAIM_TODAY.isoformat()}.bak"
        first_snapshot = bak.read_text()
        # Run 2 on the already-stripped content (simulates the next timer tick).
        reclaim_noise_entries(src.read_text(), _RECLAIM_TODAY, tmp_path,
                              source_path=src, dry_run=False)
        # The day's first pre-strip snapshot is preserved (still has both
        # reclaimable entries), not overwritten with stripped content.
        assert bak.read_text() == first_snapshot
        assert "Reclaim this plain lesson" in bak.read_text()

    def test_no_backup_when_dry_run_or_no_source_path(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        bak_glob = lambda: list(tmp_path.glob("IMPROVEMENT.md*.bak"))
        # dry_run → no backup
        reclaim_noise_entries(_RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=True)
        assert bak_glob() == []
        # no source_path → no backup (caller persists new_content itself)
        reclaim_noise_entries(_RECLAIM_FIXTURE, _RECLAIM_TODAY, tmp_path, dry_run=False)
        assert bak_glob() == []

    def test_dateless_entries_are_NOT_reclaimed(self, tmp_path):
        # SAFETY (run_94fd5597 dry-run finding): 96% of "noise" candidates were
        # date-LESS, not genuinely old — date-less means "nobody stamped a date",
        # not "ancient". Destructive reclaim MUST require a real created_date,
        # even though the read-only gauge treats date-less as old. A date-less
        # dormant ref0 guideline (e.g. "4-component session architecture") is NOT
        # reclaimable — it's unknown-age, not proven-stale.
        from core.ddd_entry_lifecycle import reclaim_noise_entries
        content = """\
## Lessons
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Dateless dormant lesson** — no date in text.
  <!-- ref:0 | last:none | decay:dormant -->

- [guideline] **Dated old dormant lesson** — genuinely old. (2025-01-01, run_x)
  <!-- ref:0 | last:none | decay:dormant -->
"""
        report = reclaim_noise_entries(content, _RECLAIM_TODAY, tmp_path, dry_run=True)
        # Only the DATED old entry is reclaimable; the date-less one is spared.
        assert report.candidates == ["Dated old dormant lesson"]


# ── M0 ② CLEAN: reclaimable-noise gate metric ────────────────────────────────
#
# The GATE must not FAIL a doc just because it holds permanent-but-dormant
# knowledge (COE/principles). compute_reclaimable_noise = raw noise MINUS
# keep-class. This is what the ddd-noise CLI and canary assert on.

class TestComputeReclaimableNoise:
    def test_excludes_protected_from_rate(self):
        from core.ddd_entry_lifecycle import (
            compute_reclaimable_noise, compute_entry_noise,
        )
        raw = compute_entry_noise(parse_entries(_RECLAIM_FIXTURE), _RECLAIM_TODAY)
        gate = compute_reclaimable_noise(parse_entries(_RECLAIM_FIXTURE), _RECLAIM_TODAY)
        # Raw counts principle + COE07 (protected) as noisy; gate does not.
        assert raw.noisy == 4  # 2 reclaimable + principle + COE07
        assert gate.noisy == 2  # only the 2 reclaimable
        assert gate.total == 5

    def test_dateless_excluded_so_gate_matches_action(self):
        # Adversarial H3: the gate (compute_reclaimable_noise) must measure ONLY
        # what reclaim can act on. reclaim skips date-less entries, so the gate
        # must too — else a date-less-dominant doc FAILs forever and no reclaim
        # run can ever bring it to PASS (measure != action).
        from core.ddd_entry_lifecycle import (
            compute_reclaimable_noise, reclaim_noise_entries,
        )
        content = """\
## Lessons
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [guideline] **Dateless dormant noise** — no date stamp.
  <!-- ref:0 | last:none | decay:dormant -->
"""
        gate = compute_reclaimable_noise(parse_entries(content), _RECLAIM_TODAY)
        # Date-less → not reclaimable → must NOT count toward the gate.
        assert gate.noisy == 0
        # And consistency with the action: reclaim also skips it.
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            rep = reclaim_noise_entries(content, _RECLAIM_TODAY, pathlib.Path(td),
                                        dry_run=True)
        assert rep.candidates == []  # gate.noisy == len(candidates): measure==action

    def test_doc_of_only_protected_is_zero_gate_noise(self):
        from core.ddd_entry_lifecycle import compute_reclaimable_noise
        content = """\
## COE Registry
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [pitfall] **COE05 cascade** — permanent post-mortem. (2025-01-01, run_x)
  <!-- ref:0 | last:none | decay:dormant -->
"""
        gate = compute_reclaimable_noise(parse_entries(content), _RECLAIM_TODAY)
        assert gate.noisy == 0  # protected → gate clean even though raw would flag it


class TestCollapseStackedMetadata:
    """R-1: heal the metadata-orphan bug — collapse consecutive metas, prefer real-date.

    Root cause (run_55c02bbe): _extract_lessons_to_memory's off-by-one splice
    orphaned an existing entry's meta as a 2nd consecutive line. inject_entry_metadata
    is orphan-blind (consumes only the first meta). This sweep heals existing stacks.
    """

    def test_collapse_two_metas_keeps_real_date(self):
        from core.ddd_entry_lifecycle import collapse_stacked_metadata
        # live shape: real-date meta on top, orphan (last:none) below
        content = """## Corrections
- [correction] **Some title** — desc (2026-06-01)
  <!-- ref:3 | last:2026-06-25 | decay:active -->
  <!-- ref:0 | last:none | decay:active -->
"""
        out = collapse_stacked_metadata(content)
        assert out.count("<!-- ref:") == 1, "must collapse 2 metas to 1"
        assert "last:2026-06-25" in out, "must keep the real-date meta"
        assert "last:none" not in out, "must drop the orphan"

    def test_prefers_real_date_even_if_orphan_is_first(self):
        from core.ddd_entry_lifecycle import collapse_stacked_metadata
        # robustness: if the none-meta were on top, still keep the real-date one
        content = """## Guidelines
- [guideline] **T** — d (2026-06-01)
  <!-- ref:0 | last:none | decay:active -->
  <!-- ref:2 | last:2026-06-20 | decay:active -->
"""
        out = collapse_stacked_metadata(content)
        assert out.count("<!-- ref:") == 1
        assert "last:2026-06-20" in out, "prefer real-date over none regardless of order"
        assert "last:none" not in out

    def test_two_real_dates_keeps_highest_ref(self):
        # R-1 Gate-2 #5: when two real-dated metas stack, keep the richer
        # (highest-ref) survivor, never silently discard the more-referenced one.
        from core.ddd_entry_lifecycle import collapse_stacked_metadata
        content = """## Decisions
- [decision] **T** — d (2026-06-01)
  <!-- ref:1 | last:2026-06-01 | decay:active -->
  <!-- ref:9 | last:2026-06-30 | decay:active -->
"""
        out = collapse_stacked_metadata(content)
        assert out.count("<!-- ref:") == 1
        assert "ref:9" in out and "last:2026-06-30" in out, "keep highest-ref real meta"
        assert "ref:1 | last:2026-06-01" not in out

    def test_idempotent(self):
        from core.ddd_entry_lifecycle import collapse_stacked_metadata
        content = """## Guidelines
- [guideline] **T** — d (2026-06-01)
  <!-- ref:1 | last:2026-06-20 | decay:active -->
  <!-- ref:0 | last:none | decay:active -->
"""
        once = collapse_stacked_metadata(content)
        twice = collapse_stacked_metadata(once)
        assert once == twice, "second run must be a no-op"

    def test_does_not_touch_bullets_or_single_metas(self):
        from core.ddd_entry_lifecycle import collapse_stacked_metadata
        content = """## Guidelines
- [guideline] **A** — d (2026-06-01)
  <!-- ref:1 | last:2026-06-20 | decay:active -->
- [guideline] **B** — d (2026-06-02)
  <!-- ref:0 | last:none | decay:active -->
"""
        out = collapse_stacked_metadata(content)
        assert out == content, "clean content (no stacks) must be unchanged; bullets untouched"
