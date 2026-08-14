"""Tests for Progressive Memory Disclosure — memory_index module.

Tests the 3-layer memory system:
- L0: Index generation with value-based tiers (Permanent/Active/Archived) + keyword aliases
- L1: Keyword relevance scoring with alias boost
- L1: Section selection based on session signals + keyword matching
- Integration: locked_write index regeneration

Key invariants:
- 100% recall coverage: every entry visible in index regardless of age
- COEs and Principles never age out (Permanent tier)
- Open Threads always loaded
- Config flag=False preserves flat injection exactly

Section names + ID prefixes track the current 7-type knowledge ontology in
memory_index.py: Principles→PRI and COE Registry→COE are Permanent; Decisions→DEC,
Guidelines→GUI, Pitfalls→PIT, Models→MOD, Processes→PRC are Active; Open Threads→OT
always loaded. The older Recent Context / Key Decisions / Lessons Learned sections
are no longer scanned — see PERMANENT_SECTIONS / ACTIVE_SECTIONS.
"""

import textwrap



# ── Sample MEMORY.md for testing ──────────────────────────────────────

SAMPLE_MEMORY = textwrap.dedent("""\
    # Memory — What I Remember

    _Curated long-term memory. Distilled from DailyActivity, not raw logs._

    ## Principles

    - 2026-03-19: **Prevent, don't handle** — Prevention > detection > recovery.
    - 2026-03-24: **Self-evolution focus** — Self-evolution and autonomous operation.

    ## Decisions

    - 2026-03-30: **Slack bot users:read scope — BLOCKED.** AWS internal Slack doesn't allow adding custom scopes.
    - 2026-03-27: **Single-process architecture confirmed** — Slack adapter runs in backend process.

    ## Guidelines

    - 2026-03-23: **Two credential chains coexist on this machine** — Claude CLI uses AWS SSO IdC tokens. boto3 uses credential_process. HTTP_PROXY issues. Isengard access blocked.
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




# ── L1: Keyword Relevance Scoring ────────────────────────────────────




# ── L1: Section Selection ────────────────────────────────────────────








# ── Integration: Index in MEMORY.md ──────────────────────────────────






class TestCJKKeywordExtraction:
    """CJK characters should be extracted as keywords, not silently dropped."""

    def test_chinese_entry_produces_keywords(self):
        """Chinese text should produce keyword aliases."""
        from core.memory_index import _extract_keywords

        entry = "2026-05-01: **Memory sovereignty 是第一原则** — 所有记忆必须自主管理"
        keywords = _extract_keywords(entry)
        # Should contain at least some CJK tokens
        assert len(keywords) > 0
        # "Memory" and "sovereignty" should be there
        assert any("memory" in k.lower() for k in keywords)

    def test_mixed_cn_en_entry(self):
        """Mixed Chinese+English entries should capture both."""
        from core.memory_index import _extract_keywords

        entry = "竞品分析陷阱：admiration ≠ need — 看到 OpenClaw 5 层 memory 架构"
        keywords = _extract_keywords(entry)
        assert len(keywords) > 0
        # Should capture English terms
        assert any("admiration" in k.lower() or "openclaw" in k.lower() for k in keywords)

    def test_tokenize_lower_preserves_cjk(self):
        """_tokenize_lower should include CJK tokens, not filter them."""
        from core.memory_index import _tokenize_lower

        tokens = _tokenize_lower("Memory 是护城河 OpenClaw 对比")
        assert len(tokens) > 0
        # Should have "memory" (English)
        assert "memory" in tokens
        # Should have CJK tokens (not empty due to ASCII-only regex)
        assert any(ord(c) > 127 for t in tokens for c in t)










# ── NEW ARCHITECTURE (2026-08-14): live MEMORY is ALWAYS full-injected ──────
class TestFullInjectionArchitecture:
    """Guards the new-architecture invariants (XG, 2026-08-14):
      1. select_memory_sections ALWAYS returns the whole body — no selective mode,
         no keyword scoring, no size threshold.
      2. The in-prompt index is NEVER injected (it was deleted).
      3. Injected output == body (size bounded upstream by the size-valve, not here).
    These replace the retired TestSelectMemorySections / TestEvergreenAlwaysInjected
    / TestOperationalBodyBM25 (which tested the deleted selective machinery)."""

    def test_returns_whole_body_all_sections_present(self):
        from core.memory_index import select_memory_sections
        result = select_memory_sections(memory_content=SAMPLE_MEMORY, user_message="anything")
        # Every body section is present regardless of the query (full injection).
        assert "## Principles" in result
        assert "## Decisions" in result
        assert "## Guidelines" in result
        assert "## COE Registry" in result
        assert "## Open Threads" in result

    def test_query_does_not_change_output(self):
        """No keyword selection: two different queries yield identical output."""
        from core.memory_index import select_memory_sections
        a = select_memory_sections(memory_content=SAMPLE_MEMORY, user_message="credential proxy")
        b = select_memory_sections(memory_content=SAMPLE_MEMORY, user_message="weather zzz nothing")
        assert a == b

    def test_no_index_ever_injected(self):
        """Teeth: the in-prompt index markers must NEVER appear in injected output.
        Re-adding an index prefix to select_memory_sections makes this go RED."""
        from core.memory_index import (
            select_memory_sections, MEMORY_INDEX_START, MEMORY_INDEX_END,
        )
        # even given a legacy file that STILL carries an index block, output strips it
        legacy = MEMORY_INDEX_START + "\n## Memory Index\n- [X] y\n" + MEMORY_INDEX_END + "\n\n" + SAMPLE_MEMORY
        for mem in (SAMPLE_MEMORY, legacy):
            out = select_memory_sections(memory_content=mem, user_message="x")
            assert MEMORY_INDEX_START not in out
            assert MEMORY_INDEX_END not in out
            assert "## Memory Index" not in out

    def test_output_equals_body(self):
        """Injected output == the index-stripped body (no additions, no truncation)."""
        from core.memory_index import select_memory_sections, extract_body_without_index
        out = select_memory_sections(memory_content=SAMPLE_MEMORY, user_message="")
        assert out == extract_body_without_index(SAMPLE_MEMORY).strip() or \
               out == extract_body_without_index(SAMPLE_MEMORY)

    def test_empty_memory_returns_empty(self):
        from core.memory_index import select_memory_sections
        assert select_memory_sections(memory_content="", user_message="x") == ""

    def test_size_valve_is_the_size_lever(self):
        """The size lever lives in the size-valve, not the injector. A huge body is
        returned WHOLE by the injector (bounding happens upstream at archive time)."""
        from core.memory_index import select_memory_sections
        big = "# Memory\n\n## Guidelines\n" + "\n".join(
            f"- [GUI{i:04d}] entry {i} " + "pad " * 40 for i in range(500)
        ) + "\n\n## Open Threads\n- none\n"
        out = select_memory_sections(memory_content=big, user_message="x")
        assert "[GUI0499]" in out  # the whole body comes through, nothing dropped
