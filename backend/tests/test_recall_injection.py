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

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

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

    @pytest.fixture
    def mock_unit(self):
        unit = MagicMock()
        unit._recall_injected = False
        unit.is_channel_session = False
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
    async def test_empty_keywords_skips_recall(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        original_prompt = mock_options.system_prompt

        await _maybe_inject_recall(
            user_message="hi",
            options=mock_options,
            unit=mock_unit,
        )

        assert mock_options.system_prompt == original_prompt
        assert mock_unit._recall_injected is True

    @pytest.mark.asyncio
    async def test_recall_timeout_does_not_block(self, mock_unit, mock_options):
        from core.session_router import _maybe_inject_recall

        def slow_recall(*args, **kwargs):
            time.sleep(2)  # Way over 150ms timeout
            return "This should never be injected"

        with patch("core.session_router._recall_for_query", side_effect=slow_recall):
            start = time.monotonic()
            await _maybe_inject_recall(
                user_message="Tell me about the architecture",
                options=mock_options,
                unit=mock_unit,
            )
            elapsed = time.monotonic() - start

        # 150ms timeout + thread overhead — must complete well under 500ms
        assert elapsed < 0.5, f"Timeout should have fired at 150ms, but took {elapsed:.1f}s"
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

        original_base = "## Base system prompt content"
        assert mock_options.system_prompt == original_base
        assert mock_unit._recall_injected is True


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
import sqlite3
import tempfile
from pathlib import Path


def _seed_memory_db(db_path: Path) -> None:
    """Create a real memory_entries + memory_vec DB with one keyword-matchable
    entry, so the keyword(FTS5) floor through MemoryRecallStore has data to find.
    Mirrors recall_chain_probe._build_db."""
    import sqlite_vec
    from core.memory_embeddings import MemoryEmbeddingStore

    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        store = MemoryEmbeddingStore(conn)
        store.ensure_tables()
        # Un-embedded (vector=None) → exercises the KEYWORD leg specifically,
        # which is exactly the synchronous floor the fix resurrects.
        store.upsert_entry(
            key="COE05", section="COE Registry",
            title="exit code -9 cascading SIGKILL failure",
            full_text="exit code -9 cascading SIGKILL OOM crash recovery resume",
            keywords=["sigkill", "oom", "crash", "recovery", "resume"],
            embedding=None,
        )
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
        return unit

    @pytest.fixture
    def mock_options(self):
        options = MagicMock()
        options.system_prompt = "## Base system prompt content"
        return options

    @pytest.mark.asyncio
    async def test_sync_path_never_calls_bedrock_embed(self, mock_unit, mock_options):
        """THE load-bearing invariant: the synchronous first-token recall path
        must NEVER invoke the Bedrock embed (430ms warm / 2343ms cold). The bug
        was that the embed WAS on this path, blowing the timeout → zero recall.

        Asserting embed-call-count == 0 is non-vacuous regardless of DB size: it
        proves the architectural guarantee (keyword-only floor, no Bedrock block)
        directly, not via a latency race that a small test DB would mask.
        """
        from core import session_router as sr

        embed_calls = {"n": 0}

        def _counting_slow_embed(_text):
            embed_calls["n"] += 1
            time.sleep(0.5)  # simulate Bedrock; if ever reached on sync path, RED
            return [0.0] * 1024

        with _seeded_db():
            with patch("core.session_router._get_cached_embed_fn",
                       return_value=_counting_slow_embed):
                await sr._maybe_inject_recall(
                    user_message="sigkill oom crash recovery resume",
                    options=mock_options,
                    unit=mock_unit,
                )

        # Keyword floor landed AND Bedrock was never called on the critical path.
        assert mock_unit._recall_injected is True
        assert "Recalled Knowledge" in mock_options.system_prompt
        assert embed_calls["n"] == 0, (
            f"Synchronous recall path called Bedrock embed {embed_calls['n']}x — "
            "it MUST be keyword-only (the original dead-path bug). "
            "_recall_for_query default allow_embed must be False."
        )

    @pytest.mark.asyncio
    async def test_recall_for_query_allow_embed_gates_bedrock(self):
        """Direct contract test: allow_embed=False → 0 embed calls;
        allow_embed=True → embed reachable. Proves the gate is the real switch,
        non-vacuous on any DB size (asserts the call count, not a latency race)."""
        from core import session_router as sr

        embed_calls = {"n": 0}

        def _counting_embed(_text):
            embed_calls["n"] += 1
            return [0.0] * 1024

        with _seeded_db():
            with patch("core.session_router._get_cached_embed_fn",
                       return_value=_counting_embed):
                # Default (sync path) must not embed.
                sr._recall_for_query("sigkill oom crash recovery resume", 8000)
                assert embed_calls["n"] == 0, "allow_embed defaults False but embed was called"

                # Explicit off-path caller may embed.
                embed_calls["n"] = 0
                sr._recall_for_query("sigkill oom crash recovery resume", 8000,
                                     allow_embed=True)
                assert embed_calls["n"] > 0, "allow_embed=True but embed was never reached"

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


# ── M2: vector inject-on-next-turn (run_e9b15722) ──
#
# The vector (semantic) leg runs as a background task off the first-token path;
# its result is injected on the NEXT turn. Gate-1 BLOCK-1: a naive design put the
# injection AFTER `if unit._recall_injected: return` → unreachable on turn 2. These
# tests prove the injection is reachable on turn 2 (called twice on the same unit),
# the background task is tracked + cancellable, and dedupe works.

class TestVectorInjectOnNextTurn:
    @pytest.fixture
    def real_unit(self):
        """A lightweight stand-in with the real unit's vector-recall attrs."""
        class U:
            is_channel_session = False
            _recall_injected = False
            _vector_recall_started = False
            _vector_recall_injected = False
            _pending_vector_recall = None
            _keyword_recall_text = ""
            _vector_recall_task = None
        return U()

    @pytest.fixture
    def opts(self):
        o = MagicMock()
        o.system_prompt = "## Base"
        return o

    @pytest.mark.asyncio
    async def test_pending_vector_injected_on_turn_2(self, real_unit, opts):
        """The BLOCK-1 regression guard: vector recall stored after turn 1 must be
        injected on turn 2 — even though _recall_injected is already True (which
        early-returns the rest of _maybe_inject_recall)."""
        from core import session_router as sr

        # Simulate: turn 1 already happened (recall injected), and the background
        # vector task has since completed and stored a pending result.
        real_unit._recall_injected = True
        real_unit._pending_vector_recall = "VECTOR-ONLY-ENTRY: semantic hit XYZ"

        # Turn 2: _maybe_inject_recall is called again. Even though it early-returns
        # on _recall_injected, the pending vector MUST be injected first.
        await sr._maybe_inject_recall(
            user_message="follow up question", options=opts, unit=real_unit,
        )

        assert "VECTOR-ONLY-ENTRY: semantic hit XYZ" in opts.system_prompt, (
            "pending vector recall was NOT injected on turn 2 — BLOCK-1 regression "
            "(injection unreachable behind the _recall_injected early-return)"
        )
        assert real_unit._vector_recall_injected is True
        assert real_unit._pending_vector_recall is None  # consumed

    @pytest.mark.asyncio
    async def test_pending_vector_injected_once_only(self, real_unit, opts):
        """Idempotent: a second injection attempt must not double-inject."""
        from core import session_router as sr
        real_unit._recall_injected = True
        real_unit._pending_vector_recall = "semantic hit ABC"

        await sr._maybe_inject_recall(user_message="q1", options=opts, unit=real_unit)
        first = opts.system_prompt.count("semantic hit ABC")
        await sr._maybe_inject_recall(user_message="q2", options=opts, unit=real_unit)
        second = opts.system_prompt.count("semantic hit ABC")

        assert first == 1 and second == 1, "vector recall double-injected"

    def test_dedupe_drops_keyword_overlap(self):
        """The vector result must drop lines already in the keyword injection."""
        from core.session_router import _dedupe_recall
        prior = "- entry A: foo\n- entry B: bar"
        new = "- entry A: foo\n- entry C: baz"  # A overlaps, C is vector-only
        out = _dedupe_recall(new, prior)
        assert "entry C: baz" in out
        assert "entry A: foo" not in out  # deduped

    @pytest.mark.asyncio
    async def test_vector_task_started_off_critical_path(self, real_unit, opts):
        """Turn 1 fires the background task; the synchronous path must NOT block
        on it (the task is created, not awaited)."""
        from core import session_router as sr

        # Stub _recall_for_query so the synchronous keyword leg returns fast and
        # the background task is created (we assert it exists, not its result).
        with patch("core.session_router._recall_for_query", return_value="kw hit"):
            await sr._maybe_inject_recall(
                user_message="session lifecycle crash recovery resume",
                options=opts, unit=real_unit,
            )

        assert real_unit._vector_recall_started is True
        assert real_unit._vector_recall_task is not None
        # cleanup the task we started
        real_unit._vector_recall_task.cancel()
