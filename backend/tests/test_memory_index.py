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
from datetime import datetime, timedelta

import pytest


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


class TestGenerateMemoryIndex:
    """L0 compact index generation with value-based tiers."""

    def test_generates_index_without_markers(self):
        """generate_memory_index returns content only — no markers."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "<!-- MEMORY_INDEX_START -->" not in index
        assert "<!-- MEMORY_INDEX_END -->" not in index
        assert "## Memory Index" in index

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
        """Index entries should have stable keys like [COE01], [DEC01], [GUI01]."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "[COE" in index
        assert "[DEC" in index
        assert "[GUI" in index

    def test_open_threads_in_index(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        assert "Signal fetcher" in index or "[OT" in index

    def test_open_threads_indexed_exactly_once(self):
        """Each OTxx must appear exactly ONCE in the index.

        Regression for the double-list bug: Open Threads is in
        MEMORY_ACTIVE_SECTIONS, so the general active_lines loop emitted every
        OT entry AND the dedicated ot_lines block emitted it again — every
        OTxx appeared twice. The dedicated ot_lines block is the canonical one
        (it filters ✅-resolved entries, which the active loop does not), so
        Open Threads must be excluded from the active_lines scan.

        Mutation check: re-add "Open Threads" to _active_scan in
        generate_memory_index and this test goes RED (each OT id == 2).
        """
        import re
        from collections import Counter
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        ot_ids = re.findall(r"^- \[(OT\d+)\]", index, re.M)
        assert ot_ids, "expected at least one [OTxx] entry in the index"
        dupes = {k: v for k, v in Counter(ot_ids).items() if v > 1}
        assert not dupes, f"Open Threads double-listed in index: {dupes}"

    def test_non_ot_sections_still_indexed_after_ot_dedup(self):
        """Excluding Open Threads from active scan must NOT drop other Active
        sections (Guidelines/Pitfalls/Decisions). Guards the blast radius of
        the OT-dedup fix."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        # Active-tier non-OT entries must survive
        assert "[GUI" in index, "Guidelines dropped from index by OT-dedup fix"
        assert "[DEC" in index, "Decisions dropped from index by OT-dedup fix"
        # And OT entries are still present (just once — via the dedicated block)
        assert "[OT" in index, "Open Threads entirely missing after dedup fix"

    def test_counts_header(self):
        """Index should have a summary count line."""
        from core.memory_index import generate_memory_index

        index = generate_memory_index(SAMPLE_MEMORY)
        # The count header is line[1] (e.g. "2 principles | 2 decisions | ...").
        # Assert against THAT line specifically — not the whole index — so the
        # hardcoded tier labels ("### Permanent (COEs + Architectural Decisions)")
        # can't satisfy this vacuously.
        count_line = index.splitlines()[1]
        assert "|" in count_line, f"expected pipe-delimited counts, got: {count_line!r}"
        # at least one "<N> <section>" pair
        import re
        assert re.search(r"\d+\s+\w+", count_line), f"no numeric counts in: {count_line!r}"

    def test_empty_memory_returns_minimal_index(self):
        from core.memory_index import generate_memory_index

        index = generate_memory_index("# Memory\n\n## Recent Context\n\n## Key Decisions\n")
        assert "## Memory Index" in index
        assert "<!-- MEMORY_INDEX_START -->" not in index

    def test_idempotent_regeneration(self):
        """Running generate twice on content that already has an index should produce same result."""
        from core.memory_index import generate_memory_index

        index1 = generate_memory_index(SAMPLE_MEMORY)
        # Inject index into memory, then regenerate
        memory_with_index = index1 + "\n\n" + SAMPLE_MEMORY
        index2 = generate_memory_index(memory_with_index)
        assert index1 == index2

    # ── D1 (run_4341fc50): index summary is a POINTER, not a 162-char copy ──
    # Cold-start cost: the whole index block is injected verbatim (select_memory
    # _sections L0). Long-prose entry titles duplicated the body. Cap the SUMMARY
    # segment; KEEP the alias tail (keyword_relevance scores on it at 1.5x).

    _LONG_PROSE_MEMORY = textwrap.dedent("""\
        # Memory

        ## Pitfalls

        - 2026-06-28: **Gate-2 cross-tab attack found a real but BOUNDED risk in the background-tab one-second timer and the right structural call was R25 threading isActive through five files** — the bounded leak-free tick removes less regression surface than the fix adds, so we kept the tick.

        ## Guidelines

        - 2026-06-27: **short one** — tiny.
    """)

    def test_summary_capped_at_title_cap(self):
        """A long-prose entry's index SUMMARY segment is capped (pointer, not
        full copy). RED before the cap exists: the summary is ~200+ chars.

        Mutation check: remove the cap in generate_memory_index and the longest
        index line's summary exceeds MEMORY_INDEX_TITLE_CAP → this goes RED.
        """
        from core.memory_index import (
            generate_memory_index,
            MEMORY_INDEX_TITLE_CAP,
        )

        index = generate_memory_index(self._LONG_PROSE_MEMORY)
        for line in index.splitlines():
            if not line.startswith("- ["):
                continue
            # summary = the segment between '] ' and the first ' | ' (or EOL)
            after_key = line.split("] ", 1)[1]
            summary = after_key.split(" | ", 1)[0]
            # strip an optional leading 'YYYY-MM-DD ' date prefix
            import re as _re
            summary = _re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", summary)
            assert len(summary) <= MEMORY_INDEX_TITLE_CAP, (
                f"index summary not capped: {len(summary)} chars > "
                f"{MEMORY_INDEX_TITLE_CAP}: {summary!r}"
            )
        # Fixed-bound backstop: the cap must stay a real pointer length, not be
        # silently raised to defeat its purpose. Independent of the constant so a
        # constant-inflation regression is also caught.
        assert MEMORY_INDEX_TITLE_CAP <= 90, (
            f"MEMORY_INDEX_TITLE_CAP inflated to {MEMORY_INDEX_TITLE_CAP} — "
            "the index is no longer a pointer"
        )

    def test_capped_summary_preserves_aliases_for_parse_back(self):
        """Capping the summary must NOT drop the '| aliases' tail — recall's
        _parse_index_entries must still recover the same aliases (the 1.5x
        keyword_relevance signal). RED-proof: if the cap eats the tail, aliases
        come back empty."""
        from core.memory_index import (
            generate_memory_index,
            _parse_index_entries,
            MEMORY_INDEX_START,
            MEMORY_INDEX_END,
        )

        raw = generate_memory_index(self._LONG_PROSE_MEMORY)
        block = MEMORY_INDEX_START + "\n" + raw + "\n" + MEMORY_INDEX_END
        entries = _parse_index_entries(block)
        assert entries, "no index entries parsed back"
        # The long-prose pitfall entry must still yield >=1 alias keyword
        # (aliases are extracted from the full entry text, not the capped title).
        long_entry = max(entries, key=lambda e: len(e["summary"]))
        assert long_entry["aliases"], (
            "aliases lost after summary cap — keyword section-selection broken"
        )

    def test_cap_does_not_split_cjk_codepoint(self):
        """Capping operates on str codepoints (not bytes) so a CJK char is never
        cut mid-sequence. Guards the heavy-CJK MEMORY.md."""
        from core.memory_index import generate_memory_index

        cjk = textwrap.dedent("""\
            # Memory

            ## Guidelines

            - 2026-06-28: **这是一条很长的中文经验条目用来测试截断逻辑是否会把一个多字节的中文字符从中间切断导致乱码必须保证按码点截断而不是按字节截断这一点非常重要** — desc.
        """)
        index = generate_memory_index(cjk)  # must not raise / produce mojibake
        # round-trips as valid utf-8 (no surrogate / partial codepoint)
        index.encode("utf-8").decode("utf-8")


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
        # Should have Open Threads but not a full content section
        assert "Signal fetcher" in result or "Open Threads" in result
        # Should NOT have a full "## Decisions" section loaded
        assert "## Decisions" not in result

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
        # Full injection: all body section headers present (assert on "## " headers
        # so the hardcoded index tier labels can't satisfy these vacuously)
        assert "## Decisions" in result
        assert "## Guidelines" in result
        assert "## Principles" in result
        assert "## Open Threads" in result

    def test_selective_injection_runs_session_recall(self, monkeypatch, tmp_path):
        """Regression (run_edfad326): SessionRecall MUST be injected in the
        selective branch. A dead `from core.app_config_manager import
        app_config_manager` (the symbol doesn't exist) used to raise ImportError
        that the bare-except swallowed, silently killing the whole block — recall
        was never injected in prod. This forces the block to execute and asserts
        the recall text appears. Goes RED if the dead import returns.

        Mocks only the boundaries: _get_session_recall (so no real DB) + a
        DB_PATH that exists. Drives the REAL select_memory_sections selective
        path with a genuine >30K fixture."""
        import core.memory_index as mi
        from core.context_directory_loader import ContextDirectoryLoader

        # >30K fixture so the selective branch runs (full injection skips recall).
        big = ("word " * 18000).strip()
        pit = ("pit " * 18000).strip()
        mem = (
            "<!-- MEMORY_INDEX_START -->\n## Memory Index\n"
            "- [DEC01] caching prefix decision | caching, prefix\n"
            "<!-- MEMORY_INDEX_END -->\n\n## Open Threads\n- P0 alpha\n"
            "\n## Decisions\n- [DEC01] caching prefix — " + ("d " * 200) +
            "\n\n## Guidelines\n- [GUI01] g — " + big +
            "\n\n## Pitfalls\n- [PIT01] p — " + pit + "\n"
        )
        assert ContextDirectoryLoader.estimate_tokens(mem) >= mi.FULL_INJECTION_THRESHOLD, \
            "fixture must exceed threshold to exercise the selective branch"

        SENTINEL = "## Session Recall\n\nSENTINEL_RECALL_INJECTED"

        class _FakeRecall:
            def recall_about(self, topic, max_sessions=2, budget_chars=3000):
                return SENTINEL

        # DB_PATH must exist for the block; point it at a real tmp file.
        db = tmp_path / "data.db"
        db.write_text("")
        monkeypatch.setattr("jobs.paths.DB_PATH", db)
        monkeypatch.setattr(mi, "_get_session_recall", lambda _p: _FakeRecall())

        out = mi.select_memory_sections(
            memory_content=mem, user_message="caching prefix",
            session_signals={}, memory_embeddings=False,
        )
        assert "Not loaded" in out, "fixture did not take the selective branch"
        assert "SENTINEL_RECALL_INJECTED" in out, \
            "SessionRecall was NOT injected — the block is dead (dead import regressed?)"


# ── Integration: Index in MEMORY.md ──────────────────────────────────


class TestIndexInMemoryFile:
    """Index block management within MEMORY.md content."""

    def test_inject_index_into_memory(self):
        from core.memory_index import inject_index_into_memory

        result = inject_index_into_memory(SAMPLE_MEMORY)
        assert "<!-- MEMORY_INDEX_START -->" in result
        assert "<!-- MEMORY_INDEX_END -->" in result
        # Original content preserved after index
        assert "## Decisions" in result

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
        assert "## Decisions" in body

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


class TestParseIndexEntriesRefs:
    """Tests for _parse_index_entries handling of refs field."""

    def test_refs_excluded_from_aliases(self):
        """Entries with | refs: X | keywords should NOT include refs in aliases."""
        from core.memory_index import _parse_index_entries

        index_block = (
            "- [KD01] 2026-05-01 Some decision | refs: COE01, COE02 | keyword1, keyword2\n"
            "- [RC01] 2026-05-01 Recent context | alias1, alias2\n"
        )
        entries = _parse_index_entries(index_block)
        assert len(entries) == 2

        kd01 = entries[0]
        assert kd01["key"] == "KD01"
        # refs: should NOT appear in aliases
        assert "refs: COE01" not in kd01["aliases"]
        assert "COE01" not in kd01["aliases"]
        assert "COE02" not in kd01["aliases"]
        # actual keywords SHOULD be there
        assert "keyword1" in kd01["aliases"]
        assert "keyword2" in kd01["aliases"]

        # Entry without refs should work as before
        rc01 = entries[1]
        assert "alias1" in rc01["aliases"]
        assert "alias2" in rc01["aliases"]

    def test_refs_only_no_keywords(self):
        """Entry with only refs and no keywords should have empty aliases."""
        from core.memory_index import _parse_index_entries

        index_block = "- [KD02] Decision title | refs: LL01, RC03\n"
        entries = _parse_index_entries(index_block)
        assert len(entries) == 1
        assert entries[0]["aliases"] == []
        assert "Decision title" in entries[0]["summary"]

    def test_multiple_pipe_segments(self):
        """Entry with refs + keywords separated by pipes parsed correctly."""
        from core.memory_index import _parse_index_entries

        index_block = "- [LL01] 2026-05-01 Lesson title | refs: KD01 | alpha, beta, gamma\n"
        entries = _parse_index_entries(index_block)
        assert len(entries) == 1
        e = entries[0]
        assert "alpha" in e["aliases"]
        assert "beta" in e["aliases"]
        assert "gamma" in e["aliases"]
        assert "KD01" not in e["aliases"]
        assert "refs:" not in " ".join(e["aliases"])


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


class TestCompressAliases:
    """_compress_aliases: safe index-size reduction (run_2f4d92da).

    Removes ONLY provably recall-neutral alias tokens:
      1. Pure-date tokens (20\\d\\d-\\d\\d-\\d\\d) — dropped UNCONDITIONALLY. A
         bare-date alias is measured NOISE at the section-selection layer (a
         bare-date query lights up 6/8 sections; on mixed date+content queries the
         date adds 0 sections beyond content — run_2f4d92da). Auto-recall strips
         dates from the query anyway; true date-scoped recall lives at the
         entry/BM25 body layer, untouched here.
      2. Within-list case-insensitive duplicates (order-preserving).

    DELIBERATELY PRESERVES (M3-skeptic verified as load-bearing recall keys):
      - run_xxx ids (443 entries carry them; they ARE live recall query keys —
        `run_002eca4c` is a real, reachable query that must still hit)
      - every non-date, non-duplicate token, including title-recovery tokens
        added by _recall_safe_aliases and CJK phrases.
    """

    def test_strips_all_pure_date_tokens(self):
        from core.memory_index import _compress_aliases

        out = _compress_aliases(["2026-06-27", "reconcile", "2026-07-03", "streaming"])
        assert "2026-06-27" not in out
        assert "2026-07-03" not in out
        assert out == ["reconcile", "streaming"]

    def test_strips_date_even_when_only_in_alias(self):
        """Date is section-selection noise regardless of where it lives — a
        date-only alias adds no discriminating recall (run_2f4d92da B decision)."""
        from core.memory_index import _compress_aliases

        out = _compress_aliases(["2026-06-27", "eviction"])
        assert out == ["eviction"]

    def test_date_embedded_in_compound_token_preserved(self):
        """Only a BARE date token is stripped; a date inside a longer token stays."""
        from core.memory_index import _compress_aliases

        out = _compress_aliases(["2026-06-27-fix", "foo"])
        assert out == ["2026-06-27-fix", "foo"]

    def test_preserves_run_ids(self):
        """run-ids are LIVE recall keys — must survive compression (skeptic Hole 1)."""
        from core.memory_index import _compress_aliases

        out = _compress_aliases(["run_002eca4c", "2026-06-27", "eviction"])
        assert "run_002eca4c" in out
        assert "2026-06-27" not in out

    def test_dedup_case_insensitive_order_preserving(self):
        from core.memory_index import _compress_aliases

        out = _compress_aliases(["Reconcile", "streaming", "reconcile", "RECONCILE", "tab"])
        # first spelling wins, order preserved, later case-variants dropped
        assert out == ["Reconcile", "streaming", "tab"]

    def test_preserves_cjk_and_non_date_tokens(self):
        """CJK phrases and ordinary tokens are never dropped (skeptic Hole 2)."""
        from core.memory_index import _compress_aliases

        aliases = ["落地成内部生产系统", "single-writer", "gate-2"]
        out = _compress_aliases(aliases)
        assert out == aliases  # nothing removed — none are dates/dups

    def test_empty_and_all_dates(self):
        from core.memory_index import _compress_aliases

        assert _compress_aliases([]) == []
        # an all-date list collapses to empty (all section-selection noise)
        assert _compress_aliases(["2026-01-01", "2026-01-02"]) == []

    def test_idempotent(self):
        """Calling twice yields the same result (pure function, no state)."""
        from core.memory_index import _compress_aliases

        once = _compress_aliases(["run_abc123", "2026-06-27", "foo", "Foo"])
        twice = _compress_aliases(once)
        assert once == twice == ["run_abc123", "foo"]

    def test_generated_index_drops_date_aliases_keeps_run_ids(self):
        """End-to-end: generate_memory_index emits no bare-date alias tokens,
        but still emits run-id aliases (integration of the helper at all 3 sites)."""
        import re
        from core.memory_index import (
            generate_memory_index,
            extract_index_from_memory,
            MEMORY_INDEX_START,
            MEMORY_INDEX_END,
        )

        raw = generate_memory_index(SAMPLE_MEMORY)
        # Parse each entry line's alias tail (after the last '|' that is not refs).
        date_alias_hits = 0
        for line in raw.splitlines():
            if not line.startswith("- ["):
                continue
            # alias tail = everything after title, excluding a 'refs:' segment
            segs = [s.strip() for s in line.split("|")[1:] if not s.strip().startswith("refs:")]
            for seg in segs:
                for tok in (t.strip() for t in seg.split(",")):
                    if re.fullmatch(r"20\d\d-\d\d-\d\d", tok):
                        date_alias_hits += 1
        assert date_alias_hits == 0, f"date tokens leaked into aliases: {date_alias_hits}"


class TestDuplicateMemoryIndex:
    """F1: extract_body_without_index must strip both marker-wrapped AND bare '## Memory Index' duplicates."""

    def test_bare_memory_index_stripped(self):
        """When MEMORY.md has a marker-wrapped index + a bare '## Memory Index', body extraction removes both."""
        from core.memory_index import extract_body_without_index, MEMORY_INDEX_START, MEMORY_INDEX_END

        content = (
            f"{MEMORY_INDEX_START}\n"
            "## Memory Index\n"
            "19 recent context | 32 key decisions\n"
            "- [RC01] Entry one | keyword1\n"
            f"{MEMORY_INDEX_END}\n\n"
            "## Memory Index\n"
            "18 recent context | 31 key decisions\n"
            "- [RC01] Entry one | keyword1\n\n"
            "# Memory — What I Remember\n\n"
            "## Recent Context\n\n"
            "- 2026-05-03: Real entry\n"
        )
        body = extract_body_without_index(content)
        # Should have NO '## Memory Index' left
        assert body.count("## Memory Index") == 0
        # Should preserve real content
        assert "## Recent Context" in body
        assert "Real entry" in body

    def test_inject_produces_single_index(self):
        """inject_index_into_memory on content with a bare duplicate produces exactly 1 index."""
        from core.memory_index import inject_index_into_memory, MEMORY_INDEX_START

        content_with_bare_dup = (
            "## Memory Index\n"
            "stale index data\n\n"
            "# Memory — What I Remember\n\n"
            "## Recent Context\n\n"
            "- 2026-05-03: Real entry\n"
        )
        result = inject_index_into_memory(content_with_bare_dup)
        assert result.count(MEMORY_INDEX_START) == 1
        assert result.count("## Memory Index") == 1  # only inside the marker block


class TestKeyToSectionLiveSchema:
    """G2: _key_to_section must map the LIVE 7-type schema, not the stale RC/KD/LL map.

    Root-cause bug: _KEY_TO_SECTION hand-maintained only RC/KD/LL/COE while the
    live index uses GUI/PIT/DEC/OT/PRI/MOD/COR/SP/COE — so the pure-keyword leg
    mapped 10/443 entries and returned {} for non-COE queries. The fix derives
    the map from the single source of truth (_REF_PREFIX_TO_SECTION).
    """

    def test_live_prefixes_map_to_real_sections(self):
        from core.memory_index import _key_to_section

        cases = {
            "GUI199": "Guidelines",
            "PIT160": "Pitfalls",
            "DEC39": "Decisions",
            "OT16": "Open Threads",
            "PRI08": "Principles",
            "MOD06": "Models",
            "COR04": "Corrections",
            "SP01": "Standing Preferences",
            "COE10": "COE Registry",
        }
        for key, expected in cases.items():
            assert _key_to_section(key) == expected, f"{key} → {_key_to_section(key)!r}, expected {expected!r}"

    def test_legacy_prefixes_still_map(self):
        """Backward compat: old index entries (KD/RC/LL) must still resolve."""
        from core.memory_index import _key_to_section

        assert _key_to_section("KD01") == "Decisions"
        assert _key_to_section("RC07") == "Guidelines"
        assert _key_to_section("LL22") == "Pitfalls"

    def test_unknown_prefix_returns_none(self):
        from core.memory_index import _key_to_section

        assert _key_to_section("ZZZ99") is None

    def test_keyword_leg_maps_majority_of_live_index(self):
        """The pure-keyword leg must map >=95% of live index entries (was 10/443)."""
        import re
        from core.memory_index import _key_to_section, _parse_index_entries

        mem_path = "/Users/gawan/.swarm-ai/SwarmWS/.context/MEMORY.md"
        try:
            content = open(mem_path).read()
        except FileNotFoundError:
            import pytest
            pytest.skip("live MEMORY.md not present in this environment")
        from core.memory_index import extract_index_from_memory
        idx = extract_index_from_memory(content)
        entries = _parse_index_entries(idx)
        if not entries:
            import pytest
            pytest.skip("no index entries parsed")
        mappable = sum(1 for e in entries if _key_to_section(e["key"]))
        ratio = mappable / len(entries)
        assert ratio >= 0.95, f"only {mappable}/{len(entries)} ({ratio:.1%}) mappable — keyword leg still dead"


class TestSectionNameSignal:
    """run_94e602ad: a query that NAMES a section/category must be able to surface
    that section, even when no per-ENTRY summary shares the query's words. Before
    this, the section title was known (_key_to_section) but never a matchable
    signal, so a category-naming query ("what cognitive PRINCIPLES govern
    judgment") scored 0 across all entries → context_files recall@5 = 0.00.

    Layer-1 fix (in _keyword_section_scores): additively score the query against
    each present section's own NAME and max-merge. Additive = can only RAISE a
    section's score, never drop/reorder an existing summary/alias match.

    NOTE (Gate-1 BLOCK): the layer-2 slicer carve-out (return head entries for a
    name-matched section) was REJECTED — a name match proves the SECTION is
    relevant but says nothing about which ENTRIES answer, so head entries = the
    exact head-position bias context_recall.py:99 forbids. So a name-matched
    section still only surfaces if an entry ALSO matches; pure name-only queries
    (Decisions with no matching entry) remain an honest miss for the deferred
    semantic leg. This test guards LAYER-1 only."""

    def _index(self):
        # a minimal MEMORY-index block: a Principle entry whose SUMMARY shares no
        # words with a "principles" category query.
        return (
            "<!-- MEMORY_INDEX_START -->\n"
            "- [PRI01] The READ path is THE differentiator | read-path, contract\n"
            "- [DEC01] Chose SQLite WAL over Postgres for the local store | sqlite, wal\n"
            "<!-- MEMORY_INDEX_END -->\n"
        )

    def test_category_name_query_scores_its_section(self):
        from core.memory_index import _keyword_section_scores
        scores = _keyword_section_scores("what cognitive principles govern judgment", self._index())
        assert "Principles" in scores, \
            "a query naming the 'principles' category must score the Principles section (name signal)"
        assert scores["Principles"] >= 0.15

    def test_name_signal_is_additive_not_reordering(self):
        """A query matching an ENTRY SUMMARY must keep scoring via the summary —
        the name signal only max-merges, never replaces a stronger summary hit."""
        from core.memory_index import _keyword_section_scores
        # 'read path differentiator' matches PRI01's summary strongly; name signal
        # for 'Principles' shouldn't erase or lower it.
        scores = _keyword_section_scores("the read path is the differentiator", self._index())
        assert scores.get("Principles", 0) >= 0.15, "summary match must survive the name-signal merge"

    def test_unrelated_query_scores_nothing(self):
        """The name signal must not inject sections for a query that names none."""
        from core.memory_index import _keyword_section_scores
        scores = _keyword_section_scores("how do I deploy the kubernetes cluster", self._index())
        assert scores == {} or all(v >= 0.15 for v in scores.values()), \
            "no spurious section from an unrelated query"
