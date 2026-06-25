"""Tests for M4-3 briefing wiring — the self-eval line renders a divergence
OVERRIDE when the eval score lies (high) while a correction class is 🔴.

The rendering logic is extracted into the pure module-level helper
`_render_self_eval_lines(health, tracker_red, case_count)` so the divergence
behavior is assertable WITHOUT constructing the full briefing (which does heavy
I/O). The helper is the single source of truth for how the self-eval line is
shown; build_session_briefing just calls it.
"""

from core.proactive_intelligence import _render_self_eval_lines


class TestSelfEvalRendering:
    def test_clean_high_score_no_divergence(self):
        health = {
            "overall_score": 100.0,
            "last_run": {"triggered_at": "2026-06-25T00:00:00", "cases_error": 0},
        }
        lines = _render_self_eval_lines(health, tracker_red=False, case_count=128)
        text = "\n".join(lines)
        assert "Score: 100.0" in text
        assert "DIVERG" not in text.upper()
        assert "🔴" not in text

    def test_cases_error_redlight_still_fires(self):
        # finding-1's existing infra-break red-light must be preserved.
        health = {
            "overall_score": 100.0,
            "last_run": {"triggered_at": "2026-06-25T00:00:00", "cases_error": 88},
        }
        lines = _render_self_eval_lines(health, tracker_red=False, case_count=128)
        text = "\n".join(lines)
        assert "🔴" in text
        assert "ERRORED" in text or "judge infra" in text

    def test_high_score_tracker_red_shows_divergence_override(self):
        # The NEW M4-3 signal: clean score but recurring correction class.
        health = {
            "overall_score": 100.0,
            "last_run": {"triggered_at": "2026-06-25T00:00:00", "cases_error": 0},
        }
        lines = _render_self_eval_lines(health, tracker_red=True, case_count=128)
        text = "\n".join(lines)
        # Divergence must be surfaced AND visually override the clean number.
        assert "🔴" in text
        assert "DIVERG" in text.upper()
        # The raw "Score: 100.0 | clean" framing must NOT be the headline.
        assert "recurred" in text.lower() or "correction class" in text.lower()

    def test_divergence_independent_of_cases_error(self):
        # tracker_red divergence fires even when cases_error == 0 — proves it is
        # orthogonal to the infra-break light, not a duplicate of it.
        health = {
            "overall_score": 90.0,
            "last_run": {"triggered_at": "2026-06-25T00:00:00", "cases_error": 0},
        }
        lines = _render_self_eval_lines(health, tracker_red=True, case_count=50)
        assert any("DIVERG" in ln.upper() for ln in lines)

    def test_low_score_tracker_red_no_override(self):
        # Low score is already honest — no override needed even if tracker red.
        health = {
            "overall_score": 40.0,
            "last_run": {"triggered_at": "2026-06-25T00:00:00", "cases_error": 0},
        }
        lines = _render_self_eval_lines(health, tracker_red=True, case_count=50)
        text = "\n".join(lines)
        assert "DIVERG" not in text.upper()

    def test_no_runs_yet(self):
        health = {"overall_score": None}
        lines = _render_self_eval_lines(health, tracker_red=False, case_count=10)
        assert any("no runs yet" in ln for ln in lines)

    def test_never_raises_on_garbage(self):
        # Briefing helpers must never raise (build_session_briefing contract).
        assert _render_self_eval_lines({}, tracker_red=True, case_count=0) == [] or isinstance(
            _render_self_eval_lines({}, tracker_red=True, case_count=0), list
        )
