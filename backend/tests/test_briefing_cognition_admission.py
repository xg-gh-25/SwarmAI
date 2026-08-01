"""Purity tests for the system-prompt session briefing (run_05b42b8b).

The system-prompt briefing (``build_session_briefing``) must stay 精简/稳定/快 and carry
ONLY content that helps the agent make THIS turn's judgment. Per the SwarmAI DDD TECH.md
"System-prompt purity" invariant (XG directive, 2026-08-01):

  (1) filesystem-ONLY as data source — NO DB/sqlite/eval_service on the assembly path
  (2) NO dashboard/feed/status-board signals — those belong to the Welcome Screen
  (3) the briefing is DECOUPLED from build_session_briefing_data
  (4) don't accrete arbitrary signals into the system prompt over time

An earlier cut (run_a16d61ad) removed the feed sections' OUTPUT (tokens) but LEFT their
I/O (glob-all-pipelines, raw-sqlite todos, eval 193-case load, health scan) on the
per-message assembly path — build_session_briefing ran 99s under concurrent-session GIL
contention. This run removes the I/O: the briefing keeps ONLY the Suggested-focus section
(sourced purely from MEMORY.md + DailyActivity) and the feed/DB/eval helpers are deleted.

The STRUCTURED twin ``build_session_briefing_data`` (frontend Welcome Screen) is the correct
home for feed data and is UNCHANGED — these tests assert that split holds.

Methodology: TDD RED→GREEN. Source-level assertions (the removed sections must not be
appended, the DB/eval calls must not be reachable) + a live-render regression guard.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import core.proactive_intelligence as pi


def _strip_comments_and_docstrings(src: str) -> str:
    """Return only the executable code of a function source — comments and the
    docstring removed — so purity assertions match real CODE references, not
    prose in explanatory comments that describe what was removed."""
    import ast

    # Drop full-line and inline comments first (safe for this codebase: no '#'
    # appears inside string literals in these two functions).
    no_comments = "\n".join(
        line.split("#", 1)[0] if "#" in line else line
        for line in src.splitlines()
    )
    # Drop the docstring node via AST (dedent so the def parses standalone).
    try:
        import textwrap
        tree = ast.parse(textwrap.dedent(no_comments))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    no_comments = no_comments.replace(doc, "", 1)
    except SyntaxError:
        pass  # fall back to comment-stripped source
    return no_comments


def _briefing_src() -> str:
    """Source of the markdown briefing builder ONLY (not the data twin)."""
    return inspect.getsource(pi.build_session_briefing)


def _briefing_code() -> str:
    """Executable code of the briefing builder (comments + docstring stripped)."""
    return _strip_comments_and_docstrings(_briefing_src())


# Section-header markers that MUST NOT be appended by the slimmed briefing.
# Every one of these is a feed/status-board section removed this run (or a prior cut).
# NOTE: "Pipeline auto-resume" is NOT here — it is a LIVE fs-only engine (the only
# auto-resume trigger, verified 147 runs with resume_attempts>0), kept as a KEEP.
_REMOVED_SECTION_MARKERS = [
    '"**Pending Radar todos:**\\n"',    # DB source (_get_todo_highlights raw sqlite3)
    '"**System health:**\\n"',          # health_findings feed
    '"**Recurrence Radar:**\\n"',       # IMPROVEMENT.md regex scan
    '"**DDD escalations (',             # DDD pending-proposals queue
    '"**Skill health:**',               # evolution skill-health stats
    '"**Signals:**\\n"',                # temporal signals (prior cut)
    '"**External signals since last session:**\\n"',
    '"**Recent job results (last 24h):**\\n"',
    '"**DDD auto-applies (72h review window):**\\n"',
    '"**Learning:** {learning_insight}"',
    '"**Codebase intelligence (',
    'sections.append(background_section)',
]

# The ONE section that MUST remain — Suggested focus (pure fs: MEMORY + DailyActivity).
_KEEP_MARKER = "focus_section"


class TestOnlyFocusSectionRemains:
    @pytest.mark.parametrize("marker", _REMOVED_SECTION_MARKERS)
    def test_removed_section_not_appended(self, marker: str):
        src = _briefing_src()
        assert marker not in src, (
            f"Removed feed/status section marker still present in build_session_briefing: {marker!r}. "
            "The system-prompt briefing keeps ONLY Suggested focus (TECH.md purity invariant)."
        )

    def test_focus_section_still_appended(self):
        src = _briefing_src()
        assert _KEEP_MARKER in src, (
            "The Suggested-focus section must remain — it is the only section the "
            "system-prompt briefing keeps (sourced from MEMORY.md + DailyActivity)."
        )


class TestNoDbOrEvalOnAssemblyPath:
    """The system-prompt assembly path must be filesystem-only (no DB, no eval_service)."""

    def test_briefing_source_has_no_db_or_eval(self):
        # Match CODE references, not comment prose (the comments describe what was
        # removed and legitimately name these symbols).
        code = _briefing_code()
        for forbidden in (
            "get_eval_service",       # eval subsystem
            "eval_service",
            "_get_todo_highlights",   # raw sqlite3 DB read
            "_render_self_eval_lines",
            "CorrectionClassTracker",
        ):
            assert forbidden not in code, (
                f"build_session_briefing must not reference {forbidden!r} in CODE — "
                "no DB/eval on the filesystem-only assembly path (TECH.md purity invariant)."
            )

    def test_active_session_digest_has_no_db_read(self):
        # _build_active_session_digest was the 2nd DB source on the assembly path
        # (db.messages.get_last_by_session). After the cut it must not read the DB.
        # Match CODE (comments legitimately name the removed call).
        from core.prompt_builder import PromptBuilder
        code = _strip_comments_and_docstrings(
            inspect.getsource(PromptBuilder._build_active_session_digest)
        )
        assert "db.messages" not in code and "get_last_by_session" not in code, (
            "_build_active_session_digest must not read the DB in CODE — the "
            "system-prompt assembly path is filesystem-only (TECH.md purity invariant)."
        )
        # Also assert the DB import is gone from the function body.
        assert "from database import db" not in code, (
            "_build_active_session_digest must not import the DB module."
        )


class TestDeadHelpersRemoved:
    @pytest.mark.parametrize(
        "helper",
        [
            "_get_todo_highlights",
            "_get_health_highlights",
            "_get_ddd_drift_line",
            "compute_recurrence_radar",
            "_render_self_eval_lines",
            "_get_skill_health_highlights",
            # transitive dead (only called by the deleted primaries):
            "_extract_what_failed_lines",
            "_get_auto_apply_review_window",
            "_detect_active_project",
        ],
    )
    def test_helper_deleted(self, helper: str):
        assert not hasattr(pi, helper), (
            f"{helper} must be deleted — 0-caller after the briefing slim "
            "(feed/DB/eval sections removed; dead code deleted with them)."
        )

    def test_auto_resume_engine_retained(self):
        # _get_paused_pipeline_highlights + _newest_completed_run are the LIVE
        # auto-resume engine (fs-only, the only trigger that resumes a paused
        # pipeline). They must NOT be deleted — deleting them silently kills
        # pipeline recovery (regression caught in adversarial review).
        assert hasattr(pi, "_get_paused_pipeline_highlights"), (
            "_get_paused_pipeline_highlights is the live auto-resume trigger — must be retained."
        )
        assert hasattr(pi, "_newest_completed_run"), (
            "_newest_completed_run (used by the auto-resume engine) must be retained."
        )


class TestBriefingStillRendersEndToEnd:
    """REGRESSION GUARD: the slim must not leave dangling locals that NameError → None."""

    def _seed_focus_workspace(self, tmp: Path) -> Path:
        (tmp / ".context").mkdir(parents=True, exist_ok=True)
        (tmp / ".context" / "MEMORY.md").write_text(
            "## Open Threads\n\n"
            "### P0 — Critical\n"
            "- **Critical test thread** — a real unfinished P0 item to surface now.\n",
            encoding="utf-8",
        )
        daily = tmp / "Knowledge" / "DailyActivity"
        daily.mkdir(parents=True, exist_ok=True)
        (daily / "2026-08-01-x.md").write_text(
            "## 07:00 | sess | working\n- did a thing\n", encoding="utf-8"
        )
        return tmp

    def test_briefing_renders_focus_only(self, tmp_path: Path):
        ws = self._seed_focus_workspace(tmp_path)
        result = pi.build_session_briefing(ws)
        assert result is not None, (
            "build_session_briefing returned None on a workspace with a P0 thread — "
            "a swallowed exception (dangling removed-section locals) made it silently dead."
        )
        assert "Session Briefing" in result
        assert "Suggested focus" in result
        # None of the removed feed sections may appear in the rendered output.
        # ("Pipeline auto-resume" is NOT here — it is a retained live engine.)
        for header in ("Pending Radar todos", "System health",
                       "Recurrence Radar", "DDD escalations", "Skill health", "Score:"):
            assert header not in result, (
                f"Removed section {header!r} appeared in rendered briefing — slim incomplete."
            )

    def test_no_dangling_removed_locals_in_trailing_log(self):
        src = _briefing_src()
        assert "len(signal_lines)" not in src, "trailing log references deleted local signal_lines"
        assert "learning_insight else" not in src, "trailing log references deleted local learning_insight"
        assert "len(briefing) // 4" not in src, "stale len//4 estimator still in briefing (use estimate_tokens)"


class TestStructuredTwinUnchanged:
    """build_session_briefing_data (Welcome Screen) keeps feed data — dashboard is its right home."""

    def test_data_twin_still_builds_feed_sections(self):
        src = inspect.getsource(pi.build_session_briefing_data)
        assert "signal" in src.lower() or "job" in src.lower(), (
            "build_session_briefing_data must remain the feed-data consumer (frontend dashboard)."
        )

    def test_data_twin_import_still_resolves(self):
        # The twin must still be importable + callable after helper deletions
        # (it must not depend on any deleted helper).
        assert callable(pi.build_session_briefing_data)
