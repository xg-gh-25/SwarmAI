"""Tests for M4-3 — score-divergence flag (self-evolution closed loop §6b).

The eval score answers "did the agent pass the golden set?" The mechanical
correction-class tracker answers "is the agent still repeating a known mistake
class in the wild?" These two signals can DIVERGE: a clean 100/100 eval while a
correction class recurs past its deployed gate (🔴). That divergence is exactly
the "100/100 on a dead loop" failure the closed-loop design condemns.

`compute_score_divergence` is a PURE function so the divergence can be asserted on
synthetic state without a live eval run. It is deliberately ORTHOGONAL to the
existing cases_error red-light (which catches judge-infra breakage, not
behavioral recurrence) — the two must not be conflated.
"""

from core.eval_service import EvalService


class TestComputeScoreDivergence:
    """Pure-function contract: high eval score + red mechanical link = diverged."""

    def test_high_score_with_tracker_red_diverges(self):
        health = {"overall_score": 100.0}
        v = EvalService.compute_score_divergence(health, tracker_red=True)
        assert v["diverged"] is True
        assert v["reason"]  # must explain WHY, non-empty

    def test_high_score_clean_tracker_does_not_diverge(self):
        health = {"overall_score": 100.0}
        v = EvalService.compute_score_divergence(health, tracker_red=False)
        assert v["diverged"] is False

    def test_low_score_tracker_red_does_not_diverge(self):
        # A low score already tells the truth — there's nothing to OVERRIDE.
        # Divergence is specifically "the score LIES (high) while reality is red".
        health = {"overall_score": 40.0}
        v = EvalService.compute_score_divergence(health, tracker_red=True)
        assert v["diverged"] is False

    def test_boundary_high_threshold(self):
        # Exactly at the high-score threshold (85) + red → diverged.
        assert EvalService.compute_score_divergence(
            {"overall_score": 85.0}, tracker_red=True
        )["diverged"] is True
        # Just below → not high enough to be a lie worth overriding.
        assert EvalService.compute_score_divergence(
            {"overall_score": 84.9}, tracker_red=True
        )["diverged"] is False

    def test_none_score_does_not_crash(self):
        # No runs yet → overall_score None. Must not crash, must not diverge.
        v = EvalService.compute_score_divergence({"overall_score": None}, tracker_red=True)
        assert v["diverged"] is False

    def test_missing_score_key_does_not_crash(self):
        v = EvalService.compute_score_divergence({}, tracker_red=True)
        assert v["diverged"] is False

    def test_string_score_does_not_crash(self):
        # Run records come from json.loads — a legacy/hand-edited run could carry
        # overall_score as a string. Must not crash on `score < threshold` (adv #1).
        v = EvalService.compute_score_divergence({"overall_score": "90"}, tracker_red=True)
        # Numeric-coercible string → diverges (90 >= 85). Either way: no crash.
        assert v["diverged"] is True
        bad = EvalService.compute_score_divergence({"overall_score": "n/a"}, tracker_red=True)
        assert bad["diverged"] is False  # non-numeric → uninterpretable → no diverge

    def test_int_score(self):
        v = EvalService.compute_score_divergence({"overall_score": 100}, tracker_red=True)
        assert v["diverged"] is True

    def test_is_static_pure(self):
        # Callable without an instance; identical inputs → identical output.
        a = EvalService.compute_score_divergence({"overall_score": 90.0}, tracker_red=True)
        b = EvalService.compute_score_divergence({"overall_score": 90.0}, tracker_red=True)
        assert a == b
        assert a["diverged"] is True
