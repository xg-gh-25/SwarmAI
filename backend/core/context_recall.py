"""Reversible Context Recall (run_9de88af9, Approach B).

When context assembly EXCLUDES content — selective MEMORY injection (sections
dropped above FULL_INJECTION_THRESHOLD), the DailyActivity 2K cap, or budget
truncation — the agent is told *that* something was excluded (via the named
manifest emitted by ``memory_index.select_memory_sections``) but not given the
content. This module is the retrieval half: given a file and a query, it returns
ONLY the top relevant EXCLUDED sections, reusing the SAME relevance scorer the
selective-injection path already uses. It never returns the whole file.

Origin: ``Knowledge/Designs/2026-06-26-reversible-context-recall-design.md`` —
the CCR (Compressed-Context-with-Retrieval) idea from headroomlabs-ai/headroom,
adapted to SwarmAI's reality (excluded content is not lost; it lives on disk —
the gap is LEGIBILITY + a scoped retrieval path, not storage).

Privacy gate (AC4): some files are excluded by *policy*, not budget — group
channels exclude MEMORY.md/USER.md, non-owner channels exclude EVOLUTION.md/
PROJECTS.md (see ``context_directory_loader.GROUP_CHANNEL_EXCLUDE`` /
``CHANNEL_LIGHT_EXCLUDE``). Recall MUST hard-deny those: the data was withheld
for privacy and recall must never become a bypass. Policy exclusions are passed
in explicitly by the caller so the gate cannot be circumvented by a tool that
doesn't know the session type.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from core.context_directory_loader import ContextDirectoryLoader

logger = logging.getLogger(__name__)

# Hard ceiling on a recall response so it can never re-inject a whole file.
RECALL_MAX_TOKENS = 2000

# A query that is EXACTLY a bare ISO date (optionally surrounded by whitespace).
# Such a query no longer matches at the index section-selection layer (date
# aliases were dropped as noise — run_2f4d92da); it falls back to entry-body BM25
# where the date genuinely lives. Anchored so "reconcile 2026-06-27" (a mixed
# query, which section-selection handles via its content tokens) does NOT trip it.
_BARE_DATE_QUERY_RE = re.compile(r"^\s*20\d\d-\d\d-\d\d\s*$")


def _basename_key(name: str) -> str:
    """Normalize a filename to a case-insensitive basename for the privacy gate.

    Strips any directory component (``../MEMORY.md`` → ``memory.md``) and
    lowercases, so neither path-traversal nor case tricks can dodge the gate.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    # Handle both separators regardless of host OS.
    base = PureWindowsPath(PurePosixPath(name).name).name
    return base.casefold()


def _slice_section_entries(body: str, query: str, budget_tokens: int) -> str:
    """Return the top query-relevant ENTRIES of a section body, within budget.

    The section body is a list of ``- [type] **title** — …`` entries (each may
    span lines: an entry owns its trailing indented ``<!-- ref … -->`` metadata).
    The old behavior returned the whole body front-truncated at the token ceiling,
    which silently dropped a matching entry that sat below the cliff in a large
    section (Guidelines 12K / Pitfalls 14K tok). This ranks the entries against
    the query with the SAME Okapi-BM25 scorer the hybrid leg uses and emits them
    highest-first until the budget is hit — so the matching entry surfaces
    regardless of its position. Works purely on the live string (no DB / embed),
    which is why it also revives the ``allow_embed=False`` path.

    Returns the joined top entries (no trailing truncation marker needed — we add
    whole entries only). If the body has no parseable entries, returns the body
    front-truncated to budget (degrade to old behavior, never crash).
    """
    import re as _re

    from core import memory_index

    # Split on line-anchored entry starts ('- [' at column 0). The lookahead
    # keeps the delimiter, so each chunk is a full entry incl. its metadata line.
    chunks = _re.split(r"(?m)^(?=- \[)", body)
    entries = [c.strip() for c in chunks if c.strip().startswith("- [")]

    if not entries:
        # No discrete entries (free-form section) — degrade to front-truncation.
        words = body.split()
        keep = max(0, int(budget_tokens * 3 / 4))
        while keep > 0:
            cand = " ".join(words[:keep])
            if ContextDirectoryLoader.estimate_tokens(cand) < budget_tokens:
                return cand
            keep -= max(1, keep // 10)
        return ""

    # Rank entries by BM25 against the query (same scorer as the hybrid leg).
    docs = {str(i): e for i, e in enumerate(entries)}
    scores = memory_index._bm25_scores(query, docs)
    if not scores:
        # No entry shares query vocabulary. The SECTION scored relevant (via its
        # index-line keywords or a vector hit), but no individual entry does —
        # so returning arbitrary HEAD entries would re-introduce the exact
        # head-position bias this slicer exists to remove (Gate-2 Finding A,
        # run_c1624c89). Skip the section instead: the caller treats an empty
        # slice as "no entry matched" and continues to the next ranked section,
        # so a genuinely-matching section still surfaces and a falsely-broad one
        # stays silent rather than injecting wrong-but-head content with
        # drilled=True. Precision > coverage on the recall READ path.
        return ""
    order = sorted(docs, key=lambda k: scores.get(k, 0.0), reverse=True)

    # Walk entries highest-rank-first, accumulating whole entries until budget.
    # Measure the ACTUAL joined-output token count each step (estimate_tokens is
    # not additive across the '\n' joins, so a per-entry sum undershoots).
    chosen_keys: list[str] = []
    for k in order:
        candidate_keys = chosen_keys + [k]
        cand_set = set(candidate_keys)
        cand_text = "\n".join(docs[ck] for ck in docs if ck in cand_set)
        if ContextDirectoryLoader.estimate_tokens(cand_text) >= budget_tokens:
            if not chosen_keys:
                # The single top entry alone exceeds budget — front-truncate it
                # so the most-relevant entry still partially surfaces.
                words = docs[k].split()
                keep = max(0, int(budget_tokens * 3 / 4))
                while keep > 0:
                    cand = " ".join(words[:keep]) + " […entry truncated]"
                    if ContextDirectoryLoader.estimate_tokens(cand) <= budget_tokens:
                        return cand
                    keep -= max(1, keep // 10)
                return ""
            break
        chosen_keys.append(k)

    # Display in original on-disk order (ranking chose WHICH entries, not order).
    chosen = set(chosen_keys)
    return "\n".join(docs[k] for k in docs if k in chosen)


@dataclass
class RecallResult:
    """Outcome of a recall_context call.

    allowed=False means the file is policy-excluded (privacy gate); content is
    empty and ``reason`` explains the denial. allowed=True carries the scoped
    matched sections in ``content``.
    """

    allowed: bool
    content: str = ""
    reason: str = ""
    sections: tuple[str, ...] = ()
    # Hit-log surface (run_1e2e663b, §6c) — recall PRODUCES these; ingestion's
    # Darwinian promotion CONSUMES them. recall itself never reads them.
    hit_layer: str = "none"  # "keyword" | "hybrid" | "none"
    drilled: bool = False    # True once a section body was sliced out


def recall_context(
    file: str,
    query: str,
    *,
    memory_content: str,
    policy_excluded_files: frozenset[str] = frozenset(),
    max_sections: int = 3,
    allow_embed: bool = False,  # pure-filesystem: vector leg removed (§5.3); inert, kept for caller compat
) -> RecallResult:
    """Return the top relevant EXCLUDED sections of ``file`` for ``query``.

    Args:
        file: Logical context filename, e.g. ``"MEMORY.md"``.
        query: The agent's natural-language query to score sections against.
        memory_content: Full text of the file (caller reads it from
            ``.context/<file>`` — recall does not re-read it, keeping this pure
            and testable). The content is on disk regardless; recall just scopes
            the slice that comes back.
        policy_excluded_files: Files the current session excludes by POLICY
            (privacy), not budget. If ``file`` is in this set → hard-deny.
        max_sections: Max sections to return (scoping cap).

    Returns:
        RecallResult. allowed=False (empty content) for policy-excluded files;
        otherwise the top-N matched sections, capped at RECALL_MAX_TOKENS.
    """
    # ── AC4: privacy gate (hard-deny, leak nothing) ──
    # CRITICAL: compare case-insensitively. The filesystem (APFS/NTFS) is
    # case-insensitive, so "memory.md" reads the same file as "MEMORY.md" — an
    # exact-string gate would let `recall_context("memory.md", ...)` bypass a
    # policy that excludes "MEMORY.md". Normalize the basename on BOTH sides.
    requested = _basename_key(file)
    denied = {_basename_key(f) for f in policy_excluded_files}
    if requested in denied:
        return RecallResult(
            allowed=False,
            content="",
            reason=(
                f"'{file}' is excluded from this session by policy (privacy) — "
                f"recall is not permitted for policy-excluded files."
            ),
        )

    # Recall is currently MEMORY-shaped (section-structured). Other files fall
    # back to the agent's Read tool; we only special-case the structured store.
    # All helper calls are wrapped: a helper failure must return a structured
    # result (the gate already passed, so allowed=True leaks nothing), never a
    # bare traceback that crashes the agent's Bash call (HIGH-3).
    from core import memory_index

    try:
        sections = memory_index.parse_memory_sections(memory_content)
        if not sections:
            return RecallResult(allowed=True, content="", reason="no sections parsed")

        index_block = memory_index.extract_index_from_memory(memory_content)
        if not index_block:
            index_block = (
                memory_index.MEMORY_INDEX_START
                + "\n"
                + memory_index.generate_memory_index(memory_content)
                + "\n"
                + memory_index.MEMORY_INDEX_END
            )

        superseded = memory_index._extract_superseded_keys(memory_content)

        # Pure keyword (pure-filesystem recall design §3.3/§5.4, 2026-06-28): the
        # vector/hybrid-on-miss leg was REMOVED — no Bedrock/Titan on any recall
        # path. Reuse the EXACT selective-injection BM25 scorer. The synonym blind
        # spot (keyword-miss on a semantically-related entry) is covered by
        # AGENTIC re-search (the caller nudges the agent to re-grep with synonyms),
        # not by a vector leg. ``allow_embed`` is retained in the signature for
        # caller compatibility but is now INERT (no embed path exists to gate).
        # section_name_signal=True: the RECALL read path benefits from a query
        # naming a category (run_94e602ad). SAFE here (unlike injection): recall
        # returns scoped sections to a deliberate recall query, it does not inject
        # into every session's system prompt on an incidental category noun.
        scores = memory_index._keyword_section_scores(
            query, index_block, superseded, section_name_signal=True)
        hit_layer = "keyword" if scores else "none"

        # Bare-date fallback (run_2f4d92da): date aliases were dropped from the
        # index as section-selection noise (a bare-date query used to light up
        # ~5/8 sections — a dump, not recall). So a PURE date query now scores
        # zero at the section layer. But the date DOES live in entry BODY text,
        # where BM25 can match it precisely. When section-selection is empty AND
        # the query is a bare ISO date, rank ALL sections equally and let the
        # entry-level BM25 slice below surface the date-stamped entries — this is
        # strictly better than the old noise-dump, and keeps the index savings.
        if not scores and _BARE_DATE_QUERY_RE.match(query.strip()):
            scores = {name: 1.0 for name in sections}
            hit_layer = "date_body_fallback"
    except Exception as exc:  # noqa: BLE001 — fail-safe: structured result, no leak
        return RecallResult(
            allowed=True, content="",
            reason=f"recall failed: {type(exc).__name__}: {exc}",
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    parts: list[str] = []
    chosen: list[str] = []
    used = 0
    for sec_name, _score in ranked:
        if len(chosen) >= max_sections:
            break
        body = sections.get(sec_name, "")
        if not body.strip():
            # Stale-index guard: a DB-ranked section absent from the live string
            # is skipped (never returned as an empty slice) — the loop continues
            # to the next live section, so a real match still surfaces.
            continue

        remaining = RECALL_MAX_TOKENS - used
        if remaining <= 50:
            break

        # ── G1 (run_c1624c89): ENTRY-level slice, not whole-body-then-truncate.
        # The old path returned the section body front-truncated at the ceiling,
        # which dropped a matching entry sitting below the cliff in a large
        # section (Guidelines 12K / Pitfalls 14K tok). Rank the section's entries
        # against the query and emit the top entries within the remaining budget,
        # so the matching entry surfaces regardless of its on-disk position. The
        # "## <section>" header costs a few tokens; reserve them from the budget.
        header = f"## {sec_name}\n"
        header_tokens = ContextDirectoryLoader.estimate_tokens(header)
        entry_budget = remaining - header_tokens
        if entry_budget <= 50:
            break
        sliced = _slice_section_entries(body.strip(), query, entry_budget)
        if not sliced.strip():
            continue
        block = header + sliced
        parts.append(block)
        chosen.append(sec_name)
        used += ContextDirectoryLoader.estimate_tokens(block)

    return RecallResult(
        allowed=True,
        content="\n\n".join(parts),
        reason="" if parts else "no excluded section matched the query",
        sections=tuple(chosen),
        # A scored-but-undrilled result (all ranked sections stale/empty) still
        # reports its layer, but drilled reflects whether a body was sliced out.
        hit_layer=hit_layer if parts else "none",
        drilled=bool(parts),
    )
