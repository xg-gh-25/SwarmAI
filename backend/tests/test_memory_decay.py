"""Tests for core/memory_decay.py — Ebbinghaus + Hebbian memory decay scoring.

Tests the mathematical model, spacing effect, archive candidate detection,
reference bumping, and session scanning.
"""

import math
from datetime import date, timedelta

import pytest


# ── Decay Score Computation ──────────────────────────────────────────────────


class TestComputeDecayScore:
    """Ebbinghaus exponential decay with stability modifier."""

    def test_fresh_entry_score_near_one(self):
        """Entry referenced today should have score ~1.0."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        score = compute_decay_score(
            ref_count=1,
            sessions_referenced=1,
            last_referenced=today,
            created=today,
            today=today,
        )
        assert score >= 0.95

    def test_old_unreferenced_entry_decays(self):
        """Entry never referenced after creation decays exponentially."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        created = today - timedelta(days=90)
        score = compute_decay_score(
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=created,
            today=today,
        )
        # With stability=1.0, exp(-90/1.0) is near 0, but floor applies
        assert score == pytest.approx(0.05, abs=0.001)

    def test_floor_never_below_threshold(self):
        """Score never drops below STRENGTH_FLOOR (0.05)."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        created = today - timedelta(days=365)
        score = compute_decay_score(
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=created,
            today=today,
        )
        assert score >= 0.05

    def test_high_ref_count_decays_slower(self):
        """Entry with many references has higher stability → decays slower."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        # Use 5 days — short enough that high-stability entry is still alive
        # but low-stability entry has decayed significantly
        last_ref = today - timedelta(days=5)
        created = today - timedelta(days=120)

        score_low_ref = compute_decay_score(
            ref_count=1, sessions_referenced=1,
            last_referenced=last_ref, created=created, today=today,
        )
        score_high_ref = compute_decay_score(
            ref_count=10, sessions_referenced=8,
            last_referenced=last_ref, created=created, today=today,
        )
        assert score_high_ref > score_low_ref


class TestSpacingEffect:
    """Cepeda spacing effect: distributed > massed reinforcement."""

    def test_spaced_refs_better_than_burst(self):
        """5 sessions × 1 ref produces higher stability than 1 session × 5 refs."""
        from core.memory_decay import compute_stability

        stability_spaced = compute_stability(ref_count=5, sessions_referenced=5)
        stability_burst = compute_stability(ref_count=5, sessions_referenced=1)
        assert stability_spaced > stability_burst

    def test_stability_capped(self):
        """Stability never exceeds MAX_STABILITY."""
        from core.memory_decay import compute_stability, MAX_STABILITY

        stability = compute_stability(ref_count=100, sessions_referenced=50)
        assert stability <= MAX_STABILITY

    def test_zero_refs_baseline_stability(self):
        """Zero references gives baseline stability of 1.0."""
        from core.memory_decay import compute_stability, STABILITY_BASE

        stability = compute_stability(ref_count=0, sessions_referenced=0)
        assert stability == pytest.approx(STABILITY_BASE)


# ── Archive Candidates ───────────────────────────────────────────────────────


class TestArchiveCandidates:
    """Filter entries below threshold + minimum age."""

    def test_low_score_old_entry_is_candidate(self):
        """Entry with low decay score AND old enough → archive candidate."""
        from core.memory_decay import get_archive_candidates, EntryDecayInfo

        today = date(2026, 6, 7)
        entry = EntryDecayInfo(
            entry_id="RC15",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=90),
            section="Recent Context",
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 1
        assert candidates[0].entry_id == "RC15"

    def test_young_entry_never_candidate(self):
        """Entry younger than min_age_days is never a candidate."""
        from core.memory_decay import get_archive_candidates, EntryDecayInfo

        today = date(2026, 6, 7)
        entry = EntryDecayInfo(
            entry_id="RC01",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=30),
            section="Recent Context",
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 0

    def test_permanent_section_immune(self):
        """Entries in PERMANENT sections are never archive candidates."""
        from core.memory_decay import get_archive_candidates, EntryDecayInfo

        today = date(2026, 6, 7)
        entry = EntryDecayInfo(
            entry_id="KD01",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=200),
            section="Key Decisions",
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 0


# ── Session Scanning ─────────────────────────────────────────────────────────


class TestScanSessionForMemoryRefs:
    """Detect MEMORY entry identifiers in session messages."""

    def test_finds_entry_ids_in_messages(self):
        """Detects KD01, LL03 etc. patterns in message content."""
        from core.memory_decay import scan_session_for_memory_refs

        messages = [
            {"role": "user", "content": "check KD01 about maxTurns"},
            {"role": "assistant", "content": "Based on KD01 and LL08, the fix is..."},
        ]
        entry_ids = {"KD01", "KD02", "LL08", "RC01"}
        found = scan_session_for_memory_refs(messages, entry_ids)
        assert found == {"KD01", "LL08"}

    def test_no_false_positives_on_similar_text(self):
        """Don't match partial IDs like 'KD' without number."""
        from core.memory_decay import scan_session_for_memory_refs

        messages = [
            {"role": "assistant", "content": "This is about knowledge discovery"},
        ]
        entry_ids = {"KD01", "KD02"}
        found = scan_session_for_memory_refs(messages, entry_ids)
        assert found == set()


# ── Metadata Bumping ─────────────────────────────────────────────────────────


class TestBumpEntryReferences:
    """Update inline metadata comments in MEMORY.md content."""

    def test_bumps_ref_count_and_last(self):
        """Increments ref count and updates last_referenced date."""
        from core.memory_decay import bump_entry_references

        content = (
            "- [KD01] 2026-06-01 CLI maxTurns fix\n"
            "  <!-- ref:2 | last:2026-06-03 | decay:active | sessions:1 -->\n"
        )
        today = date(2026, 6, 7)
        result = bump_entry_references(content, {"KD01"}, "session_abc", today)
        assert "ref:3" in result
        assert "last:2026-06-07" in result
        assert "sessions:2" in result

    def test_creates_metadata_if_missing(self):
        """Adds metadata comment if entry has none."""
        from core.memory_decay import bump_entry_references

        content = "- [KD05] 2026-05-22 Ontology scope\n\n"
        today = date(2026, 6, 7)
        result = bump_entry_references(content, {"KD05"}, "session_xyz", today)
        assert "<!-- ref:1 | last:2026-06-07 | decay:active | sessions:1 -->" in result
