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


class TestUsageDecay:
    """Write-time exponential decay that kills the cumulative ratchet (run_81f6d20c).

    The producer (.memory-usage.json) counts every [ID] citation cumulatively and
    never decremented → a once-hot-now-cold entry stayed protected forever.
    decay_usage_counts applies 0.5**(days/halflife) at write time so cold entries
    fade below the protection threshold (10) and eventually below epsilon (dropped).
    """

    def test_halflife_halves_count(self):
        """After exactly one half-life, a count is halved."""
        from core.memory_decay import decay_usage_counts, USAGE_HALFLIFE_DAYS

        out = decay_usage_counts({"PIT07": 40.0}, int(USAGE_HALFLIFE_DAYS))
        assert out["PIT07"] == pytest.approx(20.0, rel=0.01)

    def test_zero_or_negative_elapsed_is_identity(self):
        """days_elapsed <= 0 → unchanged (same-day re-run must not double-decay)."""
        from core.memory_decay import decay_usage_counts

        src = {"PIT07": 40.0, "GUI99": 12.0}
        assert decay_usage_counts(src, 0) == src
        assert decay_usage_counts(src, -5) == src

    def test_epsilon_drops_faded_keys(self):
        """A key decayed below epsilon is removed entirely (file hygiene + window-out)."""
        from core.memory_decay import decay_usage_counts, USAGE_HALFLIFE_DAYS

        # 1.0 count, 5 half-lives → 0.03125 < 0.5 epsilon → dropped
        out = decay_usage_counts({"DEC01": 1.0}, int(USAGE_HALFLIFE_DAYS) * 5, epsilon=0.5)
        assert "DEC01" not in out
