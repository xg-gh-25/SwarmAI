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






class TestCJKTokenization:
    """CJK characters must be tokenized (not silently dropped) for keyword recall.
    (The old _extract_keywords index-alias tests were removed 2026-08-14 with the
    in-prompt index; _tokenize_lower — the surviving recall tokenizer — is retained.)"""

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


class TestBareIndexStripBelowTitle:
    """run_0f009a75 BUG2: extract_body_without_index must strip a bare `## Memory Index`
    sitting one line below a DOCUMENT TITLE (`# MEMORY\\n\\n## Memory Index`), which the
    old `\\A\\s*`-only anchor missed — WHILE never destroying a real `## Section` header
    above a bare index (the Gate-0 WRONG-FRAME catch) and never touching a `## Memory
    Index` phrase deep inside an entry body (run_3cb6b9ae safety property)."""

    def test_strips_index_below_document_title(self):
        from core.memory_index import extract_body_without_index
        content = "# MEMORY\n\n## Memory Index\n15 principles\n- [X] y\n## Active\n- keep me\n"
        out = extract_body_without_index(content)
        assert "## Memory Index" not in out
        assert "15 principles" not in out
        assert "keep me" in out  # content after the index survives

    def test_strips_index_at_very_top(self):
        from core.memory_index import extract_body_without_index
        content = "## Memory Index\n15 principles\n### Perm\n- x\n## Active\n- keep\n"
        out = extract_body_without_index(content)
        assert "## Memory Index" not in out
        assert "keep" in out

    def test_never_strips_real_section_header_above_bare_index(self):
        # Gate-0 WRONG-FRAME: a `[^\n]*` first-line alt would swallow a real `## Section`
        # header sitting above a bare index. The `#[^#\n]` (single-#) alt must NOT match
        # `## Some Real Entry`, so the real entry survives intact.
        from core.memory_index import extract_body_without_index
        content = "## Some Real Entry\n\n## Memory Index\nfake\n## Another\n- keep\n"
        out = extract_body_without_index(content)
        assert "## Some Real Entry" in out, "a real ## section above a bare index must NEVER be stripped"

    def test_never_strips_index_phrase_deep_in_body(self):
        # run_3cb6b9ae property: a `## Memory Index` appearing INSIDE an entry body lower
        # in the file is untouchable (top-anchored strip only).
        from core.memory_index import extract_body_without_index
        content = "# MEMORY\n\n## Corrections\n- entry mentions ## Memory Index inline\n## Active\n- keep\n"
        out = extract_body_without_index(content)
        assert "## Corrections" in out and "mentions ## Memory Index inline" in out

    def test_plain_non_title_first_line_is_conservative(self):
        # A first line that is neither whitespace nor a single-# title → do NOT strip
        # (conservative: better to leave a bare index than risk eating real content).
        from core.memory_index import extract_body_without_index
        content = "just some prose\n## Memory Index\nidx\n## Active\n- keep\n"
        out = extract_body_without_index(content)
        assert "just some prose" in out and "## Active" in out

    def test_strips_index_with_crlf_line_endings(self):
        # Gate-2 run_0f009a75: CRLF-tolerant. A CRLF MEMORY.md (Windows / git autocrlf)
        # must still have its below-title bare index stripped, and content after it kept.
        from core.memory_index import extract_body_without_index
        content = "# MEMORY\r\n\r\n## Memory Index\r\n15 principles\r\n## Active\r\n- keep me\r\n"
        out = extract_body_without_index(content)
        assert "## Memory Index" not in out
        assert "15 principles" not in out
        assert "keep me" in out


class TestL1FreshnessGitignoredCognitiveFiles:
    """run_0f009a75 BUG1: L1 freshness must catch edits to gitignored cognitive files
    (MEMORY.md/EVOLUTION.md) that `git status` is blind to — via an UNCONDITIONAL mtime
    leg — while NOT perma-missing on per-session runtime-state noise."""

    def test_mtime_leg_catches_source_edit_after_l1(self, tmp_path):
        import time
        from core.context_directory_loader import ContextDirectoryLoader, CONTEXT_FILES, L1_CACHE_FILENAME
        ctx = tmp_path
        for spec in CONTEXT_FILES:
            (ctx / spec.filename).write_text("x")
        l1 = ctx / L1_CACHE_FILENAME
        time.sleep(0.02); l1.write_text("cache")  # L1 newest
        loader = ContextDirectoryLoader(ctx)
        assert loader._is_l1_fresh_uncached(l1) is True, "clean → fresh"
        # edit a gitignored cognitive file AFTER L1 → must be stale (git can't see it)
        time.sleep(0.02); (ctx / "MEMORY.md").write_text("edited")
        assert loader._is_l1_fresh_uncached(l1) is False, "MEMORY edit after L1 → stale (mtime leg)"

    def test_runtime_state_noise_does_not_perma_miss(self, tmp_path):
        import time
        from core.context_directory_loader import ContextDirectoryLoader, CONTEXT_FILES, L1_CACHE_FILENAME
        ctx = tmp_path
        for spec in CONTEXT_FILES:
            (ctx / spec.filename).write_text("x")
        l1 = ctx / L1_CACHE_FILENAME
        time.sleep(0.02); l1.write_text("cache")
        loader = ContextDirectoryLoader(ctx)
        # a per-session runtime-state file (NOT a context source) touched after L1 must
        # NOT make L1 stale — else the cache perma-misses every session.
        time.sleep(0.02); (ctx / ".memory-usage.json").write_text("noise")
        assert loader._is_l1_fresh_uncached(l1) is True, "runtime-state noise must not invalidate L1"
