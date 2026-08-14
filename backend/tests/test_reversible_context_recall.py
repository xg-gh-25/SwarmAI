"""Tests for Reversible Context Recall (run_9de88af9, Approach B + entropy guard).

Covers:
- AC1: zero-exclusion sessions add zero manifest tokens
- AC2: selective injection emits a NAMED manifest at the tail of MEMORY
- AC3: recall_context returns scoped excluded sections only (<2K tok)
- AC4: recall_context HARD-DENIES policy-excluded files (privacy gate)
- AC5: truncation never bisects entropy tokens (run_/SHA/path) — characterization
       lock proving the word-boundary guarantee of _truncate_section.
"""
from __future__ import annotations

import re

from core.context_directory_loader import ContextDirectoryLoader
from core import memory_index


# ── Fixtures ───────────────────────────────────────────────────────────────

def _small_memory() -> str:
    """A MEMORY.md well under FULL_INJECTION_THRESHOLD → full injection, no manifest."""
    return (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        "- [GUI01] tiny note | 2026-01-01, alpha\n"
        "<!-- MEMORY_INDEX_END -->\n\n"
        "## Guidelines\n- [guideline] one small thing (2026-01-01)\n\n"
        "## Open Threads\n- nothing open\n"
    )


def _large_memory() -> str:
    """A MEMORY.md exceeding FULL_INJECTION_THRESHOLD → selective injection.

    Index references several sections; body is padded so the total exceeds the
    30K-token threshold, forcing selective mode where most sections are excluded.
    """
    pad = ("lorem ipsum dolor sit amet consectetur adipiscing elit " * 60 + "\n")
    # Index keys map to section names via memory_index._KEY_TO_SECTION:
    #   COE→"COE Registry", LL→"Lessons Learned", KD→"Key Decisions", RC→"Recent Context".
    index = (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        "- [COE05] exit code -9 cascading SIGKILL OOM failure | 2026-03-17, sigkill, oom, exit-code\n"
        "- [LL99] some lesson about streaming | 2026-04-01, streaming, render\n"
        "- [KD01] a key decision about caching | 2026-05-01, cache, prefix\n"
        "<!-- MEMORY_INDEX_END -->\n"
    )
    sections = []
    for name, body in [
        ("COE Registry", "COE05 exit code -9 cascading SIGKILL OOM\n" + pad * 40),
        ("Lessons Learned", "streaming render lesson\n" + pad * 40),
        ("Key Decisions", "caching prefix decision\n" + pad * 40),
        ("Open Threads", "- one open thread\n"),
    ]:
        sections.append(f"## {name}\n{body}")
    return index + "\n" + "\n\n".join(sections)


# ── AC5 DELETED 2026-08-14: locked the word-boundary guarantee of the read-line
# `_truncate_section`, which was removed (read-line no longer truncates — size
# governance is the write-side line's job). No read-line truncator remains to guard.


# ── AC1: zero-exclusion sessions add zero manifest tokens ──────────────────

def test_ac1_full_injection_has_no_manifest():
    """Small MEMORY → full injection → no '[Not loaded' or 'sections not loaded' manifest."""
    out = memory_index.select_memory_sections(_small_memory(), user_message="anything")
    assert "Not loaded" not in out
    assert "sections not loaded" not in out
    assert "recall_context" not in out


# ── AC2: selective injection emits a NAMED manifest at the tail ────────────

def test_ac2_selective_injection_emits_named_manifest():
    """Large MEMORY → selective injection → manifest lists excluded section NAMES
    (not just a count) and names the recall path, at the tail of the output."""
    mem = _large_memory()
    # Sanity: this fixture actually triggers selective mode.
    total = ContextDirectoryLoader.estimate_tokens(mem)
    assert total >= memory_index.FULL_INJECTION_THRESHOLD, f"fixture too small: {total}"

    out = memory_index.select_memory_sections(mem, user_message="caching prefix")
    # Manifest must carry NAMES, not only a count.
    assert "Not loaded" in out, "named manifest marker missing"
    # At least one excluded section name appears in the manifest line.
    manifest_line = [ln for ln in out.splitlines() if "Not loaded" in ln]
    assert manifest_line, "no manifest line found"
    line = manifest_line[-1]
    assert any(name in line for name in ("COE Registry", "Lessons Learned", "Key Decisions")), \
        f"manifest has no section names: {line!r}"
    # Recall path named so the agent knows how to retrieve.
    assert "recall_context" in line
    # Tail position: manifest is the last non-empty line of the assembled output.
    non_empty = [ln for ln in out.splitlines() if ln.strip()]
    assert "Not loaded" in non_empty[-1], "manifest is not at the tail"


# ── AC3: recall_context returns scoped excluded sections only (<2K tok) ────

def test_ac3_recall_returns_scoped_section_not_whole_file():
    from core.context_recall import recall_context

    mem = _large_memory()
    res = recall_context("MEMORY.md", "exit code -9 sigkill", memory_content=mem,
                         policy_excluded_files=frozenset(), max_sections=3)
    assert res.allowed is True
    # Returns the relevant excluded section (Pitfalls mentions COE05 exit code -9).
    assert "COE05" in res.content or "exit code" in res.content
    # Scoped: well under 2K tokens, and NOT the whole 30K+ file.
    assert ContextDirectoryLoader.estimate_tokens(res.content) < 2000
    assert len(res.content) < len(mem)


def test_bare_date_query_falls_back_to_body_bm25():
    """run_2f4d92da (B + Gate-2 F1 fix): date aliases are dropped from the index
    as section-selection noise, so a PURE bare-date query scores 0 at the section
    layer. recall_context must FALL BACK to entry-body BM25 (where the date lives)
    rather than return nothing. Simulates the post-refresh index (no date aliases)
    with the date present only in the body."""
    from core.context_recall import recall_context

    pad = ("lorem ipsum dolor sit amet " * 80 + "\n")
    # Index with NO date aliases (the post-compression shape).
    index = (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        "- [COE05] exit code sigkill oom | sigkill, oom, exit-code\n"
        "- [DEC01] a decision about caching | cache, prefix\n"
        "<!-- MEMORY_INDEX_END -->\n"
    )
    # Body carries the date stamp (as real MEMORY entries do).
    mem = index + "\n" + "\n\n".join([
        "## COE Registry\n- 2026-03-17: **COE05 exit code -9** — sigkill oom\n" + pad * 40,
        "## Decisions\n- 2026-05-01: **caching decision** — prefix cache\n" + pad * 40,
        "## Open Threads\n- one open thread\n",
    ])

    # Pure bare-date query: section-selection scores 0 (no date aliases) → fallback.
    res = recall_context("MEMORY.md", "2026-03-17", memory_content=mem, max_sections=3)
    assert res.allowed is True
    assert res.hit_layer == "date_body_fallback"
    assert "2026-03-17" in res.content  # the date-stamped entry surfaced
    assert ContextDirectoryLoader.estimate_tokens(res.content) < 2000  # still scoped

    # Mixed date+content query must NOT trip the fallback — content tokens drive
    # normal section-selection.
    mixed = recall_context("MEMORY.md", "caching 2026-05-01", memory_content=mem)
    assert mixed.hit_layer != "date_body_fallback"

    # A non-date miss must NOT trip the fallback (stays empty, no dump).
    miss = recall_context("MEMORY.md", "zzz_no_such_token_xyz", memory_content=mem)
    assert miss.hit_layer == "none"
    assert miss.content == ""


# ── AC4: recall_context HARD-DENIES policy-excluded files (privacy gate) ───

def test_ac4_recall_denies_policy_excluded_file():
    """RED-first: a group-channel session policy-excludes MEMORY.md/USER.md.
    recall_context MUST deny and leak NO content."""
    from core.context_recall import recall_context

    mem = _large_memory()
    res = recall_context("MEMORY.md", "exit code -9 sigkill", memory_content=mem,
                         policy_excluded_files=frozenset({"MEMORY.md", "USER.md"}),
                         max_sections=3)
    assert res.allowed is False, "policy-excluded file must be denied"
    assert res.content == "", "denied recall must leak zero content"
    assert "COE05" not in (res.content or "")
    assert res.reason  # a denial reason is provided


def test_ac4_recall_denies_nonowner_channel_files():
    from core.context_recall import recall_context

    res = recall_context("EVOLUTION.md", "class A correction", memory_content="## X\nbody\n",
                         policy_excluded_files=frozenset({"EVOLUTION.md", "PROJECTS.md"}),
                         max_sections=3)
    assert res.allowed is False
    assert res.content == ""


# ── AC4 hardening: Gate-2 adversarial findings (CRITICAL 1 + 2) ────────────

def test_ac4_gate_is_case_insensitive():
    """CRITICAL-1: a case variant must NOT bypass the gate (APFS reads same file)."""
    from core.context_recall import recall_context

    mem = _large_memory()
    for variant in ("memory.md", "MEMORY.MD", "Memory.md"):
        res = recall_context(variant, "exit code -9", memory_content=mem,
                             policy_excluded_files=frozenset({"MEMORY.md"}),
                             max_sections=3)
        assert res.allowed is False, f"case variant {variant!r} bypassed the gate"
        assert res.content == ""


def test_ac4_gate_resists_path_traversal_in_recall():
    """CRITICAL-2 (recall layer): a dir-prefixed name normalizes to the basename."""
    from core.context_recall import recall_context

    mem = _large_memory()
    res = recall_context("../MEMORY.md", "exit code -9", memory_content=mem,
                         policy_excluded_files=frozenset({"MEMORY.md"}), max_sections=3)
    assert res.allowed is False
    assert res.content == ""


def test_cli_group_channel_denies_case_and_traversal(tmp_path):
    """CRITICAL-1+2 at the CLI (the production enforcement point)."""
    import json as _json
    import io
    from contextlib import redirect_stdout
    from scripts import context_recall_cli as cli

    # Plant a sensitive file in a fake context dir.
    (tmp_path / "MEMORY.md").write_text(_large_memory(), encoding="utf-8")

    def run(file_arg):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["--file", file_arg, "--query", "exit code -9",
                      "--session-type", "group_channel", "--context-dir", str(tmp_path)])
        return _json.loads(buf.getvalue())

    for variant in ("MEMORY.md", "memory.md", "../MEMORY.md", "./MEMORY.md"):
        out = run(variant)
        assert out["allowed"] is False, f"{variant!r} leaked in group channel"
        assert out["content"] == ""


def test_cli_denies_hardlink_alias_to_policy_file(tmp_path):
    """Gate-2 residual: a hardlink is a 2nd name for the same inode that
    .resolve() can't unmask. The inode-identity gate must still deny it."""
    import json as _json
    import io
    import os
    from contextlib import redirect_stdout
    from scripts import context_recall_cli as cli

    mem = tmp_path / "MEMORY.md"
    mem.write_text(_large_memory(), encoding="utf-8")
    alias = tmp_path / "NOTES.md"
    os.link(mem, alias)  # hardlink: same inode, different name

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(["--file", "NOTES.md", "--query", "exit code -9",
                  "--session-type", "group_channel", "--context-dir", str(tmp_path)])
    out = _json.loads(buf.getvalue())
    assert out["allowed"] is False, "hardlink alias bypassed the privacy gate"
    assert out["content"] == ""


def test_cli_desktop_serves_and_requires_session_type(tmp_path):
    import json as _json
    import io
    import pytest
    from contextlib import redirect_stdout
    from scripts import context_recall_cli as cli

    (tmp_path / "MEMORY.md").write_text(_large_memory(), encoding="utf-8")

    # Desktop: serves (no policy exclusions).
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(["--file", "MEMORY.md", "--query", "exit code -9 sigkill",
                  "--session-type", "desktop", "--context-dir", str(tmp_path)])
    out = _json.loads(buf.getvalue())
    assert out["allowed"] is True

    # MEDIUM-4: session-type is REQUIRED (no permissive default).
    with pytest.raises(SystemExit):
        cli.main(["--file", "MEMORY.md", "--query", "x", "--context-dir", str(tmp_path)])


def test_recall_helper_failure_is_structured_not_crash(monkeypatch):
    """HIGH-3: a helper exception returns a structured result, never a traceback."""
    from core import memory_index
    from core import context_recall

    def boom(*a, **k):
        raise RuntimeError("simulated helper failure")

    monkeypatch.setattr(memory_index, "parse_memory_sections", boom)
    res = context_recall.recall_context("MEMORY.md", "q", memory_content="## A\nbody\n",
                                        policy_excluded_files=frozenset(), max_sections=3)
    assert res.allowed is True
    assert res.content == ""
    assert "recall failed" in res.reason


# ── G1: entry-level recall (run_c1624c89) ──────────────────────────────────
#
# The bug: recall scored SECTIONS, returned the whole section body, then
# front-truncated at RECALL_MAX_TOKENS=2000. A matched entry in the bottom of a
# large section (Guidelines 12K, Pitfalls 14K tok) was dropped by truncation
# even though the section "matched". Fix: rank ENTRIES within the matched
# sections and return the top entries, so the matching entry always surfaces.

def _memory_with_bottom_entry() -> str:
    """A MEMORY.md whose Decisions section has many entries, with the entry that
    matches the query at the BOTTOM (beyond the 2000-tok front-truncation cliff).
    Forces selective mode (>30K) so recall/section-scoring is the live path.
    """
    filler_entries = []
    pad = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod"
    for i in range(120):
        filler_entries.append(
            f"- [decision] **Filler decision {i} about unrelated plumbing** — "
            f"{pad} {pad} (2026-01-{(i % 28) + 1:02d})"
        )
    # The TARGET entry sits at the very bottom of the section body.
    target = (
        "- [decision] **task_budget=800K for desktop, 400K for channels** — "
        "the CLI default is 128K; we override task_budget to 800K on desktop and "
        "400K on channels to avoid premature PreCompact (2026-06-17)"
    )
    decisions_body = "\n".join(filler_entries + [target])
    index = (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        "- [DEC32] task_budget desktop channel token limit | 2026-06-17, task_budget, 800k, 400k, precompact\n"
        "<!-- MEMORY_INDEX_END -->\n"
    )
    return f"{index}\n## Decisions\n{decisions_body}\n\n## Open Threads\n- none\n"


def test_g1_entry_level_surfaces_bottom_matching_entry():
    """A query matching an entry at the BOTTOM of a large section must surface
    that entry — not be dropped by front-truncation of the whole section body."""
    from core import context_recall

    mem = _memory_with_bottom_entry()
    res = context_recall.recall_context(
        "MEMORY.md", "task_budget desktop channel 800K token limit",
        memory_content=mem, allow_embed=False,
    )
    assert res.allowed is True
    assert res.content, "recall returned empty content"
    # The matching entry's distinctive token MUST be present.
    assert "task_budget" in res.content, (
        "matching bottom entry was dropped by truncation — "
        f"content head: {res.content[:200]!r}"
    )
    # And we must NOT have blown the token ceiling to achieve it.
    assert ContextDirectoryLoader.estimate_tokens(res.content) <= context_recall.RECALL_MAX_TOKENS


def test_g1_sections_field_still_carries_section_names():
    """Gate-1 condition C: entry-level slicing changes CONTENT only — the
    `sections` field must still carry SECTION NAMES (callers + probes depend on it)."""
    from core import context_recall

    mem = _memory_with_bottom_entry()
    res = context_recall.recall_context(
        "MEMORY.md", "task_budget desktop channel 800K",
        memory_content=mem, allow_embed=False,
    )
    assert "Decisions" in res.sections, f"sections lost section-name semantics: {res.sections}"
    assert res.drilled is True


def test_g1_no_embed_non_coe_query_not_dead():
    """AC3: allow_embed=False (recall_all anti-scope / Bedrock-down path) for a
    non-COE query must still surface the matching entry — was structurally dead
    because the pure-keyword leg mapped only COE."""
    from core import context_recall

    mem = _memory_with_bottom_entry()
    res = context_recall.recall_context(
        "MEMORY.md", "task_budget desktop channel 800K",
        memory_content=mem, allow_embed=False,
    )
    assert res.hit_layer != "none", "no-embed recall is dead for non-COE query"
    assert "task_budget" in res.content


def test_g1_no_entry_match_skips_section_not_head_bias():
    """Gate-2 Finding A: when a section scores relevant (index keywords) but NO
    entry body shares query vocabulary, the slicer must NOT emit arbitrary head
    entries (re-introducing head-position bias). It returns empty → caller skips."""
    from core import context_recall

    # Decisions section: index line carries the query keywords, but every entry
    # body is about an unrelated topic (zero lexical overlap with the query).
    pad = "networking dns routing subnet gateway packet latency throughput"
    entries = "\n".join(
        f"- [decision] **Unrelated networking note {i}** — {pad} {pad} (2026-02-{(i % 28) + 1:02d})"
        for i in range(80)
    )
    index = (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        "- [DEC32] task_budget desktop channel precompact override | 2026-06-17, task_budget, precompact\n"
        "<!-- MEMORY_INDEX_END -->\n"
    )
    mem = f"{index}\n## Decisions\n{entries}\n\n## Open Threads\n- none\n"

    sliced = context_recall._slice_section_entries(
        "\n".join(
            f"- [decision] **Unrelated networking note {i}** — {pad} {pad}"
            for i in range(80)
        ),
        "task_budget desktop channel precompact",  # zero overlap with bodies
        budget_tokens=1500,
    )
    assert sliced == "", (
        "slicer emitted head entries for a zero-overlap query — head-position "
        f"bias re-introduced: {sliced[:160]!r}"
    )
