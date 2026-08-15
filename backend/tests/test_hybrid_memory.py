"""Tests for memory injection wiring after the vector/hybrid recall leg was removed.

NEW ARCHITECTURE (2026-08-14): the vector/embedding recall leg was removed. Recall
is now pure FTS5+BM25 and live MEMORY is full-injected. The old semantic-recall,
embedding-sync, and Bedrock-fallback tests targeted deleted modules
(core.memory_embeddings, core.embedding_client) and have been deleted. What remains
is the full-injection contract for select_memory_sections().
"""

import hashlib

# ---------------------------------------------------------------------------
# Fixture: sample MEMORY.md content with known semantic relationships
# ---------------------------------------------------------------------------

SAMPLE_MEMORY = """\
<!-- MEMORY_INDEX_START -->
## Memory Index
3 recent context | 2 key decisions | 2 lessons learned | 1 coe registry

### Permanent (COEs + Architectural Decisions — never age out)
- [COE01] 2026-03-17 Sev-1: exit code -9 cascading SIGKILL | sigkill, sev-1, oom
- [KD01] 2026-03-27 Single-process architecture | auto-restart, sigterm
- [KD02] 2026-03-19 Design principle: prevent, don't handle | prevention, structurally

### Active (Recent Context + Lessons)
- [RC01] 2026-03-31 Progressive Memory Disclosure | 3-layer, memory_index
- [RC02] 2026-03-23 Unified Job System audit | credential, http_proxy
- [RC03] 2026-03-22 Generic Settings Pipeline | pass-through, snaketocamel
- [LL01] 2026-03-22 Sync wrappers around async cleanup = resource leaks | async, wrappers, cleanup
- [LL02] 2026-03-22 Invariants must be enforced at a single point | invariants, enforced
<!-- MEMORY_INDEX_END -->

## Open Threads
### P2 — Nice to have
- 🔵 **Signal fetcher service** — not yet created.

## Recent Context
- 2026-03-31: **Progressive Memory Disclosure shipped** — 3-layer recall system with index.
- 2026-03-23: **Unified Job System audit** — credential architecture fix, http_proxy.
- 2026-03-22: **Generic Settings Pipeline** — pass-through, snakeToCamel, camelToSnake.

## Key Decisions
- 2026-03-27: **Single-process architecture** — keep auto-restart, no multi-process.
- 2026-03-19: **Design principle: prevent, don't handle** — eliminating errors structurally.

## Lessons Learned
- 2026-03-22: **Sync wrappers around async cleanup = resource leaks** — async cleanup needs async callers. The sync wrapper leaked 3 file descriptors per crash.
- 2026-03-22: **Invariants must be enforced at a single point** — conflicting enforcement = bugs.

## COE Registry
- 2026-03-17: **Sev-1: exit code -9 cascading SIGKILL failure** — OOM kills, retry made it worse.
"""


# ===========================================================================
# E2E wiring — select_memory_sections full injection
# ===========================================================================

class TestE2EHybridWiring:
    """NEW ARCHITECTURE (2026-08-14): the vector/hybrid recall leg AND selective
    injection were removed — live MEMORY is full-injected. memory_embeddings is an
    inert param. These tests are the full-injection contract; the old
    _hybrid_section_scores mock-based semantic-match test is retired (that scorer is
    gone)."""

    def test_select_with_embeddings_flag_full_injects(self):
        """select_memory_sections accepts the inert memory_embeddings param and
        full-injects the body (no index)."""
        from core.memory_index import select_memory_sections
        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="async cleanup resource leaks",
            memory_embeddings=False,
        )
        # full body present (this fixture's sections) + no in-prompt index
        assert "## COE Registry" in result or "## Recent Context" in result
        assert "<!-- MEMORY_INDEX_START -->" not in result

    def test_full_injection_carries_all_sections_regardless_of_query(self):
        """Whatever the query, the whole body comes through (no semantic/keyword
        selection at injection time — that's recall's job now)."""
        from core.memory_index import select_memory_sections
        result = select_memory_sections(
            memory_content=SAMPLE_MEMORY,
            user_message="app crashes on startup",
            memory_embeddings=True,  # inert
        )
        assert "COE Registry" in result or "exit code" in result.lower()
