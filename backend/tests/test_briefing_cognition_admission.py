"""Cognition-admission tests for the markdown session briefing (run_a16d61ad, §4.2.1).

The system-prompt briefing (``build_session_briefing``) is COGNITION real-estate, not a
dashboard. Per the 2026-06-28 READ-line design §4.2.1, 8 sections are CUT from the markdown
briefing (feed/status-board noise that competes with the 11 context files for attention):

  temporal Signals · background-suggestions · External-signals · Job-results ·
  DDD-auto-applies · DDD-trust · Learning · Codebase-intelligence

KEEP (承重 — self-state cognition + imperative unfinished work): focus · paused-pipeline ·
Radar todos · System health · Recurrence Radar · DDD escalations · open-threads.

The STRUCTURED twin ``build_session_briefing_data`` (frontend Welcome Screen) is the correct
home for feed data and is UNCHANGED — these tests assert that split holds.

Methodology: TDD RED→GREEN. Source-level assertions (the CUT sections must not be appended
in the markdown path, and the two markdown-only helpers must be deleted as 0-caller dead code).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import core.proactive_intelligence as pi


# Markers that uniquely identify each CUT section's APPEND in build_session_briefing.
# These are the literal header strings appended via sections.append(...).
_CUT_MARKERS = [
    '"**Signals:**\\n"',                                  # temporal signals
    '"**External signals since last session:**\\n"',      # external feed
    '"**Recent job results (last 24h):**\\n"',            # job results
    '"**DDD auto-applies (72h review window):**\\n"',     # auto-apply countdown
    '"**DDD low-trust sections**',                        # ddd-trust (1273:2 no discrimination)
    '"**Learning:** {learning_insight}"',                 # learning statistical portrait
    '"**Codebase intelligence (',                         # codebase map (PUSH→PULL)
    'sections.append(background_section)',                # "Also in..." background-suggestions APPEND
]

# KEEP承重 markers — must still be appended.
_KEEP_MARKERS = [
    '"**System health:**\\n"',
    '"**Recurrence Radar:**\\n"',
    '"**Pending Radar todos:**\\n"',
    '"**Pipeline auto-resume',
    '"**DDD escalations (',
]


def _briefing_src() -> str:
    """Source of the markdown briefing builder ONLY (not the data twin)."""
    return inspect.getsource(pi.build_session_briefing)


class TestCutSectionsRemovedFromMarkdownBriefing:
    @pytest.mark.parametrize("marker", _CUT_MARKERS)
    def test_cut_section_not_appended(self, marker: str):
        src = _briefing_src()
        assert marker not in src, (
            f"CUT section marker still present in build_session_briefing: {marker!r}. "
            "Per §4.2.1 this feed/status-board section must not occupy system-prompt cognition."
        )

    @pytest.mark.parametrize("marker", _KEEP_MARKERS)
    def test_keep_section_still_appended(self, marker: str):
        src = _briefing_src()
        assert marker in src, (
            f"KEEP承重 section marker missing from build_session_briefing: {marker!r}. "
            "DoD12 zero-regression: 承重 cognition (health/radar/todos/paused/escalations) must stay."
        )


class TestDeadHelpersRemoved:
    def test_ddd_trust_summary_helper_deleted(self):
        assert not hasattr(pi, "_get_ddd_trust_summary"), (
            "_get_ddd_trust_summary must be deleted (0-caller after DDD-trust CUT, §4.2.1 #11)."
        )

    def test_detect_active_coding_project_deleted(self):
        assert not hasattr(pi, "_detect_active_coding_project"), (
            "_detect_active_coding_project must be deleted (0-caller after codebase-map block removed)."
        )
        assert not hasattr(pi, "_detect_active_coding_project_impl"), (
            "_detect_active_coding_project_impl (inner) must be deleted too."
        )


class TestSelfEvalConvergesToRedLineOnly:
    """self-eval in the briefing keeps only the 🧬 red lines; the clean 'Score: X' line is dropped."""

    def test_clean_score_line_not_emitted(self):
        # Healthy state: score present, no divergence, no errors → renderer returns [].
        health = {"overall_score": 100.0, "last_run": {"triggered_at": "2026-06-27", "cases_error": 0}}
        lines = pi._render_self_eval_lines(health, tracker_red=False, case_count=172, draft_skeletons=0)
        joined = "\n".join(lines)
        assert "Score:" not in joined, (
            "Clean 'Score: X' line must NOT occupy system-prompt cognition (no-discrimination, "
            "near-constant 100). Only 🧬 divergence/error red lines earn the headline (§4.2.1 #13)."
        )

    def test_divergence_red_line_still_emitted(self):
        health = {"overall_score": 100.0, "last_run": {"triggered_at": "2026-06-27", "cases_error": 0}}
        lines = pi._render_self_eval_lines(health, tracker_red=True, case_count=172, draft_skeletons=0)
        joined = "\n".join(lines)
        assert "🔴" in joined or "DIVERGENCE" in joined, (
            "Gate-failure divergence MUST still surface — that is the load-bearing self-eval signal."
        )


class TestBriefingStillRendersEndToEnd:
    """REGRESSION GUARD (run_a16d61ad): the CUT must not leave dangling locals.

    A source-grep that the CUT sections are gone can PASS while the function is
    runtime-DEAD: the half-done CUT left the trailing log referencing locals
    (`signal_lines` / `learning_insight`) whose assignments were removed by the
    CUT → NameError → swallowed by the outer `except` → build_session_briefing
    silently returns None on EVERY call. Source assertions never catch this; only
    actually RENDERING the briefing does. This is the GUI222 silent-failure class.
    """

    def _seed_keep_workspace(self, tmp: Path) -> Path:
        """A synthetic workspace that triggers at least one KEEP section so the
        briefing returns a non-empty string (not None-because-empty)."""
        (tmp / ".context").mkdir(parents=True, exist_ok=True)
        # MEMORY.md with a P0 Open Thread → survives the quality filter
        # (build_session_briefing:1766 keeps only P0/P1/blocking/high-freq items),
        # so `ranked` is non-empty → a focus_section is appended → the trailing
        # log (the NameError regression site) actually executes.
        # Priority comes from a `### P0 ` header (not the line emoji) — see
        # _PRIORITY_HEADER_RE. A P0 thread survives the quality filter at
        # build_session_briefing:1766.
        (tmp / ".context" / "MEMORY.md").write_text(
            "## Open Threads\n\n"
            "### P0 — Critical\n"
            "- **Critical test thread** — a real unfinished P0 item to surface now.\n",
            encoding="utf-8",
        )
        daily = tmp / "Knowledge" / "DailyActivity"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2026-06-28-x.md").write_text(
            "## 07:00 | sess | working\n- did a thing\n", encoding="utf-8"
        )
        return tmp

    def test_briefing_does_not_raise_and_renders(self, tmp_path: Path):
        ws = self._seed_keep_workspace(tmp_path)
        # Must NOT raise (the NameError regression raised inside, was swallowed to None).
        result = pi.build_session_briefing(ws)
        # With a KEEP-triggering workspace the briefing must produce content,
        # NOT silently collapse to None via a swallowed NameError.
        assert result is not None, (
            "build_session_briefing returned None on a workspace that has KEEP content — "
            "a swallowed exception (dangling CUT locals) made the briefing silently dead."
        )
        assert "Session Briefing" in result

    def test_no_dangling_cut_locals_in_trailing_log(self):
        """The trailing log must not reference locals the CUT removed."""
        src = _briefing_src()
        # These were assigned only inside now-deleted CUT sections.
        assert "len(signal_lines)" not in src, "trailing log still references deleted local signal_lines"
        assert "learning_insight else" not in src, "trailing log still references deleted local learning_insight"
        # And the dual-estimator leftover must be gone (design §1).
        assert "len(briefing) // 4" not in src, "stale len//4 estimator still in briefing (use estimate_tokens)"


class TestStructuredTwinUnchanged:
    """build_session_briefing_data (Welcome Screen) keeps feed data — dashboard is its right home."""

    def test_feed_helpers_not_deleted_by_this_commit(self):
        # SCOPE BOUNDARY (corrected after Gate-2 adversarial): these helpers are
        # NOT deleted by the cognition-admission CUT. NOTE the accurate reason —
        # they are currently 0-caller in production (the data twin
        # build_session_briefing_data reads signal_digest.json / .job-results.jsonl
        # INLINE, it does NOT call these helpers). They are out-of-scope dead-code,
        # not "shared with the twin"; deleting them is a separate cleanup (a
        # follow-up), and this CUT must not silently take them. This test pins the
        # boundary: the CUT removed APPENDS, not these helper definitions.
        for name in ("_get_signal_highlights", "_get_job_result_highlights"):
            assert hasattr(pi, name), (
                f"{name} must not be deleted by the cognition-admission CUT "
                "(out-of-scope dead-code cleanup, tracked as a follow-up)."
            )

    def test_data_twin_still_builds_feed_sections(self):
        # The structured twin still produces feed data (reads signal/job JSON inline).
        # It is the correct home for dashboard feed — this CUT must not touch it.
        src = inspect.getsource(pi.build_session_briefing_data)
        assert "signal" in src.lower() or "job" in src.lower(), (
            "build_session_briefing_data must remain the feed-data consumer (frontend dashboard)."
        )
