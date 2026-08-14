"""MEMORY.md parsing + BM25 keyword scoring for recall.

ARCHITECTURE (2026-08-14): live MEMORY.md is ALWAYS fully injected — there is
NO in-prompt index, NO selective/section-scored injection, NO L0/L1 tiering.
The old 3-layer "L0 Compact Index / L1 Section Selection" system and its
generator functions (generate_memory_index / keyword_relevance /
inject_index_into_memory / extract_index_from_memory) were DELETED. Size is
bounded on the WRITE side (distillation caps + the size-valve archiver), not by
injection-time section dropping. Recall is pure FTS5+BM25 over the body +
`.context/*-archive*.md` (the vector leg was removed the same day).

This module now provides the surviving parse/score utilities used by that
recall path.

Public symbols:

- ``parse_memory_sections``        — Split MEMORY.md into named sections
- ``extract_body_without_index``   — Strip a legacy index block (still stripped
                                      on read; no longer generated)
- ``select_memory_sections``       — DEGRADED pass-through (returns the full
                                      body; kept as a contract-lock guard so a
                                      selective-mode regression is caught by tests)
- ``MEMORY_INDEX_START`` / ``MEMORY_INDEX_END`` — legacy block markers (used only
                                      to strip a legacy block, not to generate one)
"""

import re
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

# The index-block markers survive: the in-prompt index is no longer GENERATED
# (2026-08-14), but MEMORY.md files may still carry a legacy block, so
# extract_body_without_index() strips it on every recall/injection read.
MEMORY_INDEX_START = "<!-- MEMORY_INDEX_START -->"
MEMORY_INDEX_END = "<!-- MEMORY_INDEX_END -->"


# ── Section definitions: imported from single source of truth ─────────
from .ddd_entry_lifecycle import (
    MEMORY_PERMANENT_SECTIONS,
    MEMORY_ACTIVE_SECTIONS,
    MEMORY_EVERGREEN_SECTIONS,
    MEMORY_SECTION_NAMES,
    MEMORY_PREFIX_MAP,
    MEMORY_PREFIX_TO_SECTION,
)

# Re-export for backward compat with existing callers
PERMANENT_SECTIONS = MEMORY_PERMANENT_SECTIONS
ACTIVE_SECTIONS = MEMORY_ACTIVE_SECTIONS

# Sections always loaded in L1 regardless of keyword matching (CYCLE 2/7).
# = the MEMORY_EVERGREEN_SECTIONS SSOT (Principles / Corrections / COE Registry /
# Standing Preferences / Open Threads): query-INDEPENDENT judgment that shapes
# every turn, so it is injected in FULL every turn — never keyword-gated or
# budget-skipped. Derived from the schema (NOT a hand-list) so adding an evergreen
# section to MEMORY_SECTIONS auto-updates injection (P8 one-brain consistency).
# Was {"Open Threads"} only — which dropped Principles/Corrections on a keyword
# miss (the cross-language recall gap: a CJK query not matching a principle's
# English body silently omitted the whole Principles section).
ALWAYS_LOAD_SECTIONS = set(MEMORY_EVERGREEN_SECTIONS)

# Inert default retained only as select_memory_sections' now-inert max_tokens
# param default (MEMORY.md is ALWAYS full-injected — 2026-08-14; no selective
# mode). Kept because that function is a live contract-lock guard (its tests
# assert it returns the full body); its signature default must resolve.
DEFAULT_MAX_TOKENS = 50_000

# Prefix patterns: derived from single source of truth
SECTION_KEY_PREFIX = {
    **MEMORY_PREFIX_MAP,
    # Legacy names (backward compat — old MEMORY.md format still parseable)
    "Recent Context": "RC",
    "Key Decisions": "KD",
    "Lessons Learned": "LL",
    "COE Registry": "COE",
}

# Common stop words to filter from keyword matching
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "has", "his", "how",
    "its", "may", "new", "now", "old", "see", "way", "who", "did",
    "get", "let", "say", "she", "too", "use", "with", "this", "that",
    "from", "have", "been", "will", "more", "when", "what", "some",
    "them", "than", "each", "make", "like", "over", "such", "into",
    "just", "also", "back", "after", "only", "come", "made", "find",
    "here", "thing", "many", "well", "about", "which", "their",
    "there", "other", "could", "would", "should", "these", "those",
})


# ── Tokenization ─────────────────────────────────────────────────────

# CJK Unified Ideographs (U+4E00–U+9FFF) — compiled once at module level
_CJK_RE = re.compile(r"[一-鿿]")


def _tokenize_lower(text: str) -> list[str]:
    """Split text into lowercase tokens, filtering short/stop words.

    Unicode-aware: captures CJK characters as word constituents so
    Chinese/Japanese/Korean memory entries participate in keyword matching.
    """
    # \w with re.UNICODE includes CJK; add hyphen for compound terms
    tokens = re.findall(r"[\w\-]+", text.lower(), re.UNICODE)
    # CJK 2-char words (竞品, 周报) are meaningful → min 2.
    # ASCII 2-char tokens (is, at, to, pe) are usually noise → min 3.
    # Exception: ALL-CAPS 2-3 letter abbreviations in the ORIGINAL text
    # (PE, DB, SSE) would be useful, but we've already lowercased.
    # Those abbreviations appear as 3+ char tokens in aliases (e.g. "oom"),
    # so they still match via exact lookup. Acceptable tradeoff.
    return [t for t in tokens if t not in _STOP_WORDS and (
        len(t) > 2 or _CJK_RE.search(t)
    )]


# Mapping from ref prefix → MEMORY.md section name (from single source)
_REF_PREFIX_TO_SECTION: dict[str, str] = {
    **MEMORY_PREFIX_TO_SECTION,
    # Legacy prefixes (backward compat for old index entries)
    "KD": "Decisions",
    "RC": "Guidelines",
    "LL": "Pitfalls",
}




# ── Index Generation ──────────────────────────────────────────────────


def parse_memory_sections(content: str) -> dict[str, str]:
    """Split MEMORY.md content into named sections.

    Returns dict mapping section name (e.g. "Recent Context") to its content.
    Strips the index block if present.
    """
    # Remove existing index block
    body = extract_body_without_index(content)

    sections: dict[str, str] = {}
    # Split on ## headers
    parts = re.split(r"^(##\s+.+)$", body, flags=re.MULTILINE)

    current_name = None
    for part in parts:
        header_match = re.match(r"^##\s+(.+)$", part.strip())
        if header_match:
            current_name = header_match.group(1).strip()
            # Strip common suffixes like " — What I Remember"
            current_name = re.sub(r"\s*[—–-]\s+.*$", "", current_name)
        elif current_name:
            sections[current_name] = part.strip()

    return sections




# ── Keyword Relevance ─────────────────────────────────────────────────




# ── Okapi-BM25 keyword scorer (run_1e2e663b, recall semantic upgrade) ──

# Standard Okapi-BM25 free parameters (Robertson/Sparck-Jones defaults).
BM25_K1 = 1.5  # term-frequency saturation
BM25_B = 0.75  # document-length normalization strength


def _bm25_tokenize(text: str) -> list[str]:
    """Tokenize for BM25: reuse the lexical tokenizer, but expand CJK runs into
    character bigrams so partial CJK matches still score (token-exact BM25 would
    miss '竞品分析陷阱' vs '竞品分析的结论' — they share no whole token, only the
    bigrams 竞品/品分/分析). Mirrors the CJK-flexibility ``keyword_relevance`` has,
    keeping the recall upgrade from REGRESSING bilingual matching.
    """
    out: list[str] = []
    for tok in _tokenize_lower(text):
        if _CJK_RE.search(tok) and len(tok) >= 2:
            # Emit character bigrams for the CJK run (overlapping).
            out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            out.append(tok)
    return out


def _bm25_scores(query: str, docs: dict[str, str]) -> dict[str, float]:
    """Score each doc against the query with Okapi-BM25 + corpus-relative IDF.

    IDF is computed over ``docs`` (the candidate set), so common terms are
    down-weighted relative to THIS corpus — the property the old token-overlap
    leg lacked. Returns raw BM25 scores (>= 0); the caller min-max normalizes
    them to make the leg commensurable with the absolute vector leg (§3.6).

    Args:
        query: The user/recall query.
        docs: Mapping of doc key → doc text (the candidate set).

    Returns:
        Dict key → BM25 score (>= 0). Empty query or empty corpus → {}.
    """
    import math

    if not docs:
        return {}
    q_terms = _bm25_tokenize(query)
    if not q_terms:
        return {}

    doc_tokens = {k: _bm25_tokenize(text) for k, text in docs.items()}
    doc_len = {k: len(toks) for k, toks in doc_tokens.items()}
    n_docs = len(docs)
    avgdl = (sum(doc_len.values()) / n_docs) if n_docs else 0.0

    # Document frequency per query term.
    doc_term_sets = {k: set(toks) for k, toks in doc_tokens.items()}
    q_unique = set(q_terms)
    df: dict[str, int] = {}
    for term in q_unique:
        df[term] = sum(1 for s in doc_term_sets.values() if term in s)

    # IDF: ln(1 + (N - df + 0.5)/(df + 0.5)) — the +1 form keeps IDF >= 0.
    idf: dict[str, float] = {}
    for term in q_unique:
        d = df[term]
        idf[term] = math.log(1.0 + (n_docs - d + 0.5) / (d + 0.5))

    scores: dict[str, float] = {}
    for key, toks in doc_tokens.items():
        if not toks:
            continue
        # term frequency in this doc
        tf: dict[str, int] = {}
        for t in toks:
            if t in q_unique:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        dl = doc_len[key]
        score = 0.0
        for term, f in tf.items():
            denom = f + BM25_K1 * (1.0 - BM25_B + BM25_B * (dl / avgdl if avgdl else 1.0))
            score += idf[term] * (f * (BM25_K1 + 1.0)) / denom
        if score > 0:
            scores[key] = score
    return scores


def _minmax_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalize scores to [0, 1] over the candidate set.

    Makes an unbounded leg (BM25) commensurable with the absolute [0,1] vector
    leg (§3.6). A single-candidate or all-equal set maps to 1.0 (it is the only
    / a co-best option, so it deserves full weight, not 0).
    """
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return {k: 1.0 for k in scores}
    span = hi - lo
    return {k: (v - lo) / span for k, v in scores.items()}


def _normalize_bm25_scores(raw: dict[str, float]) -> dict[str, float]:
    """Make raw Okapi-BM25 scores commensurable with the absolute [0,1] vector
    leg (§3.6), without over-promoting a degenerate candidate set.

    - Spread set (hi > lo): min-max to [0, 1].
    - Degenerate set (single candidate or all-equal): saturation ``s/(s+K1)``
      of the RAW score. A lone WEAK match gets a small score; a lone STRONG
      match approaches 1. This fixes the prior ``→ 1.0`` blanket, which gave a
      weak single candidate full keyword weight (run_aba4f77a F1). K1 is the
      same TF-saturation constant BM25 already uses, so this BM25-aware
      transform lives here — ``_minmax_normalize`` stays a generic normalizer.
    """
    if not raw:
        return {}
    vals = list(raw.values())
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return {k: v / (v + BM25_K1) for k, v in raw.items()}
    return _minmax_normalize(raw)


# ── Section Selection ─────────────────────────────────────────────────






# ── SessionRecall singleton cache ────────────────────────────────────

_session_recall_cache: dict[str, object] = {}  # db_path_str → SessionRecall


def _get_session_recall(db_path: Path) -> object:
    """Return a cached SessionRecall instance for the given DB path.

    Avoids re-creating the object (and its sqlite connections) on every
    ``select_memory_sections()`` call.
    """
    key = str(db_path)
    if key not in _session_recall_cache:
        from core.session_recall import SessionRecall
        _session_recall_cache[key] = SessionRecall(db_path)
    return _session_recall_cache[key]


# ── Key→Section mapping ──────────────────────────────────────────────
#
# Derived from the SINGLE SOURCE OF TRUTH (_REF_PREFIX_TO_SECTION, which itself
# expands ddd_entry_lifecycle.MEMORY_PREFIX_TO_SECTION + the legacy KD/RC/LL
# aliases). The old hand-maintained _KEY_TO_SECTION literal listed only
# RC/KD/LL/COE and drifted: the live index uses GUI/PIT/DEC/OT/PRI/MOD/COR/SP,
# so the pure-keyword leg mapped 10/443 entries and returned {} for non-COE
# queries (run_c1624c89 G2). Deriving from the source-of-truth fixes the root
# cause once and cannot drift again.
_KEY_TO_SECTION = _REF_PREFIX_TO_SECTION


def _key_to_section(key: str) -> Optional[str]:
    """Map an index entry key like 'GUI01' / 'PIT12' / 'COE03' to a section name.

    Longest-prefix match: sort prefixes by length descending so a longer prefix
    (e.g. a hypothetical 'COE') is preferred over a shorter one that is its
    prefix. The current schema has no such collision, but this is cheap
    insurance against a future short prefix shadowing a longer one.
    """
    for prefix in sorted(_KEY_TO_SECTION, key=len, reverse=True):
        if key.startswith(prefix):
            return _KEY_TO_SECTION[prefix]
    return None


def _strip_superseded_entries(body: str, superseded_keys: set[str]) -> str:
    """Remove entry blocks whose key ∈ superseded_keys from a section body.

    Unified-retrieval STEP1: body-BM25 scores a whole ## section, so it has no
    per-entry key to down-weight (unlike the retired index scorer's per-entry
    SUPERSEDED_WEIGHT). Instead we strip a superseded entry's text from the body
    BEFORE scoring — a superseded entry then contributes 0 to its section's BM25
    score (section-body granularity of the same "superseded ≠ recall signal"
    intent). An entry block = its `- [KEY]` line through the line before the next
    top-level `- ` (or section end), incl. its metadata comment lines.
    """
    if not superseded_keys:
        return body
    lines = body.split("\n")
    out: list[str] = []
    skip = False
    for line in lines:
        m = re.match(r"\s*- \[([A-Z]{1,4}\d+)\]", line)
        if m:
            skip = m.group(1) in superseded_keys  # start/stop at each entry boundary
        if not skip:
            out.append(line)
    return "\n".join(out)


def _section_body_scores(
    user_message: str,
    sections: dict[str, str],
    superseded_keys: set[str] | None = None,
    *,
    include_evergreen: bool = False,
) -> dict[str, float]:
    """Score MEMORY ## sections against a query by body-BM25 (index-free).

    Unified-retrieval — the replacement for the old index-based section scorer
    (both it and the in-prompt index were deleted 2026-08-14). Mirrors DDD's
    ``recall_multi._ddd_section_scores`` (the correct, index-free pattern):
    ``_bm25_scores({section: body})`` → ``_normalize_bm25_scores``. Why body-BM25
    superseded the old index scorer (verified run_a2dffa0d): (1) the index's
    aliases were body-keyword-derived, so the body carries the same recall tokens
    + more; (2) ``_bm25_tokenize`` emits CJK bigrams → natively cross-language,
    where the old index leg's whole-token/prefix CJK match missed reordered CJK.

    ``include_evergreen`` selects the two callers' different section scopes — the
    ONE seam where injection and recall legitimately differ:
      • INJECTION (STEP1, default False): evergreen (principle/correction/COE/
        standing-preference/open-threads) is ALWAYS injected in full (CYCLE 2), so
        it is NOT keyword-selected — only OPERATIONAL sections are scored.
      • RECALL (STEP5a, True): recall returns SCOPED sections by query (not full
        injection), so evergreen MUST be reachable by a matching query too — score
        ALL sections.

    superseded entries are stripped from each body before scoring (see
    ``_strip_superseded_entries``).

    Returns ``{section_name: normalized_score in [0,1]}``; empty if none match.
    """
    corpus_sections = {
        name: body for name, body in sections.items()
        if body.strip() and (include_evergreen or name not in MEMORY_EVERGREEN_SECTIONS)
    }
    if not corpus_sections:
        return {}
    _sup = superseded_keys or set()
    corpus = {name: _strip_superseded_entries(body, _sup) for name, body in corpus_sections.items()}
    raw = _bm25_scores(user_message, corpus)
    if not raw:
        return {}
    return _normalize_bm25_scores(raw)


# Back-compat alias: the injection path (STEP1) calls the operational-only scorer.
def _operational_section_scores(
    user_message: str,
    sections: dict[str, str],
    superseded_keys: set[str] | None = None,
) -> dict[str, float]:
    """Injection-path scorer: operational sections only (evergreen always-injected).

    Thin wrapper over ``_section_body_scores(include_evergreen=False)`` — kept as a
    named entry point so the injection call site + its tests read intently."""
    return _section_body_scores(user_message, sections, superseded_keys, include_evergreen=False)


def _extract_superseded_keys(memory_content: str) -> set[str]:
    """Extract set of entry keys that are marked superseded in MEMORY.md.

    Scans for temporal metadata HTML comments with non-null superseded_by.
    Returns keys like {'KD03', 'RC14'} for entries that should be down-weighted.
    """
    superseded: set[str] = set()
    # Match entry key followed (within 2 lines) by superseded metadata
    for m in re.finditer(
        r"- \[([A-Z]{1,4}\d+)\].*?\n(?:.*?\n)?.*?"
        r"<!--.*?superseded_by:\s*(?!null)(\S+).*?-->",
        memory_content,
    ):
        superseded.add(m.group(1))
    return superseded




# ── Memory Injection ──────────────────────────────────────────────────


def select_memory_sections(
    memory_content: str,
    user_message: str = "",
    session_signals: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context_percent_used: float = 0.0,
    memory_embeddings: bool = False,
) -> str:
    """Assemble live MEMORY.md for system-prompt injection = the WHOLE body.

    NEW ARCHITECTURE (2026-08-14, XG): live MEMORY.md is ALWAYS full-injected
    regardless of size — there is NO selective mode, NO keyword/section scoring,
    NO in-prompt index, NO channel-minimal, NO adaptive-budget truncation. Size is
    bounded UPSTREAM by the size-valve (distillation_hook._enforce_size_valve:
    body >30K → archive lowest-value operational to .context until ≤25K), and
    excluded/archived content is reachable via recall (body-BM25 over
    .context/*-archive*.md). So the injector's ONLY job is: return the body.

    This deliberately RETIRED (old selective machinery, all removed): body-BM25
    section selection, _channel_minimal, _adaptive_max_tokens, EntryRefs 1-hop,
    SessionRecall injection, the in-prompt index block, and the "[Not loaded …]"
    manifest. Their value is now carried by full-injection (nothing is excluded to
    announce) + recall (past sessions / archived sections retrieved on demand).

    Channel privacy is enforced UPSTREAM, not here: group/non-owner sessions drop
    MEMORY.md wholesale via context_directory_loader.WHOLE_FILE_PRIVATE, so this
    function is never reached for them (the old is_channel branch was redundant).

    The extra params (user_message / session_signals / max_tokens /
    context_percent_used / memory_embeddings) are retained ONLY for call-site +
    test signature compatibility and are now INERT.

    Returns the full MEMORY body (index block stripped if a legacy file still
    carries one), or "" if empty.
    """
    body = extract_body_without_index(memory_content)
    return body if body.strip() else ""


# ── Index Injection / Extraction ──────────────────────────────────────






def extract_body_without_index(content: str) -> str:
    """Get MEMORY.md content with the index block removed.

    Strips both:
    1. Marker-delimited index blocks (<!-- MEMORY_INDEX_START/END -->)
    2. Bare '## Memory Index' sections written by agent Edit tool (no markers)

    The bare section is the root cause of duplicate indexes: the agent
    writes a '## Memory Index' via Edit, then inject_index_into_memory
    adds a marker-wrapped copy at the top — two copies coexist.

    Returns the original content if no index block is present.
    """
    start = content.find(MEMORY_INDEX_START)
    end = content.find(MEMORY_INDEX_END)

    if start == -1 or end == -1 or end <= start:
        result = content
    else:
        # Remove from start marker to end marker (inclusive) + trailing whitespace
        after_index = content[end + len(MEMORY_INDEX_END):]
        before_index = content[:start]
        result = before_index + after_index

    # Also strip a bare '## Memory Index' section (not marker-wrapped) — an
    # agent-written duplicate that persists across sessions. Pattern: the heading
    # followed by lines until the next ## header or EOF (.* not .+ consumes blank
    # lines within the section).
    # run_3cb6b9ae hardening (Gate-2 LOW): anchor to the TOP OF FILE only — the orphan
    # index block is always at the top. A bare `## Memory Index` heading appearing INSIDE
    # an entry body lower in the file must NOT be stripped: the old `^…$/MULTILINE` form
    # matched any line, so persisting the strip to disk (locked_write #6) could silently
    # delete a real entry whose body happened to contain that heading line.
    #
    # run_0f009a75 (Gate-0 fix): the anchor now ALSO allows the index one line below a
    # DOCUMENT TITLE — `# MEMORY\n\n## Memory Index` — the real shape the pure-`\A\s*`
    # form missed. The leading alt is deliberately `#[^#\n]` (a SINGLE-`#` document
    # title), NOT `[^\n]*` (any first line): a `[^\n]*` first-line would also swallow a
    # real `## Some Real Entry` header sitting above a bare index → catastrophic entry
    # deletion (the Gate-0 WRONG-FRAME catch). `#[^#\n]` matches `# MEMORY` but never
    # `## Section`, so a real section header above the index is untouchable. The
    # `(?!^## )` lookahead (needs re.MULTILINE) still stops the strip at the next
    # section, so content below the orphan index is never consumed.
    # CRLF-tolerant (`(?:\r\n|\n)` at every line break): MEMORY.md is written by the
    # agent via locked_write (always \n) on a macOS daemon, so CRLF is not expected —
    # but the pre-existing `\n`-only form would silently no-op on a CRLF file, so this
    # closes that latent gap at zero cost (Gate-2 run_0f009a75).
    result = re.sub(
        r"\A(?:\s*|#[^#\n][^\n]*(?:\r\n|\n)\s*)## Memory Index(?:\r\n|\n)(?:(?!^## ).*(?:\r\n|\n)?)*",
        "",
        result,
        flags=re.MULTILINE,
    )

    # Clean up extra blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    # If stripping the index left nothing meaningful, return as-is
    stripped = result.strip()
    return stripped + "\n" if stripped else ""


# ── Temporal Validity (P2) ───────────────────────────────────────────
# HTML comment metadata on MEMORY.md entries:
#   <!-- valid_from: YYYY-MM-DD | superseded_by: KEY | confidence: high -->
# Superseded entries score 0.1x weight in select_memory_sections().

_TEMPORAL_RE = re.compile(
    r"<!--\s*valid_from:\s*(\S+)\s*\|\s*superseded_by:\s*(\S+)\s*\|\s*confidence:\s*(\S+)\s*-->"
)

SUPERSEDED_WEIGHT = 0.1  # Score weight for superseded entries


def parse_temporal_metadata(text: str) -> Optional[dict]:
    """Extract temporal metadata from an HTML comment string.

    Args:
        text: String that may contain a temporal metadata HTML comment.

    Returns:
        Dict with keys valid_from, superseded_by (str or None), confidence.
        Returns None if no temporal metadata found.
    """
    if not text:
        return None

    m = _TEMPORAL_RE.search(text)
    if not m:
        return None

    superseded = m.group(2)
    if superseded.lower() == "null":
        superseded = None

    return {
        "valid_from": m.group(1),
        "superseded_by": superseded,
        "confidence": m.group(3),
    }


def _entry_temporal_weight(entry_text: str) -> float:
    """Return temporal score weight for an entry.

    Superseded entries → 0.1, active/no-metadata → 1.0.

    Args:
        entry_text: The full entry text including any HTML comment.

    Returns:
        Weight multiplier (0.1 or 1.0).
    """
    meta = parse_temporal_metadata(entry_text)
    if meta and meta.get("superseded_by"):
        return SUPERSEDED_WEIGHT
    return 1.0


def format_temporal_metadata(
    valid_from: str,
    superseded_by: Optional[str] = None,
    confidence: str = "high",
) -> str:
    """Generate an HTML comment string with temporal metadata.

    Args:
        valid_from: Date string (YYYY-MM-DD).
        superseded_by: Key of the superseding entry, or None.
        confidence: Confidence level (high/medium/low).

    Returns:
        HTML comment string like
        ``<!-- valid_from: 2026-04-11 | superseded_by: null | confidence: high -->``
    """
    sup = superseded_by if superseded_by else "null"
    return f"<!-- valid_from: {valid_from} | superseded_by: {sup} | confidence: {confidence} -->"


def add_temporal_metadata_to_entry(
    entry: str,
    valid_from: str,
    confidence: str = "high",
) -> str:
    """Add temporal metadata to a MEMORY.md entry if not already present.

    Appends the HTML comment on the line after the entry text.
    Idempotent — won't add if already present.

    Args:
        entry: The entry text (e.g. "- [KD28] 2026-04-11 New decision").
        valid_from: Date string for valid_from field.
        confidence: Confidence level.

    Returns:
        Entry with temporal metadata appended (or unchanged if already present).
    """
    if _TEMPORAL_RE.search(entry):
        return entry  # Already has temporal metadata

    meta = format_temporal_metadata(valid_from=valid_from, confidence=confidence)
    # Append on the next line with indentation
    return f"{entry.rstrip()}\n  {meta}"


def mark_entry_superseded(
    content: str,
    old_key: str,
    new_key: str,
) -> str:
    """Mark a MEMORY.md entry as superseded by another entry.

    If the entry already has temporal metadata, updates the superseded_by field.
    If not, adds temporal metadata with superseded_by set.

    Args:
        content: Full MEMORY.md content.
        old_key: Key of the entry to mark (e.g. "KD02").
        new_key: Key of the superseding entry (e.g. "KD99").

    Returns:
        Updated content string, or unchanged if old_key not found.
    """
    # Find the entry line by key
    entry_pattern = re.compile(
        rf"^(- \[{re.escape(old_key)}\].+?)$",
        re.MULTILINE,
    )
    match = entry_pattern.search(content)
    if not match:
        return content  # Key not found

    entry_start = match.start()
    entry_line = match.group(1)

    # Check if there's existing temporal metadata on the next line(s)
    # Look at the text after this entry line until the next entry or section
    after_entry = content[match.end():]

    # Check for existing temporal metadata within the next 2 lines
    next_lines = after_entry.split("\n", 3)
    temporal_line_idx = None
    for i, line in enumerate(next_lines[:3]):
        if _TEMPORAL_RE.search(line):
            temporal_line_idx = i
            break

    if temporal_line_idx is not None:
        # Update existing metadata
        old_meta_match = _TEMPORAL_RE.search(next_lines[temporal_line_idx])
        if old_meta_match:
            old_meta = old_meta_match.group(0)
            new_meta = format_temporal_metadata(
                valid_from=old_meta_match.group(1),
                superseded_by=new_key,
                confidence=old_meta_match.group(3),
            )
            return content.replace(old_meta, new_meta, 1)
    else:
        # Add new metadata after the entry line
        # Extract date from the entry for valid_from
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", entry_line)
        valid_from = date_match.group(0) if date_match else "unknown"
        meta = format_temporal_metadata(
            valid_from=valid_from, superseded_by=new_key
        )
        return content.replace(
            entry_line,
            f"{entry_line}\n  {meta}",
            1,
        )

    return content
