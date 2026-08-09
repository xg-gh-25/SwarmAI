"""Knowledge Admission — Component A: is_noise() SSOT.

is_noise(text) -> (bool, reason) is the SINGLE consolidated noise gate. A proposal
that is_noise → DISCARD before it can be queued/scored/shown. It composes the
existing primitives (is_quality_lesson: instance-log/narration/fragment) AND adds
machine-broadcast detection (code_intel_feed "Architecture change detected" style),
returning a machine-readable reason for observability. (Design AC1.)
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestIsNoise:
    def _fn(self):
        from core.ddd_cultivation import is_noise
        return is_noise

    def test_instance_log_is_noise(self):
        noise, reason = self._fn()("exit_code: 0")
        assert noise is True
        assert reason  # non-empty machine-readable reason

    def test_narration_is_noise(self):
        noise, reason = self._fn()("I'll diagnose the root cause now")
        assert noise is True
        assert reason

    def test_short_fragment_is_noise(self):
        noise, reason = self._fn()("done")
        assert noise is True

    def test_machine_broadcast_is_noise(self):
        # the code_intel_feed drift-broadcast shape — pure machine observation.
        noise, reason = self._fn()(
            "Architecture change detected:\n- new_module: `backend/core/foo.py`"
        )
        assert noise is True
        assert "broadcast" in reason or "machine" in reason

    def test_undocumented_module_broadcast_is_noise(self):
        noise, reason = self._fn()(
            "Undocumented module `backend/core/bar.py` (5 functions). "
            "Consider adding to TECH.md architecture documentation."
        )
        assert noise is True

    def test_real_lesson_is_not_noise(self):
        noise, reason = self._fn()(
            "A silent race drops a bubble when two writes interleave; a recurring bug "
            "whenever a fallback path is added without a client_id."
        )
        assert noise is False
        assert reason in ("", "clean", None) or not reason

    def test_real_lesson_opening_with_broadcast_words_is_NOT_dropped(self):
        # Gate-2 HOLE#1: a human lesson that merely OPENS with "Architecture change
        # detected" (no machine colon/list shape) must NOT be false-discarded.
        noise, reason = self._fn()(
            "Architecture change detected requires a careful versioning strategy so "
            "existing clients do not break when the schema shifts underneath them."
        )
        assert noise is False, f"real lesson wrongly dropped as {reason}"

    def test_real_lesson_mentioning_undocumented_module_is_not_dropped(self):
        noise, _ = self._fn()(
            "An undocumented module is a smell but not a blocker — prefer shipping with "
            "a TODO over gating the release on doc coverage, per our velocity priority."
        )
        assert noise is False

    def test_real_decision_is_not_noise(self):
        noise, _ = self._fn()(
            "Chose read-side percentiles over SQL pre-aggregation because SQLite has "
            "no percentile function and the row volume is bounded."
        )
        assert noise is False

    def test_reason_is_stable_string(self):
        # reason must be a short stable token (for logging/telemetry), not prose.
        noise, reason = self._fn()("stdout: foo")
        assert isinstance(reason, str) and len(reason) <= 40
