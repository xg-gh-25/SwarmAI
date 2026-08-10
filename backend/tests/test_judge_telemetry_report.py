"""Tests for judge_telemetry_report — the READ side of judge telemetry.

Locks the calibration logic: fail-closed verdicts (infra failure faked as 'suspect')
must be separated from real judgments, or the pass/discard gauge lies.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.judge_telemetry_report import analyze, render  # noqa: E402


def _row(verdict, reason="judged", section="S", text="some lesson text"):
    return {"ts": "2026-08-10T00:00:00+00:00", "section": section,
            "verdict": verdict, "reason": reason, "text_len": len(text),
            "text_sha": "abc123def456", "text": text}


class TestAnalyze:
    def test_counts_and_rates(self):
        rows = [_row("pass"), _row("pass"), _row("suspect"), _row("noise")]
        s = analyze(rows)
        assert s["total"] == 4
        assert s["verdicts"] == {"pass": 2, "suspect": 1, "noise": 1}
        assert s["pass_rate"] == 0.5
        assert s["discard_rate"] == 0.5

    def test_fail_closed_detected_from_reason(self):
        rows = [
            _row("suspect", reason="judge_error:RuntimeError"),
            _row("suspect", reason="unparseable_or_empty"),
            _row("suspect", reason="too vague"),   # a REAL suspect judgment
            _row("pass"),
        ]
        s = analyze(rows)
        # only the two infra-failure suspects count as fail-closed
        assert s["fail_closed"] == 2

    def test_discarded_collects_suspect_and_noise(self):
        rows = [_row("pass"), _row("suspect"), _row("noise")]
        s = analyze(rows)
        assert len(s["discarded"]) == 2


class TestRenderCalibration:
    def test_high_fail_closed_is_red_and_dominates(self):
        # 8/18 fail-closed → RED, and pass/discard flagged unreliable.
        rows = ([_row("suspect", reason="judge_error:RuntimeError")] * 4
                + [_row("suspect", reason="unparseable_or_empty")] * 4
                + [_row("suspect", reason="vague")] * 5
                + [_row("noise")] * 3
                + [_row("pass")] * 2)
        out = render(analyze(rows), days=0)
        assert "🔴" in out
        assert "fail-closed" in out
        assert "UNRELIABLE" in out
        # real-judgment pass rate computed over 10 (18-8), not 18
        assert "10 real judgments" in out or "/10" in out

    def test_zero_pass_over_real_judgments_is_red(self):
        rows = [_row("suspect", reason="vague")] * 6  # all real, none pass
        out = render(analyze(rows), days=0)
        assert "🔴" in out
        assert "broken-strict" in out

    def test_healthy_band_is_green(self):
        rows = [_row("pass")] * 5 + [_row("suspect", reason="vague")] * 3 + [_row("noise")] * 2
        out = render(analyze(rows), days=0)
        assert "🟢" in out

    def test_empty_window_is_graceful(self):
        out = render(analyze([]), days=7)
        assert "No judge verdicts" in out
