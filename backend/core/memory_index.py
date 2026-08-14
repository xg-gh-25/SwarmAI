"""Progressive Memory Disclosure — index generation and section selection.

Implements the 3-layer memory system for 100% recall coverage:

- **L0 Compact Index**: Machine-generated index block with value-based tiers
  (Permanent/Active/Archived) and keyword aliases per entry.  Always injected
  into the system prompt (~300-500 tokens).

- **L1 Section Selection**: Topic-triggered loading of 0-3 MEMORY.md sections
  based on keyword matching (against user's first message) and rule-based
  session signals.  Budget-capped at a configurable token limit.

- **L2 On-Demand**: Agent uses Read tool to load specific sections.  No code
  needed — behavioral directive in AGENT.md.

North star: **any memory entry, regardless of age, can be recalled when relevant.**

Public symbols:

- ``generate_memory_index``        — Parse MEMORY.md, produce compact index block
- ``keyword_relevance``            — Score relevance of an index entry to a message
- ``select_memory_sections``       — Select sections for L1 injection
- ``inject_index_into_memory``     — Insert/replace index block in MEMORY.md
- ``extract_index_from_memory``    — Pull out the index block
- ``extract_body_without_index``   — Get MEMORY.md content minus the index block
- ``parse_memory_sections``        — Split MEMORY.md into named sections
- ``MEMORY_INDEX_START``           — Start marker constant
- ``MEMORY_INDEX_END``             — End marker constant
"""

import re
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

MEMORY_INDEX_START = "<!-- MEMORY_INDEX_START -->"
MEMORY_INDEX_END = "<!-- MEMORY_INDEX_END -->"

# D1 (run_4341fc50): the index block is injected verbatim on every cold start
# (select_memory_sections L0). Long-prose entry titles duplicated the body
# entry they point to (avg 159, max 478 chars/line). The index is a NAVIGATION
# layer — a pointer ([ID] + short title + keyword aliases) is all section
# selection needs; the full prose lives in the body. Cap the title SUMMARY to a
# word boundary at/under this length; the '| aliases' tail is ALWAYS kept (it is
# the 1.5x-weighted keyword_relevance recall signal — see _keyword_section_scores).
MEMORY_INDEX_TITLE_CAP = 70


def _cap_index_title(title: str, cap: int = MEMORY_INDEX_TITLE_CAP) -> str:
    """Cap an index title to <= `cap` chars at a word boundary.

    Operates on str codepoints (never bytes) so a CJK char is never split
    mid-sequence. Trims at the last whitespace within the cap; if there is no
    whitespace (one long token / CJK run), hard-slices at `cap` codepoints.
    Appends an ellipsis only when content was dropped, so callers/tests can see
    the title was truncated without inflating it past the cap meaningfully.
    """
    if len(title) <= cap:
        return title
    # Reserve 1 codepoint for the ellipsis so the result is ALWAYS <= cap.
    budget = cap - 1
    head = title[:budget]
    cut = head.rsplit(" ", 1)[0]
    # Guard the over-aggressive rsplit (Gate-2 F2): only trim at the last space
    # if it RETAINS at least half the budget; otherwise the single early space
    # (e.g. "Note: <200 chars>") would collapse the title to almost nothing.
    # Fall back to the hard codepoint slice (still CJK-safe — str, not bytes).
    if len(cut) < budget // 2:
        cut = head
    return cut.rstrip() + "…"


def _recall_safe_aliases(full_title: str, capped_title: str,
                         aliases: list[str]) -> list[str]:
    """Preserve section-selection recall when a title is capped (Gate-2 F1).

    `_keyword_section_scores` scores a query against the index line's
    summary + aliases. The FULL prose title used to be the match surface;
    capping it would silently drop the matchable tokens beyond the cap (e.g.
    generic words like "subprocess"/"linux" that `_extract_keywords` does not
    rank into the top-6 distinctive aliases). To keep selection lossless, append
    those LOST content tokens (stopword/short-filtered, deduped) to the aliases.

    Content tokens are far fewer bytes than the dropped prose (filler/stopwords
    are excluded), so the index still shrinks — it just keeps the keyword
    surface. No-op when the title was not capped.
    """
    if full_title == capped_title:
        return aliases
    full_tokens = _tokenize_lower(full_title)
    kept_tokens = set(_tokenize_lower(capped_title))
    have = {a.lower() for a in aliases}
    lost: list[str] = []
    seen: set[str] = set()
    for tok in full_tokens:
        if tok in kept_tokens or tok in have or tok in seen:
            continue
        seen.add(tok)
        lost.append(tok)
    return aliases + lost


# Pure ISO date (YYYY-MM-DD). Anchored: only a token that IS a bare date, never
# a date embedded in a longer token (e.g. a hypothetical "2026-06-27-fix" stays).
_PURE_DATE_RE = re.compile(r"^20\d\d-\d\d-\d\d$")



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

# Keyword relevance threshold for L1 section loading
KEYWORD_THRESHOLD = 0.15

# Default max tokens for selective injection (only used above threshold)
DEFAULT_MAX_TOKENS = 50_000

# Full-injection threshold: below this, inject entire MEMORY.md.
# At 30K tokens (~375 entries), MEMORY.md uses 30% of 100K system prompt budget.
# Below this, Claude reads everything — no selection needed.
FULL_INJECTION_THRESHOLD = 30_000

# Reversible Context Recall (run_9de88af9): max excluded section NAMES listed in
# the selective-injection manifest before collapsing to "+N more". Keeps the
# manifest to a single cache-friendly tail line.
MANIFEST_MAX_NAMES = 8

# Maximum additional sections to load via EntryRefs 1-hop expansion.
# Scale up as MEMORY.md grows and token budget allows.
MAX_REF_SECTIONS = 3

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


# ── Entry Parsing ─────────────────────────────────────────────────────


def _parse_entries(section_content: str) -> list[dict]:
    """Parse bullet-point entries from a MEMORY.md section.

    Each entry is a ``- YYYY-MM-DD: **title** — description`` line.
    Returns list of dicts with keys: date_str, title, full_text, date.
    """
    entries = []
    # Match entries: - YYYY-MM-DD: **title** — rest
    # Also match: - 🔵 **title** — rest (Open Threads format)
    # Only process top-level bullets (column 0). Any leading whitespace
    # means indented sub-bullet — skip to avoid polluting the index.
    for raw_line in section_content.split("\n"):
        if not raw_line or raw_line[0] in (" ", "\t"):
            continue
        line = raw_line.strip()
        if not line.startswith("- "):
            continue

        entry: dict = {"full_text": line[2:].strip()}  # strip "- "

        # Try date-prefixed format: 2026-03-30: **title** ...
        date_match = re.match(
            r"(\d{4}-\d{2}-\d{2}):\s*\*\*(.+?)\*\*",
            entry["full_text"],
        )
        if date_match:
            entry["date_str"] = date_match.group(1)
            entry["title"] = date_match.group(2).strip()
            try:
                entry["date"] = datetime.strptime(
                    entry["date_str"], "%Y-%m-%d"
                ).date()
            except ValueError:
                entry["date"] = None
        else:
            # Try emoji/bullet format: 🔵 **title** — rest
            title_match = re.match(r"[^\*]*\*\*(.+?)\*\*", entry["full_text"])
            if title_match:
                entry["title"] = title_match.group(1).strip()
            else:
                entry["title"] = entry["full_text"][:60]
            entry["date_str"] = None
            entry["date"] = None

        entries.append(entry)

    return entries


def _extract_keywords(entry_text: str) -> list[str]:
    """Extract 3-6 keyword aliases from an entry's full text.

    Focuses on technical terms, proper nouns, and distinctive tokens
    that would help with recall.  Unicode-aware so CJK entries produce
    meaningful keywords.
    """
    tokens = re.findall(r"[\w\-]+", entry_text, re.UNICODE)

    # Score tokens by distinctiveness
    scored: dict[str, float] = {}
    for token in tokens:
        t_lower = token.lower()
        is_cjk = bool(_CJK_RE.search(token))

        # Length filter: 2-char CJK words (竞品, 测试) are meaningful;
        # 2-char English tokens (is, at, to) are noise.
        min_len = 2 if is_cjk else 3
        if len(t_lower) < min_len or t_lower in _STOP_WORDS:
            continue

        score = 0.0
        # CJK tokens are inherently distinctive — boost them
        if is_cjk:
            score += 2.0
        # Technical terms (contains underscore, hyphen, or ALL_CAPS)
        if "_" in token or "-" in token:
            score += 2.0
        if token.isupper() and len(token) > 2:
            score += 2.0
        # CamelCase or mixed case
        if any(c.isupper() for c in token[1:]) and any(c.islower() for c in token):
            score += 1.5
        # Longer tokens are more distinctive
        if len(token) > 6:
            score += 1.0
        # Numbers mixed with text (e.g., "v7", "5428", "200K")
        if any(c.isdigit() for c in token):
            score += 0.5
        # Base score for all tokens
        score += 0.5

        key = t_lower
        if key not in scored or score > scored[key]:
            scored[key] = score

    # Return top 6 by score
    sorted_tokens = sorted(scored.items(), key=lambda x: x[1], reverse=True)
    return [t for t, _ in sorted_tokens[:6]]


# ── Cross-Reference Extraction ───────────────────────────────────────

def _extract_refs(entry_text: str, self_key: str) -> list[str]:
    """Extract cross-reference IDs from an entry's full text.

    Scans for patterns like [COE02], [KD01], [RC15] etc.
    Excludes self-references (where the ref matches the entry's own key).

    Args:
        entry_text: The full text of the memory entry.
        self_key: The entry's own key (e.g. "KD01") to exclude self-refs.

    Returns:
        Sorted list of unique reference IDs (e.g. ["COE02", "RC15"]).
    """
    # Extract full reference IDs (e.g. "COE02", "KD01", "PRI03") from bracketed refs
    full_refs = re.findall(
        r"\[(COE\d+|KD\d+|RC\d+|LL\d+|OT\d+|PRI\d+|COR\d+|DEC\d+|GUI\d+|PIT\d+|PRC\d+|MOD\d+|SP\d+)\]",
        entry_text,
    )
    unique = sorted(set(full_refs) - {self_key})
    return unique


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

    Unified-retrieval — the replacement for the index-based
    ``_keyword_section_scores``. Mirrors DDD's ``recall_multi._ddd_section_scores``
    (the correct, index-free pattern): ``_bm25_scores({section: body})`` →
    ``_normalize_bm25_scores``. Reasons this supersedes the index scorer (verified
    run_a2dffa0d): (1) the index's aliases are ``_extract_keywords(body)``-derived,
    so the body carries the same recall tokens + more; (2) ``_bm25_tokenize`` emits
    CJK bigrams → natively cross-language, where the index leg's ``_cjk_match``
    (whole-token/prefix) missed reordered CJK.

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




def _hybrid_section_scores(user_message: str, allow_embed: bool = False) -> dict[str, float]:
    """REMOVED — vector/hybrid section scoring is gone (pure-filesystem recall
    design §3.3/§5.4, 2026-06-28).

    The vector leg (memory_vec + Bedrock Titan embed + hybrid_memory_search merge)
    was deleted: NO recall path embeds anymore. This function is retained as an
    inert stub that ALWAYS returns ``{}`` so (a) callers that still import it do
    not ImportError during the transition, and (b) it is structurally impossible
    for any Titan/embed call to fire from here (the body that called
    ``embed_text`` no longer exists). All recall scoring is keyword/BM25 via
    ``_keyword_section_scores``. ``allow_embed`` is kept only for signature
    compatibility and has no effect.

    The vector golden-case probes (recall_chain_probe.py synonym_guard /
    missing_vector / stale_index / recall_budget / knowledge_live) that drove the
    old body are retired via s_golden-case in the same change (design §7/DoD9).
    """
    return {}


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

    # Also strip any bare '## Memory Index' sections (not marker-wrapped).
    # These are agent-written duplicates that persist across sessions.
    # Pattern: '## Memory Index' followed by lines until the next ## header or EOF.
    # Uses .* (not .+) so blank lines within the index section are consumed.
    result = re.sub(
        r"^## Memory Index\n(?:(?!^## ).*\n?)*",
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
