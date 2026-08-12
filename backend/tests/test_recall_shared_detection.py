"""Recall path refactor (run_6ebf6479): shared active-project detection + fault isolation.

Two problems this refactor fixes, each proven by a test here that is RED against the
pre-refactor code:

  B (correctness) — the DDD leg (_inject_ddd_for_active_project) and the unified leg
  (_unified_recall_body) each call detect_active_project() SEPARATELY, and with
  DIFFERENT inputs (DDD leg: full user_message; unified leg: extracted keywords). So
  they can (a) run detect_active_project TWICE (redundant blocking iterdir) and (b)
  resolve DIFFERENT active projects on the same turn. FIX: detect ONCE at the top of
  _maybe_inject_recall (using the full user_message = strongest signal), cache on
  unit._active_project, both legs read the cache.

  A (robustness) — _maybe_inject_recall guards its DDD block and its unified block
  individually, but the between-block code (keyword extraction, base-token estimate)
  is UNGUARDED, so an exception there escapes to the send path and can crash the
  system-prompt builder. FIX: a top-level try wraps the whole body; on any exception
  it logs loud + latches the guard (no infinite retry) + returns cleanly (recall
  block simply absent, core context untouched).

Methodology: mutation-proof. Each test asserts the POST-refactor contract; run against
HEAD (pre-refactor) they are RED (B1 sees call_count==2; B2 sees legs disagree; A1/A2
see the exception propagate). Boundary-only mocking: detect_active_project and the
recall legs are the seams; the orchestration under test is real.
"""
import pytest
from unittest.mock import MagicMock, patch


def _make_unit():
    unit = MagicMock()
    unit._recall_injected = False
    unit._ddd_injected = False
    unit.is_channel_session = False
    unit._recall_keyword_misses = 0
    # The refactor introduces this cached attr; a fresh unit starts unset.
    unit._active_project = None
    return unit


def _make_options():
    options = MagicMock()
    options.system_prompt = "## Base system prompt content"
    return options


class TestSharedActiveProjectDetection:
    """B: detect_active_project resolves ONCE per recall and both legs agree."""

    @pytest.mark.asyncio
    async def test_detect_called_once_not_twice(self):
        """B1: across the DDD leg + the unified leg, detect_active_project is called
        EXACTLY ONCE. RED pre-refactor: each leg calls it independently → 2.

        NOTE: we do NOT mock _unified_recall_body (that would hide its OWN internal
        detect call and make the double-count invisible). We let both real legs run
        and mock only the leaf deps (detect + recall_all + render + graph enrich)."""
        from core.session_router import _maybe_inject_recall

        detect = MagicMock(return_value=("SwarmAI", "signal1_project_path"))
        with patch("core.recall_multi.detect_active_project", detect), \
             patch("core.recall_multi.recall_all", return_value=MagicMock(buckets={"ddd": []})), \
             patch("core.recall_multi.render_recall_body", return_value=""), \
             patch("core.session_router._graph_enrich_recall", return_value=""), \
             patch("core.session_router._flatten_recall_hits", return_value=[]), \
             patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline recall work?",
                options=_make_options(),
                unit=_make_unit(),
                editor_context={"file_path": "/x/Projects/SwarmAI/2-understanding/TECH.md"},
            )

        assert detect.call_count == 1, (
            f"detect_active_project must resolve ONCE and be shared by both legs; "
            f"got {detect.call_count} calls (pre-refactor double-detect)"
        )

    @pytest.mark.asyncio
    async def test_detection_uses_full_user_message_not_stripped_keywords(self):
        """B2: the shared detection uses the FULL user_message (strongest signal),
        so a message whose keyword-extraction would strip the project name still
        resolves the right project. RED pre-refactor: the unified leg detects from
        extracted keywords → a SECOND, divergent query reaches detection."""
        from core.session_router import _maybe_inject_recall

        seen_queries = []

        def _detect(editor_file_path=None, query=None, candidates=None):
            seen_queries.append(query)
            return ("SwarmAI", "signal3_keyword")

        with patch("core.recall_multi.detect_active_project", side_effect=_detect), \
             patch("core.recall_multi.recall_all", return_value=MagicMock(buckets={"ddd": []})), \
             patch("core.recall_multi.render_recall_body", return_value=""), \
             patch("core.session_router._graph_enrich_recall", return_value=""), \
             patch("core.session_router._flatten_recall_hits", return_value=[]), \
             patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="please help with the evolution pipeline thing",
                options=_make_options(),
                unit=_make_unit(),
                editor_context=None,
            )

        assert seen_queries, "detection must run"
        # Every detection this turn must see the SAME (full) query — not one call with
        # the full message and another with stripped keywords.
        assert len(set(seen_queries)) == 1, (
            f"all detections must share one query; saw divergent inputs {seen_queries!r}"
        )
        assert seen_queries[0] == "please help with the evolution pipeline thing", (
            "shared detection must use the full user_message, not extracted keywords"
        )

    @pytest.mark.asyncio
    async def test_detection_result_cached_on_unit(self):
        """B3: the resolved (project, signal) is cached on unit._active_project so a
        re-entry within the session does not re-detect."""
        from core.session_router import _maybe_inject_recall

        unit = _make_unit()
        detect = MagicMock(return_value=("SwarmAI", "signal1_project_path"))
        with patch("core.recall_multi.detect_active_project", detect), \
             patch("core.session_router._unified_recall_body", return_value=("", None)), \
             patch("core.session_router._recall_for_query", return_value=""), \
             patch("core.recall_multi.recall_all", return_value=MagicMock(buckets={"ddd": []})):
            await _maybe_inject_recall(
                user_message="how does recall detection work in the pipeline",
                options=_make_options(),
                unit=unit,
                editor_context={"file_path": "/x/Projects/SwarmAI/2-understanding/TECH.md"},
            )

        assert unit._active_project == ("SwarmAI", "signal1_project_path"), (
            "shared detection must cache (project, signal) on unit._active_project"
        )


class TestRecallFaultIsolation:
    """A: recall can NEVER propagate an exception to the send path."""

    @pytest.mark.asyncio
    async def test_keyword_extraction_exception_does_not_propagate(self):
        """A1: an exception in the between-block code (keyword extraction) is caught
        by the top-level guard — _maybe_inject_recall returns cleanly, core context
        (already committed by prompt_builder into options.system_prompt) is intact,
        no recall block appended. RED pre-refactor: the exception escapes."""
        from core.session_router import _maybe_inject_recall

        options = _make_options()
        core = options.system_prompt
        unit = _make_unit()

        with patch("core.session_router._extract_query_keywords",
                   side_effect=RuntimeError("boom in keyword extraction")):
            # Must NOT raise.
            result = await _maybe_inject_recall(
                user_message="anything at all here",
                options=options,
                unit=unit,
                editor_context=None,
            )

        assert result is None, "on internal failure, recall reports 'did not run' (None)"
        assert options.system_prompt == core, (
            "core context must be untouched — recall failure appends nothing"
        )
        assert "Recalled Knowledge" not in options.system_prompt

    @pytest.mark.asyncio
    async def test_failed_extraction_latches_guard_no_infinite_retry(self):
        """A2: after an internal failure the once-per-session guard is latched so the
        failing path is not re-run every turn. RED pre-refactor: unguarded exception
        never reaches the latch."""
        from core.session_router import _maybe_inject_recall

        unit = _make_unit()
        with patch("core.session_router._extract_query_keywords",
                   side_effect=RuntimeError("boom")):
            await _maybe_inject_recall(
                user_message="anything at all here",
                options=_make_options(),
                unit=unit,
                editor_context=None,
            )

        assert unit._recall_injected is True, (
            "an internal recall failure must latch the guard to avoid re-running the "
            "broken path every turn"
        )

    @pytest.mark.asyncio
    async def test_detect_exception_does_not_propagate(self):
        """A3: an exception in the shared detection itself is contained — recall
        degrades to empty, core context intact, no raise."""
        from core.session_router import _maybe_inject_recall

        options = _make_options()
        core = options.system_prompt
        with patch("core.recall_multi.detect_active_project",
                   side_effect=RuntimeError("iterdir blew up")), \
             patch("core.session_router._unified_recall_body", return_value=("", None)), \
             patch("core.session_router._recall_for_query", return_value=""):
            # Must NOT raise even though detection is the very first shared step.
            await _maybe_inject_recall(
                user_message="how does recall detection resolve the active project",
                options=options,
                unit=_make_unit(),
                editor_context={"file_path": "/x/Projects/SwarmAI/2-understanding/TECH.md"},
            )

        # Contract: no raise (asserted by reaching here) + core context intact.
        # Recall itself may still run with no project (keywords present) and append
        # an empty-match nudge — that is correct graceful degradation, not corruption.
        # What must hold: the core prompt remains the intact prefix.
        assert options.system_prompt.startswith(core), (
            "a detection failure must never crash the builder or corrupt core context "
            "— core must remain the intact prefix"
        )


class TestTriggerTimingPreserved:
    """PRESERVED: the DDD leg still fires pre-keyword-gate (signal-1 zero-keyword)."""

    @pytest.mark.asyncio
    async def test_zero_keyword_opener_with_editor_still_injects_ddd(self):
        """A zero-keyword opener ("继续") + an editor file in Projects/<X>/ must STILL
        trigger DDD injection via signal-1 — the refactor shares the detection RESULT
        but must NOT move the DDD trigger behind the keyword gate."""
        from core.session_router import _maybe_inject_recall

        unit = _make_unit()
        injected = {"ddd": False}

        def _fake_ddd(options, user_message, editor_file_path):
            injected["ddd"] = True

        with patch("core.session_router._inject_ddd_for_active_project", side_effect=_fake_ddd), \
             patch("core.session_router._unified_recall_body", return_value=("", None)), \
             patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="继续",  # zero technical keywords
                options=_make_options(),
                unit=unit,
                editor_context={"file_path": "/x/Projects/SwarmAI/2-understanding/TECH.md"},
            )

        assert injected["ddd"] is True, (
            "signal-1 (editor file path) must still fire the DDD leg on a zero-keyword "
            "opener — the pre-keyword-gate trigger timing is preserved"
        )
