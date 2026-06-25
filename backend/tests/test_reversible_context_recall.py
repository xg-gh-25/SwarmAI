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


# ── AC5: entropy-token boundary guarantee (characterization lock) ──────────

def _stf(name: str, content: str) -> int:
    return ContextDirectoryLoader.estimate_tokens(f"## {name}\n{content}")


def test_ac5_truncation_never_bisects_entropy_tokens():
    """_truncate_section is word-level; entropy tokens (no internal whitespace)
    must never appear sliced. Locks the guarantee against a future regression
    to character-level truncation."""
    loader = ContextDirectoryLoader.__new__(ContextDirectoryLoader)
    ids = [
        "run_a1b2c3d4e5f6",
        "9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddcc",  # 40-hex SHA
        "/Users/gawan/Desktop/SwarmAI-Workspace/swarmai/backend/core/x.py",
    ]
    words = []
    for i in range(200):
        words.append(f"filler{i}")
        if i % 7 == 0:
            words.append(ids[i % 3])
    content = " ".join(words)
    orig = _stf("Test", content)

    id_set = set(ids)
    checks = 0
    for overshoot in range(1, orig, max(1, orig // 40)):
        for direction in ("tail", "head"):
            out = loader._truncate_section(content, "Test", orig, overshoot, direction, _stf)
            checks += 1
            body = re.sub(r"\[Truncated:.*?tokens\]", "", out)
            toks = body.split()
            # Every id that survives must be intact (== full id), never a prefix/suffix slice.
            for full in ids:
                for t in toks:
                    if t != full and (full.startswith(t) or full.endswith(t)) and t not in {f"filler{i}" for i in range(200)}:
                        # a non-filler partial of an id = bisection
                        assert t in id_set, f"entropy token bisected: {t!r} from {full!r} (dir={direction}, overshoot={overshoot})"
    assert checks > 0


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
