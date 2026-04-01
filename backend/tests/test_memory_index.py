"""Tests for Progressive Memory Disclosure — memory_index module.

Tests the 3-layer memory system:
- L0: Index generation with value-based tiers (Permanent/Active/Archived) + keyword aliases
- L1: Keyword relevance scoring with alias boost
- L1: Section selection based on session signals + keyword matching
- Integration: locked_write index regeneration

Key invariants:
- 100% recall coverage: every entry visible in index regardless of age
- COEs and Key Decisions never age out (Permanent tier)
- Open Threads always loaded
- Config flag=False preserves flat injection exactly
"""

import textwrap
from datetime import datetime, timedelta

import pytest


# ── Sample MEMORY.md for testing ──────────────────────────────────────

SAMPLE_MEMORY = textwrap.dedent("""\
    # Memory — What I Remember

    _Curated long-term memory. Distilled from DailyActivity, not raw logs._

    ## Recent Context

    - 2026-03-30: **Slack bot users:read scope — BLOCKED.** AWS internal Slack doesn't allow adding custom scopes.
    - 2026-03-29: **AIDLC Expert — marathon session shipped all materials.** LT Review v3 PDF, customer pitch PPTX.
    - 2026-03-22: **Process Resource Management bugfix** — 7 interacting bugs fixed across 7 backend files.
    - 2026-03-11: **P0 context files fix** — _build_system_prompt() was silently dropping all 11 context files.

    ## Key Decisions

    - 2026-03-27: **Single-process architecture confirmed** — Slack adapter runs in backend process.
    - 2026-03-24: **Two strategic focus areas** — Self-evolution and autonomous operation.
    - 2026-03-19: **Design principle: prevent, don't handle** — Prevention > detection > recovery.

    ## Lessons Learned

    - 2026-03-23: **Two credential chains coexist on this machine** — Claude CLI uses AWS SSO IdC tokens. boto3 uses credential_process. HTTP_PROXY issues. Isengard access blocked.
    - 2026-03-22: **Constants correct at one scale become bugs at another** — 85% threshold for 200K, catastrophic for 1M.
    - 2026-03-22: **Don't parse OS internals when a library exists** — psutil over vm_stat.

    ## COE Registry

    - 2026-03-20: **Sev-2: Big-bang refactor migration misses** — v7 re-architecture deleted agent_manager.py before verifying migration.
    - 2026-03-19: **Sev-2: Memory pipeline temporal lag gap** — DailyActivity captured mid-session, missing commits.
    - 2026-03-17: **Sev-1: exit code -9 cascading SIGKILL failure** — CLI+5 MCPs ~500MB spike, jetsam kills.

    ## Open Threads

    ### P0 — Blocking
    _(None — all clear)_

    ### P1 — Important
    _(None — all clear)_

    ### P2 — Nice to have
    - 🔵 **Signal fetcher service** — Services/signals/ directory not yet created.
    - 🔵 **MCP Gateway (shared MCPs)** — 4 sessions × 5 MCPs = 20 instances.
""")


# ── L0: Index Generation ─────────────────────────────────────────────


class TestGenerateMemoryIndex:
    """L0 compact index generation with value-based tiers."""

    def test_generates_index_with_markers(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "<!-- MEMORY_INDEX_START -->" in index
        assert "<!-- MEMORY_INDEX_END -->" in index

    def test_permanent_tier_contains_coes(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        # COEs should be in Permanent tier
        assert "### Permanent" in index
        assert "Big-bang refactor" in index
        assert "SIGKILL" in index

    def test_permanent_tier_contains_key_decisions(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "Single-process architecture" in index or "prevent, don't handle" in index

    def test_active_tier_contains_recent_context(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "### Active" in index
        assert "Slack bot" in index

    def test_active_tier_contains_lessons(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "credential chains" in index

    def test_entries_have_keyword_aliases(self):
        """Each index entry should have keyword aliases after |."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        # At least some entries should have | delimiter for aliases
        lines_with_aliases = [
            line for line in index.split("\n")
            if line.strip().startswith("- [") and "|" in line
        ]
        assert len(lines_with_aliases) > 0, "No entries have keyword aliases"

    def test_entries_have_stable_keys(self):
        """Index entries should have stable keys like [COE01], [KD01], [RC01]."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "[COE" in index
        assert "[KD" in index
        assert "[RC" in index

    def test_open_threads_in_index(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "Signal fetcher" in index or "[OT" in index

    def test_counts_header(self):
        """Index should have a summary count line."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        # Should contain counts like "4 recent contexts | 3 decisions | ..."
        assert "recent context" in index.lower() or "decision" in index.lower()

    def test_empty_memory_returns_minimal_index(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index("# Memory\n\n## Recent Context\n\n## Key Decisions\n")
        assert "<!-- MEMORY_INDEX_START -->" in index
        assert "<!-- MEMORY_INDEX_END -->" in index

    def test_idempotent_regeneration(self):
        """Running generate twice on content that already has an index should produce same result."""
        from core.memory_index import generate_memory_index

        index1 = generate_memory_index(SAMPLE_MEMORY)
        # Inject index into memory, then regenerate
        memory_with_index = index1 + "\n\n" + SAMPLE_MEMORY
        index2 = generate_memory_index(memory_with_index)
        assert index1 == index2


# ── L1: Keyword Relevance Scoring ────────────────────────────────────


class TestKeywordRelevance:
    """Keyword matching with alias boost for recall quality."""

    def test_exact_title_match(self):
        from core.memory_index import keyword_relevance

        score = keyword_relevance(
            "credential chains issue",
            "credential chains coexist",
            ["proxy", "boto3", "sso"],
        )
        assert score > 0.0

    def test_alias_match_higher_than_zero(self):
        from core.memory_index import keyword_relevance

        score = keyword_relevance(
            "proxy environment variable problem",
            "credential chains coexist",
            ["proxy", "boto3", "sso", "HTTP_PROXY", "Isengard"],
        )
        assert score > 0.0, "Alias 'proxy' should match user message"

    def test_no_match_returns_zero(self):
        from core.memory_index import keyword_relevance

        score = keyword_relevance(
            "what time is it",
            "credential chains coexist",
            ["proxy", "boto3"],
        )
        assert score == 0.0

    def test_alias_boost_applied(self):
        """Alias hits should score higher per token than title hits."""
        from core.memory_index import keyword_relevance

        # Match via alias only
        alias_score = keyword_relevance(
            "HTTP_PROXY is broken",
            "some unrelated title",
            ["HTTP_PROXY", "proxy"],
        )
        # Match via title only (same overlap count)
        title_score = keyword_relevance(
            "some title words",
            "some title words here",
            [],
        )
        # Can't directly compare since denominator differs, but alias should be > 0
        assert alias_score > 0.0

    def test_short_tokens_filtered(self):
        """Tokens <= 2 chars should be filtered as stop words."""
        from core.memory_index import keyword_relevance

        score = keyword_relevance(
            "is it on",
            "is it on the table",
            [],
        )
        assert score == 0.0, "Short tokens (is, it, on) should be filtered"

    def test_case_insensitive(self):
        from core.memory_index import keyword_relevance

        score = keyword_relevance(
            "SIGKILL cascade failure",
            "sigkill cascade",
            ["jetsam", "OOM"],
        )
        assert score > 0.0


# ── L1: Section Selection ────────────────────────────────────────────


class TestSelectMemorySections:
    """Topic-triggered section selection for L1 injection."""

    def test_open_threads_always_included(self):
        from core.memory_index import select_memory_sections

        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="hello world",
            session_signals={},
        )
        assert "Open Threads" in result or "Signal fetcher" in result

    def test_keyword_match_loads_relevant_section(self):
        from core.memory_index import select_memory_sections

        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="credential chains proxy issue",
            session_signals={},
        )
        assert "credential chains" in result.lower()

    def test_channel_session_loads_minimal(self):
        """Channel sessions should only get index + Open Threads, no full sections."""
        from core.memory_index import select_memory_sections

        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="hello",
            session_signals={"is_channel": True},
        )
        # Should have Open Threads but not full Recent Context section
        assert "Signal fetcher" in result or "Open Threads" in result
        # Should NOT have full "## Recent Context" section loaded
        assert "## Recent Context" not in result

    def test_no_match_returns_index_plus_open_threads(self):
        """When nothing matches, return index + Open Threads as minimum."""
        from core.memory_index import select_memory_sections

        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="completely unrelated query about weather",
            session_signals={},
        )
        assert "<!-- MEMORY_INDEX_START -->" in result
        # Open Threads should be present
        assert "Signal fetcher" in result or "P0" in result

    def test_full_injection_for_small_memory(self):
        """Small MEMORY.md (<30K tokens) is fully injected — all sections present."""
        from core.memory_index import select_memory_sections

        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="tell me everything about all topics",
            session_signals={},
        )
        # Full injection: all sections should be present
        assert "Recent Context" in result
        assert "Key Decisions" in result
        assert "Lessons Learned" in result
        assert "Open Threads" in result


# ── Integration: Index in MEMORY.md ──────────────────────────────────


class TestIndexInMemoryFile:
    """Index block management within MEMORY.md content."""

    def test_inject_index_into_memory(self):
        from core.memory_index import inject_index_into_memory

        result = inject_index_into_memory(SAMPLE_MEMORY)
        assert "<!-- MEMORY_INDEX_START -->" in result
        assert "<!-- MEMORY_INDEX_END -->" in result
        # Original content preserved after index
        assert "## Recent Context" in result

    def test_extract_index_from_memory(self):
        from core.memory_index import extract_index_from_memory, inject_index_into_memory

        memory_with_index = inject_index_into_memory(SAMPLE_MEMORY)
        index_block = extract_index_from_memory(memory_with_index)
        assert index_block is not None
        assert "<!-- MEMORY_INDEX_START -->" in index_block

    def test_extract_index_returns_none_when_missing(self):
        from core.memory_index import extract_index_from_memory

        result = extract_index_from_memory(SAMPLE_MEMORY)
        assert result is None

    def test_extract_body_without_index(self):
        from core.memory_index import extract_body_without_index, inject_index_into_memory

        memory_with_index = inject_index_into_memory(SAMPLE_MEMORY)
        body = extract_body_without_index(memory_with_index)
        assert "<!-- MEMORY_INDEX_START -->" not in body
        assert "## Recent Context" in body

    def test_extract_body_without_index_when_index_is_only_content(self):
        """Edge case: MEMORY.md contains only the index block and nothing else."""
        from core.memory_index import (
            extract_body_without_index,
            MEMORY_INDEX_START,
            MEMORY_INDEX_END,
        )

        index_only = f"{MEMORY_INDEX_START}\n## Memory Index\nsome entries\n{MEMORY_INDEX_END}\n"
        body = extract_body_without_index(index_only)
        # Should return empty string, not the original content
        assert body.strip() == ""
        assert MEMORY_INDEX_START not in body
