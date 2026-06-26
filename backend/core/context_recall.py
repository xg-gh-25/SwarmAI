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
from dataclasses import dataclass
from typing import Optional

from core.context_directory_loader import ContextDirectoryLoader

logger = logging.getLogger(__name__)

# Hard ceiling on a recall response so it can never re-inject a whole file.
RECALL_MAX_TOKENS = 2000


def _basename_key(name: str) -> str:
    """Normalize a filename to a case-insensitive basename for the privacy gate.

    Strips any directory component (``../MEMORY.md`` → ``memory.md``) and
    lowercases, so neither path-traversal nor case tricks can dodge the gate.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    # Handle both separators regardless of host OS.
    base = PureWindowsPath(PurePosixPath(name).name).name
    return base.casefold()


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
    allow_embed: bool = True,
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

        # Keyword-first (no Bedrock on the hot path — the embed leg is the
        # measured 2.65s/query cost). Reuse the EXACT selective-injection scorer.
        scores = memory_index._keyword_section_scores(query, index_block, superseded)
        hit_layer = "keyword" if scores else "none"

        # Hybrid-on-miss: only when keyword finds nothing do we pay the embed
        # cost. The hybrid (DB-backed) path ranks DB-stored entries → section
        # names; those names may be STALE (MEMORY.md edited after the last embed
        # sync), so the slice loop below verifies each ranked section exists in
        # the LIVE passed string and silently-drops nothing (it falls through to
        # whatever live sections matched). See design DP-v stale-index hazard.
        if not scores:
            try:
                hybrid = memory_index._hybrid_section_scores(query, allow_embed=allow_embed)
            except Exception as exc:  # noqa: BLE001 — hybrid is best-effort; keyword already empty
                logger.debug("hybrid recall escalation failed (best-effort): %s: %s",
                             type(exc).__name__, exc)
                hybrid = {}
            if hybrid:
                scores = hybrid
                hit_layer = "hybrid"
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
        block = f"## {sec_name}\n{body.strip()}"
        block_tokens = ContextDirectoryLoader.estimate_tokens(block)
        if used + block_tokens > RECALL_MAX_TOKENS:
            # Truncate this one block to fit the scoping ceiling, then stop.
            suffix = " […recall truncated]"
            suffix_tokens = ContextDirectoryLoader.estimate_tokens(suffix)
            remaining = RECALL_MAX_TOKENS - used - suffix_tokens
            if remaining > 50:  # only if a useful slice remains
                words = block.split()
                # Conservative word budget; verify against estimate and trim to fit.
                keep = max(0, int(remaining * 3 / 4))
                while keep > 0:
                    candidate = " ".join(words[:keep]) + suffix
                    if used + ContextDirectoryLoader.estimate_tokens(candidate) < RECALL_MAX_TOKENS:
                        parts.append(candidate)
                        chosen.append(sec_name)
                        break
                    keep -= max(1, keep // 10)
            break
        parts.append(block)
        chosen.append(sec_name)
        used += block_tokens

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
