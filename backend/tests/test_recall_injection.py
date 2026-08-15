"""Tests for pre-response recall injection (G3: post-first-message recall).

Verifies that RecallEngine L2/L3 is triggered by the user's actual first
message (not proactive keywords) and injects results into the system prompt
before it reaches the SDK.

Acceptance criteria under test:
  1. First message triggers recall with actual query keywords
  2. Recalled knowledge injected into system prompt
  3. Second+ messages skip recall (once-per-session)
  4. Channel sessions excluded
  5. 150ms timeout — failure never blocks
  6. Chinese queries extract CJK terms correctly
  7. Short/empty messages skip recall
"""

import time
from unittest.mock import MagicMock, patch

import pytest


# ── Keyword extraction tests ─────────────────────────────────────────


class TestExtractQueryKeywords:
    """Test keyword extraction from user messages (no LLM, pure NLP)."""

    def test_extracts_english_keywords(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("How does the evolution pipeline handle corrections?")
        assert "evolution" in result.lower()
        assert "pipeline" in result.lower()
        assert "corrections" in result.lower()

    def test_strips_filler_phrases(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("Hey please can you help me with the recall engine?")
        # Filler stripped, substantive words kept
        assert "recall" in result.lower()
        assert "engine" in result.lower()
        # Filler words should not dominate
        assert "hey" not in result.lower()
        assert "please" not in result.lower()

    def test_extracts_cjk_terms(self):
        from core.session_router import _extract_query_keywords

        result = _extract_query_keywords("帮我看看 OOM 修复的进展")
        # CJK characters preserved
        assert any(ord(c) >= 0x4E00 for c in result), "Should contain CJK chars"
        # English terms also extracted
        assert "oom" in result.lower()

    def test_empty_message_returns_empty(self):
        from core.session_router import _extract_query_keywords

        assert _extract_query_keywords("") == ""

    def test_short_message_returns_empty(self):
        from core.session_router import _extract_query_keywords

        # Messages too short for meaningful recall
        assert _extract_query_keywords("hi") == ""
        assert _extract_query_keywords("ok") == ""

    def test_caps_at_max_terms(self):
        from core.session_router import _extract_query_keywords

        long_msg = " ".join(f"word{i}" for i in range(50))
        result = _extract_query_keywords(long_msg)
        terms = result.split()
        assert len(terms) <= 15, f"Should cap at 15 terms, got {len(terms)}"


# ── Recall injection tests ────────────────────────────────────────────


class TestMaybeInjectRecall:
    """Test the pre-response recall injection hook."""

    @pytest.fixture(autouse=True)
    def _force_recall_fallback_leg(self):
        """Deterministically route recall through the leg these tests mock.

        Runtime recall now tries ``_unified_recall_body`` FIRST (strangler-fig,
        run_ccd1b6c5) and only falls back to ``_recall_for_query`` when the
        unified leg returns empty. Every test below mocks ``_recall_for_query``
        to control the recall result, but NOT the unified leg — so without this
        the unified leg runs against the REAL filesystem, the mocked result is
        bypassed, and the assertions become machine-state-dependent (the
        2026-07-18 GS_RCALL canary staleness: green on one machine, red on
        another). Forcing the unified leg empty makes the code fall through to
        the mocked ``_recall_for_query`` on every run — restoring determinism
        without changing any test's intent. The provenance header, degraded
        counter, and opener-unlatch behaviors under test all live in the shared
        post-recall code, so mutation-proofing is preserved either leg.

        Autouse-safe: tests that never reach recall (channel/opener/second-msg
        early-returns) simply never call the patched leg.
        """
        # _unified_recall_body now returns (body_str, structured_hits|None).
        with patch("core.session_router._unified_recall_body", return_value=("", None)):
            yield

    @pytest.fixture
    def mock_unit(self):
        unit = MagicMock()
        unit._recall_injected = False
        unit.is_channel_session = False
        # Real int (not an auto-Mock) so the recall#5 cap arithmetic works.
        unit._recall_keyword_misses = 0
        return unit

    @pytest.fixture
    def mock_options(self):
        options = MagicMock()
        options.system_prompt = "## Base system prompt content"
        return options

    @pytest.mark.asyncio
    async def test_first_message_triggers_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value="Some recalled knowledge"):
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline work?",
                options=mock_options,
                unit=mock_unit,
            )

        assert "Recalled Knowledge" in mock_options.system_prompt
        assert "Some recalled knowledge" in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recalled_provenance_header(self, mock_unit, mock_options):
        """run_a16d61ad: injected recall MUST carry the [RECALLED] provenance header
        so the model treats keyword-retrieved history as a lead-to-verify, NOT its
        own reasoning (confabulation boundary). Mutation: strip the prefix → RED."""
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value="Some recalled knowledge"):
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline work?",
                options=mock_options,
                unit=mock_unit,
            )

        sp = mock_options.system_prompt
        # The provenance header must prefix the recalled block (not just exist somewhere).
        assert "**[RECALLED]**" in sp
        assert "NOT this turn's reasoning" in sp
        assert "lead to verify" in sp
        # Ordering: header appears BEFORE the recalled content (it's a prefix).
        assert sp.index("**[RECALLED]**") < sp.index("Some recalled knowledge")

    @pytest.mark.asyncio
    async def test_empty_match_stashes_ran_snapshot(self, mock_unit, mock_options):
        """Recall ran and matched NOTHING — the panel must hear ran=True with zero
        hits. Without a stash the endpoint falls back to its ran=False default and
        the panel says "no recall this session", turning a systematic keyword miss
        into "the feature never fired" (review run_abab234c, MED #6)."""
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline work?",
                options=mock_options,
                unit=mock_unit,
            )

        snap = mock_unit._recall_snapshot
        assert isinstance(snap, dict), "empty-match branch must stash a snapshot"
        assert snap["ran"] is True, "the leg DID run — only the match was empty"
        assert snap["hits"] == [], "no hits to report"
        assert snap["body"] == "", "and no rendered body either"
        assert snap["keywords"], "the keywords that missed are the useful signal"
        # The re-search nudge is prompt text, not recalled knowledge.
        assert snap["tokens"] == 0

    @pytest.mark.asyncio
    async def test_second_message_skips_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        mock_unit._recall_injected = True  # Already recalled
        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="Tell me more about corrections",
            options=mock_options,
            unit=mock_unit,
        )

        # Prompt unchanged
        assert mock_options.system_prompt == original_prompt

    @pytest.mark.asyncio
    async def test_channel_session_excluded(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        mock_unit.is_channel_session = True
        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="What about the recall engine?",
            options=mock_options,
            unit=mock_unit,
        )

        assert mock_options.system_prompt == original_prompt
        assert mock_unit._recall_injected is True  # Flag still set to prevent retry

    @pytest.mark.asyncio
    async def test_empty_keywords_skips_recall_but_keeps_guard_open(self, mock_unit, mock_options):
        """recall#5 fix (run_a16d61ad): a zero-keyword OPENER skips recall this turn
        but does NOT burn the once-per-session guard. The flag stays False so a later
        substantive message can still trigger recall (see the two-message test below)."""
        from core.session_router import _maybe_inject_recall

        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="hi",
            options=mock_options,
            unit=mock_unit,
        )

        assert mock_options.system_prompt == original_prompt
        # CHANGED CONTRACT: zero-keyword opener leaves the guard OPEN (False) so the
        # next substantive message gets a chance. (Mutation test: revert the fix —
        # restore `unit._recall_injected = True` on this branch — and this flips RED.)
        assert mock_unit._recall_injected is False

    @pytest.mark.asyncio
    async def test_zero_keyword_opener_then_substantive_message_recalls(self, mock_unit, mock_options):
        """recall#5 core fix: opener 'hi' (no keywords) must NOT permanently disable
        recall. A following substantive query in the SAME session still runs recall once.

        This is the RED-on-revert guard for work-stream E: with the old code (which
        set _recall_injected=True on the zero-keyword branch), the second call would
        be short-circuited by the guard and inject nothing → this test goes RED."""
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value="Recalled X"):
            # Turn 1: opener with no extractable keywords.
            await _maybe_inject_recall(
                user_message="hi",
                options=mock_options,
                unit=mock_unit,
            )
            assert mock_unit._recall_injected is False, "opener must not burn the guard"
            assert "Recalled Knowledge" not in mock_options.system_prompt

            # Turn 2: substantive query — recall MUST run now.
            await _maybe_inject_recall(
                user_message="How does the evolution pipeline handle corrections?",
                options=mock_options,
                unit=mock_unit,
            )

        assert "Recalled Knowledge" in mock_options.system_prompt
        assert "Recalled X" in mock_options.system_prompt
        assert mock_unit._recall_injected is True, "guard set True after a real recall"

    @pytest.mark.asyncio
    async def test_zero_keyword_retry_is_bounded_by_cap(self, mock_unit, mock_options):
        """recall#5 CAP: a session that NEVER yields keywords must not re-run the
        extractor forever. After _RECALL_KEYWORD_MISS_CAP keyword-less turns the
        guard latches closed, so subsequent turns short-circuit on the guard.

        Mutation guard: remove the cap (the `if ... >= _RECALL_KEYWORD_MISS_CAP`
        block) and the guard never latches → this test goes RED on the final assert.
        """
        from core.session_router import _maybe_inject_recall, _RECALL_KEYWORD_MISS_CAP

        # Feed CAP keyword-less openers. _extract_query_keywords("hi") → [] each time.
        with patch("core.session_router._recall_for_query", return_value="X"):
            for i in range(_RECALL_KEYWORD_MISS_CAP):
                assert mock_unit._recall_injected is False, f"guard latched too early at turn {i}"
                await _maybe_inject_recall(user_message="hi", options=mock_options, unit=mock_unit)

        # After CAP misses, the guard is latched closed — recall stops retrying.
        assert mock_unit._recall_keyword_misses == _RECALL_KEYWORD_MISS_CAP
        assert mock_unit._recall_injected is True, (
            "guard must latch closed after CAP keyword-less turns (bounds the regex cost)"
        )

    @pytest.mark.asyncio
    async def test_recall_disaster_timeout_bounds_a_hang(self, mock_unit, mock_options, monkeypatch):
        """CORRECTNESS-FIRST (run_4d06640b): the DISASTER cap bounds a recall HANG
        (not a daily latency judge). A recall that hangs past the cap must NOT block
        the turn forever — it returns within ~cap, injects nothing, sets the guard.
        (The OLD 400ms daily-timeout assertion was deleted — it encoded the
        speed-first objective this pipeline reversed.)"""
        from core import session_router as sr

        # Tiny cap for the test; real cap is 8s.
        monkeypatch.setattr(sr, "_RECALL_DISASTER_TIMEOUT_S", 0.1)

        def hang(*args, **kwargs):
            time.sleep(2)  # way past the 0.1s test cap
            return "This should never be injected"

        with patch("core.session_router._recall_for_query", side_effect=hang):
            start = time.monotonic()
            await sr._maybe_inject_recall(
                user_message="Tell me about the architecture",
                options=mock_options,
                unit=mock_unit,
            )
            elapsed = time.monotonic() - start

        # Bounded by the disaster cap (+ thread overhead), not the 2s hang.
        assert elapsed < 1.0, f"Disaster cap should have bounded the hang, took {elapsed:.1f}s"
        assert "never be injected" not in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_exception_does_not_block(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", side_effect=RuntimeError("DB corrupt")):
            # Should not raise
            await _maybe_inject_recall(
                user_message="Check the memory pipeline",
                options=mock_options,
                unit=mock_unit,
            )

        # Prompt unchanged, flag set
        assert "Recalled Knowledge" not in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_empty_result_no_injection(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        with patch("core.session_router._recall_for_query", return_value=""):
            await _maybe_inject_recall(
                user_message="Tell me about something obscure",
                options=mock_options,
                unit=mock_unit,
            )

        # Pure-filesystem recall (DoD6, 2026-06-28): on a keyword no-match we now
        # inject an AGENTIC re-search nudge (not nothing) — the keyword-only blind
        # spot is covered by prompting the agent to re-grep with synonyms. So the
        # prompt GROWS by the hint, and a "## Recalled Knowledge" header appears.
        original_base = "## Base system prompt content"
        assert mock_options.system_prompt != original_base
        assert "## Recalled Knowledge" in mock_options.system_prompt
        assert "synonyms" in mock_options.system_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_empty_result_increments_degraded_counter(self, mock_unit, mock_options):
        """recall#3 fix (run_a16d61ad): 'keyword recall ran but matched nothing' is the
        load-bearing failure mode after the vector leg was retired (synonym miss). It must
        be COUNTED, not just INFO-logged. Mutation test: remove the _record_recall_degraded
        call on the empty branch and this goes RED."""
        from core import session_router as sr

        before = sr._recall_degraded_count.get("empty_with_keywords", 0)
        with patch("core.session_router._recall_for_query", return_value=""):
            await sr._maybe_inject_recall(
                user_message="How does the obscure subsystem handle edge cases?",
                options=mock_options,
                unit=mock_unit,
            )
        after = sr._recall_degraded_count.get("empty_with_keywords", 0)
        assert after == before + 1, (
            "empty-with-keywords recall must increment the degradation counter "
            f"(before={before}, after={after}) — silent synonym-miss is the dead-path class"
        )


# ── REAL-PATH integration (the test that was missing — run_bbd79e84) ──
#
# The mock-based tests above patch _recall_for_query, so they NEVER exercised
# the real timeout-vs-latency reality. Production measured: keyword(FTS5) recall
# = 150-265ms, Bedrock embed = 430ms warm / 2343ms cold. With the old 150ms hard
# timeout, the real path TimeoutError'd EVERY first message → zero knowledge
# injected, while every mocked test passed (test-theater, GUI16/PIT13 class).
#
# These tests drive the REAL _maybe_inject_recall + _recall_for_query (NOT mocked)
# through the real asyncio timeout wrapper, against a REAL seeded sqlite DB (the
# same _build_db / patch("jobs.paths.DB_PATH") pattern recall_chain_probe.py uses).
# The embed leg is a DELAYED STUB simulating Bedrock latency. Non-creds-dependent.

import contextlib
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path


def _seed_memory_db(db_path: Path) -> None:
    """Create a real memory_entries DB with one keyword-matchable entry, so the
    keyword leg through MemoryRecallStore has data to find.

    NEW ARCHITECTURE (2026-08-14): the vector leg (memory_vec / sqlite-vec) was
    removed — recall is pure FTS5+BM25 keyword. This seeds ONLY memory_entries
    (the LIKE-scan table MemoryRecallStore reads), no embedding, no vec table.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_entries (
                key TEXT PRIMARY KEY,
                section TEXT NOT NULL,
                title TEXT NOT NULL,
                full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                keywords TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        full_text = "exit code -9 cascading SIGKILL OOM crash recovery resume"
        conn.execute(
            "INSERT INTO memory_entries (key, section, title, full_text, content_hash, keywords) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "COE05", "COE Registry",
                "exit code -9 cascading SIGKILL failure",
                full_text,
                hashlib.sha256(full_text.encode()).hexdigest(),
                json.dumps(["sigkill", "oom", "crash", "recovery", "resume"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()


@contextlib.contextmanager
def _seeded_db():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "mem.db"
        _seed_memory_db(db)
        # _recall_for_query opens via open_vec_db() → jobs.paths.DB_PATH.
        with patch("jobs.paths.DB_PATH", db), \
                patch("core.vec_db._DEFAULT_DB_PATH", db):
            yield db


class TestRealRecallPathBudget:
    """Drive the REAL recall path (no mock of _recall_for_query) through the
    real timeout wrapper against a real seeded DB. Proves the keyword floor
    lands within budget even when the embed (vector) leg is slow — the reality
    the mocked tests missed."""

    @pytest.fixture
    def mock_unit(self):
        unit = MagicMock()
        unit._recall_injected = False
        unit.is_channel_session = False
        # Real int (not an auto-Mock) so the recall#5 cap arithmetic works.
        unit._recall_keyword_misses = 0
        return unit

    @pytest.fixture
    def mock_options(self):
        options = MagicMock()
        options.system_prompt = "## Base system prompt content"
        return options

    @pytest.mark.asyncio
    async def test_sync_path_is_keyword_only(self, mock_unit, mock_options):
        """PURE-FILESYSTEM (design §3.3/§5.2/§5.4, 2026-06-28): the recall path is
        KEYWORD-ONLY — the Bedrock VECTOR leg was removed entirely (there is no
        embed function left to spy on). Recall still LANDS through the keyword
        floor (MemoryRecallStore), just without embedding.
        """
        from core import session_router as sr

        with _seeded_db():
            await sr._maybe_inject_recall(
                user_message="sigkill oom crash recovery resume",
                options=mock_options,
                unit=mock_unit,
            )

        assert mock_unit._recall_injected is True
        assert "Recalled Knowledge" in mock_options.system_prompt

    @pytest.mark.asyncio
    async def test_memory_entry_recallable_by_keyword(self, mock_unit, mock_options):
        """AC4: a MEMORY entry is recallable by keyword through the real path
        (MemoryRecallStore keyword leg). Proves Memory is wired, not cosmetic."""
        from core import session_router as sr

        with _seeded_db():
            await sr._maybe_inject_recall(
                user_message="sigkill oom crash recovery resume",
                options=mock_options,
                unit=mock_unit,
            )

        assert "Recalled Knowledge" in mock_options.system_prompt
        # The seeded MEMORY entry's content must appear — proves Memory domain
        # contributed via its real keyword leg (not silently swallowed).
        assert "SIGKILL" in mock_options.system_prompt or "sigkill" in mock_options.system_prompt.lower()


# ── Correctness-first recall: loud-on-degradation + disaster cap (run_4d06640b) ──
#
# The recall path runs BOTH legs synchronously to completion. The old 400ms daily
# timeout (which silently dropped recall) is gone; a DISASTER cap only bounds a
# code HANG, and ANY degradation (timeout OR internal failure) is LOUD, never
# silent — silent empty recall is the exact dead-path class that hid for months.

class TestRecallLoudOnDegradation:
    @pytest.fixture
    def mock_unit(self):
        unit = MagicMock()
        unit._recall_injected = False
        unit.is_channel_session = False
        # Real int (not an auto-Mock) so the recall#5 cap arithmetic works.
        unit._recall_keyword_misses = 0
        return unit

    @pytest.fixture
    def mock_options(self):
        o = MagicMock()
        o.system_prompt = "## Base"
        return o

    @pytest.mark.asyncio
    async def test_disaster_timeout_is_loud_not_silent(self, mock_unit, mock_options, monkeypatch):
        """W5/AC3: if recall HANGS past the disaster cap, it must log at ERROR +
        record a metric — NEVER silent. (The old path logged at debug and silently
        returned empty — why recall was dead for months unnoticed.)"""
        from core import session_router as sr

        # Force a hang: _recall_for_query sleeps far past a tiny test cap.
        monkeypatch.setattr(sr, "_RECALL_DISASTER_TIMEOUT_S", 0.05)
        sr._recall_degraded_count.clear()

        def _hang(*a, **k):
            time.sleep(0.5)  # > 0.05s cap → triggers the disaster path
            return "should not arrive"

        errors = []
        monkeypatch.setattr(sr.logger, "error", lambda *a, **k: errors.append((a, k)))

        with patch("core.session_router._recall_for_query", side_effect=_hang):
            await sr._maybe_inject_recall(
                user_message="session lifecycle crash recovery resume",
                options=mock_options, unit=mock_unit,
            )

        # LOUD: error logged + metric incremented; recall NOT injected (it hung).
        assert errors, "disaster timeout did NOT log at ERROR — silent degradation regression"
        assert sr._recall_degraded_count.get("disaster_timeout", 0) == 1
        assert "should not arrive" not in mock_options.system_prompt
        assert mock_unit._recall_injected is True  # guard set, no retry-storm

    @pytest.mark.asyncio
    async def test_internal_failure_is_loud_not_silent(self, monkeypatch):
        """W5: a failure INSIDE _recall_for_query (Bedrock auth, sqlite error) must
        log at WARNING + metric, not the old silent logger.debug + return ''."""
        from core import session_router as sr
        sr._recall_degraded_count.clear()
        warnings = []
        monkeypatch.setattr(sr.logger, "warning", lambda *a, **k: warnings.append((a, k)))

        # Force open_vec_db to raise inside _recall_for_query.
        with patch("core.vec_db.open_vec_db", side_effect=RuntimeError("sqlite boom")):
            out = sr._recall_for_query("sigkill oom crash", 8000, allow_embed=True)

        assert out == ""  # degrades to empty...
        assert warnings, "internal recall failure was SILENT — the dead-path class (W5)"
        assert any("exception" in k for k in sr._recall_degraded_count)


class TestKeywordOnlyOneTurn:
    """Pure-filesystem (design §5.4): recall lands in ONE synchronous turn via the
    KEYWORD leg only — no vector, no next-turn dependency, no embed."""

    @pytest.mark.asyncio
    async def test_keyword_leg_lands_one_turn(self, monkeypatch):
        from core import session_router as sr

        unit = MagicMock(); unit._recall_injected = False; unit.is_channel_session = False
        opts = MagicMock(); opts.system_prompt = "## Base"

        with _seeded_db():
            await sr._maybe_inject_recall(
                user_message="sigkill oom crash recovery resume",
                options=opts, unit=unit,
            )

        # Single turn: recall injected via the keyword floor (no vector leg).
        assert "Recalled Knowledge" in opts.system_prompt
