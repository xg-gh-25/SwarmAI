"""Reversible Context Recall (run_9de88af9, Approach B).

When context assembly EXCLUDES content — channel exclusions (group/non-owner drop
MEMORY/EVOLUTION), the DailyActivity 2K cap, or budget pressure — the agent can
retrieve it on demand. (MEMORY.md is now ALWAYS full-injected — the old selective-
injection exclusion path was removed 2026-08-14; recall is pure FTS5+BM25.) This
module is the retrieval half: given a file and a query, it returns ONLY the top
relevant EXCLUDED sections via the FTS5+BM25 relevance scorer. It never returns the
whole file.

Origin: ``Knowledge/Designs/2026-06-26-reversible-context-recall-design.md`` —
the CCR (Compressed-Context-with-Retrieval) idea from headroomlabs-ai/headroom,
adapted to SwarmAI's reality (excluded content is not lost; it lives on disk —
the gap is LEGIBILITY + a scoped retrieval path, not storage).

Privacy gate (AC4): some files are excluded by *policy*, not budget — group
channels exclude MEMORY.md/USER.md, non-owner channels exclude EVOLUTION.md
(see ``context_directory_loader.GROUP_CHANNEL_EXCLUDE`` /
``CHANNEL_LIGHT_EXCLUDE``). Recall MUST hard-deny those: the data was withheld
for privacy and recall must never become a bypass. Policy exclusions are passed
in explicitly by the caller so the gate cannot be circumvented by a tool that
doesn't know the session type.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from core.context_directory_loader import ContextDirectoryLoader

logger = logging.getLogger(__name__)

# Hard ceiling on a recall response so it can never re-inject a whole file.
RECALL_MAX_TOKENS = 2000

# Entry-type tags recognized as a NARROW no-`- `-prefix entry start (Gate-2 MED,
# run_03fc3441) — MUST mirror ddd_entry_lifecycle.VALID_TYPES. Kept as a local
# literal to avoid an import-time cycle; drift from the canonical list is caught by
# test_reversible_context_recall.test_valid_entry_types_matches_canonical (a real
# equality assertion — the earlier "caught by test_p1_*" claim was false).
VALID_ENTRY_TYPES = ("guideline", "pitfall", "decision", "model", "process",
                     "principle", "correction")

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


def _truncate_at_sentence(text: str, budget_tokens: int, *, mark: bool = False) -> str:
    """Bounded truncation that degrades at a SENTENCE boundary, never mid-word.

    The ONLY truncation on the recall read path (run_03fc3441). It fires only on
    pathological over-budget content (a single giant entry, or a free-form section
    with no entry boundaries) — normal-sized entries are moved WHOLE. It keeps as
    many WHOLE sentences as fit the budget, so semantics degrade gracefully (never
    a half-word / mid-clause cut, honoring "别切断语义" as far as bounding allows).
    ``mark`` appends a visible ``[…truncated]`` so the reader knows more exists.
    """
    if ContextDirectoryLoader.estimate_tokens(text) <= budget_tokens:
        return text
    suffix = " […truncated]" if mark else ""
    budget_for_body = budget_tokens  # suffix accounted per-candidate below

    def _fits(s: str) -> bool:
        return ContextDirectoryLoader.estimate_tokens(s + suffix) <= budget_for_body

    # Sentence terminators: ASCII AND full-width CJK (。！？；…) + newline. Gate-2
    # (run_03fc3441): an ASCII-only class silently DROPPED all Chinese prose (no
    # match → whitespace fallback → space-less CJK → empty), losing a first-class
    # content type on the read path.
    _TERM = ".!?\n。！？；…"
    sentences = re.findall(rf"[^{re.escape(_TERM)}]*[{re.escape(_TERM)}]+|\S[^{re.escape(_TERM)}]*$", text)
    kept: list[str] = []
    for s in sentences:
        if not _fits("".join(kept) + s):
            break
        kept.append(s)
    out = "".join(kept).rstrip()
    if out:
        return out + suffix

    # Even the first sentence overflows. Prefer a whole-word cut (never mid-word);
    # for a space-less run (CJK / one giant token) fall back to a CHARACTER cut so
    # we still return SOMETHING bounded and non-empty. Binary search on length
    # (Gate-2: the old one-unit-at-a-time shrink was O(n²) → multi-second stalls on
    # large punctuation-free sections).
    units = text.split()
    if len(units) > 1:
        # word granularity
        lo, hi, best = 0, len(units), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if _fits(" ".join(units[:mid])):
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        out = " ".join(units[:best])
    else:
        # character granularity (space-less CJK / single giant token)
        lo, hi, best = 0, len(text), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if _fits(text[:mid]):
                best = mid; lo = mid + 1
            else:
                hi = mid - 1
        out = text[:best].rstrip()
    return (out + suffix) if out else ""


def _slice_section_entries(body: str, query: str, budget_tokens: int) -> str:
    """Return the top query-relevant ENTRIES of a section body, within budget.

    The section body is a list of ``- [type] **title** — …`` entries (each may
    span lines: an entry owns its trailing indented ``<!-- ref … -->`` metadata).
    The old behavior returned the whole body front-truncated at the token ceiling,
    which silently dropped a matching entry that sat below the cliff in a large
    section (Guidelines 12K / Pitfalls 14K tok). This ranks the entries against
    the query with the SAME Okapi-BM25 scorer the FTS5 recall leg uses and emits them
    highest-first until the budget is hit — so the matching entry surfaces
    regardless of its position. Works purely on the live string (no DB / embed),
    which is why it also revives the ``allow_embed=False`` path.

    Returns the joined top entries as WHOLE blocks — the normal path never
    truncates an entry (P2, run_03fc3441): entries accumulate highest-rank-first
    until the budget, and a matching entry moves whole. The ONLY truncation left is
    the pathological single-giant-entry / free-form-section case, which stays
    BOUNDED (recall must not dump 30K into a prompt) but degrades at a SENTENCE
    boundary via ``_truncate_at_sentence`` — never a mid-word cut (honors
    "别切断语义" as far as bounding physically allows; O030/P6).
    """
    import re as _re

    from core import memory_index

    # Split on line-anchored entry starts. P1/P8 (run_03fc3441): recall's boundary
    # rule must AGREE with the size-valve's on the REALISTIC entry shapes, without
    # over-splitting an entry's own body. An entry start is:
    #   • a `- [` bullet — the canonical `- [type] **title**` / `- [ID] …` form,
    #     case-INSENSITIVE on the type (the valve lowercases before matching);
    #   • a `- ` bullet whose text leads with an emoji/status glyph — curated
    #     Open-Threads bullets (`- 🔴 **…**`);
    #   • a bare col-0 `[type]`/`[ID]` legacy entry (no `- ` prefix).
    # NARROW on the bracket forms (valid [type] OR ID-shaped [ABC12], never any
    # `[Word`) so a wrapped `[see also](url)` clause cannot false-split. Gate-2 LOW
    # (run_03fc3441): we deliberately do NOT split on a PLAIN `- ` bullet — a col-0
    # `- ` sub-list inside an entry body would orphan without its header. (The valve
    # also splits indented sub-bullets; recall staying col-0-and-typed is strictly
    # SAFER — it keeps entry bodies whole — so the doors agree on real entries and
    # recall never emits a headerless fragment.)
    # Cycle-5 (#5): the ID-lead + type-lead fragments come from the ddd_entry_lifecycle
    # SSOT (shared with the size-valve — P8 same-vocabulary-both-doors); the emoji-lead
    # and the NARROW composition (no plain `- `) are recall-specific and stay local.
    from core.ddd_entry_lifecycle import _ID_LEAD_PAT as _id_lead, _TYPE_LEAD_PAT as _type_lead
    _emoji_lead = r"- [\U0001F300-\U0001FAFF←-⯿]"
    _entry_lead = rf"(?:- (?:{_type_lead}|{_id_lead})|{_emoji_lead}|{_id_lead}|{_type_lead})"
    _start = rf"(?m)^(?={_entry_lead})"
    chunks = _re.split(_start, body)

    def _is_entry(chunk: str) -> bool:
        return bool(_re.match(rf"^{_entry_lead}", chunk.strip()))

    entries = [c.strip() for c in chunks if _is_entry(c)]

    if not entries:
        # No discrete entries (free-form section). Recall MUST stay bounded (never
        # dump a whole 30K+ section into the prompt — that starves every other
        # recall), but P2 (run_03fc3441) also forbids severing semantics mid-word.
        # Reconciliation: truncate at a SENTENCE boundary, not a word count, and
        # mark it — so what surfaces is whole sentences, never a half-word, and the
        # reader knows more exists. (A free-form section has no entry boundaries to
        # move as blocks, so bounded sentence-truncation is the best integrity
        # available here; discrete entries below are moved WHOLE.)
        return _truncate_at_sentence(body.strip(), budget_tokens)

    # Rank entries by BM25 against the query (same scorer as the FTS5 recall leg).
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
                # The single top-ranked entry alone exceeds budget. Two duties
                # collide: recall must stay BOUNDED (never inject a 30K entry that
                # starves other recall) AND P2 (run_03fc3441) forbids severing an
                # entry mid-word. A single entry this large is itself a DATA
                # pathology (distillation + the size-valve bound real entries to a
                # few hundred tokens) — so bounding wins, but we degrade gracefully:
                # truncate at a SENTENCE boundary (whole sentences, never a half-
                # word) and mark it, rather than a raw word-count cut. This is the
                # ONLY truncation left on the read path and it fires only on the
                # pathological single-giant-entry case; every normal-sized entry is
                # returned WHOLE by the accumulation loop above.
                return _truncate_at_sentence(docs[k], budget_tokens, mark=True)
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
    hit_layer: str = "none"  # "keyword" | "date_body_fallback" | "none" (no vector/hybrid — FTS5+BM25 only)
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

        superseded = memory_index._extract_superseded_keys(memory_content)

        # Index-free body-BM25 (unified-retrieval STEP5a, 2026-08-14): section
        # selection scores the ## section BODY directly — the recall read path no
        # longer touches the in-prompt index (which STEP3 stopped injecting and
        # STEP5b deletes). Same scorer as the injection path (STEP1) but
        # include_evergreen=True: recall returns SCOPED sections by query, so an
        # evergreen section (Principles/Corrections/COE) matching the query MUST be
        # reachable — unlike injection, where evergreen is always-injected in full
        # and thus operational-only-scored. body-BM25 is natively cross-language
        # (``_bm25_tokenize`` CJK bigrams), which the old index ``_cjk_match``
        # missed. Pure keyword (pure-filesystem design §3.3/§5.4): the vector leg
        # was removed; the synonym blind spot is covered by AGENTIC re-search, not a
        # vector leg. ``allow_embed`` is retained in the signature but INERT.
        #
        # ACCEPTED TRADEOFF (STEP5a, reverses the run_94e602ad section_name_signal
        # fix ON PURPOSE): body-BM25 has NO section-name boost, so a query naming a
        # category whose token never appears in any entry body (e.g. "what decisions
        # are recorded") now scores 0 where the index-alias path once matched the
        # section name. This is deliberate — index-free unification + cross-language
        # correctness outweigh the narrow category-noun case, which agentic
        # re-search (re-grep with content synonyms) covers. Do NOT re-litigate as a
        # bug: it is the designed cost of retiring the index.
        scores = memory_index._section_body_scores(
            query, sections, superseded, include_evergreen=True)
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
