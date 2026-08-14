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


def _compress_aliases(aliases: list[str]) -> list[str]:
    """Recall-neutral shrink of a Memory Index alias list (run_2f4d92da).

    Applied to the FINAL alias list at every index-emission site (after
    ``_recall_safe_aliases`` has already added back any title-cap-dropped
    tokens). Removes ONLY tokens that provably contribute nothing to recall
    SECTION-SELECTION (the only thing the index aliases feed —
    ``_keyword_section_scores`` → ``keyword_relevance``):

    1. **Date tokens** (``20\\d\\d-\\d\\d-\\d\\d``) — dropped unconditionally.
       A bare-date alias is measured NOISE at the section-selection layer, not a
       recall key: a bare-date query lights up 6/8 sections (verified run_2f4d92da),
       i.e. it fails to discriminate. On MIXED date+content queries the date token
       adds ZERO sections beyond the content tokens (``date_adds=nothing`` on the
       live corpus) — content drives selection. On the auto-recall path the query
       side already strips dates (``_extract_query_keywords``), so the alias is
       unreachable there. True date-scoped recall happens at the ENTRY/BM25 body
       layer (entry text carries the date stamp), which this does not touch.
       Removing date aliases therefore does not lose recall — and it slightly
       SHARPENS section selection by not dragging unrelated sections in on a date.
    2. **Within-list duplicates** — case-insensitive, order-preserving; first wins.

    DELIBERATELY PRESERVED (M3-skeptic verified load-bearing, run_e787c746):
    ``run_xxx`` ids (they ARE live recall query keys — 443 entries; a
    ``run_002eca4c`` query must still hit), every non-date/non-dup token, and
    CJK phrases (``keyword_relevance`` matches CJK by substring/prefix, so an
    exact-equality prune would silently drop a distinct match surface).

    Pure function, no state — idempotent by construction (a compressed list has
    no dates and no dups, so a second pass is a no-op).
    """
    out: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if _PURE_DATE_RE.match(alias):
            continue  # date aliases are section-selection noise (see docstring)
        key = alias.lower()
        if key in seen:
            continue  # within-list duplicate (first spelling wins)
        seen.add(key)
        out.append(alias)
    return out

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


def _load_referenced_sections(
    loaded_parts: list[str],
    sections: dict[str, str],
    already_loaded: set[str],
) -> list[str]:
    """Extract cross-references from loaded memory entries and return
    section names that should be loaded via 1-hop ref expansion.

    Scans ``refs:`` annotations in the loaded index lines for ref IDs
    (e.g. COE02, KD01), maps them to section names, and returns any
    not already loaded.  Returns at most a few sections to control
    token budget.
    """
    ref_ids: set[str] = set()
    for part in loaded_parts:
        # Look for refs: annotations in index lines
        for match in re.finditer(r"refs:\s*([A-Z0-9, ]+)", part):
            for ref in match.group(1).split(","):
                ref = ref.strip()
                if ref:
                    ref_ids.add(ref)

    # Map ref IDs to section names
    needed_sections: list[str] = []
    for ref_id in ref_ids:
        # Extract prefix (letters before digits)
        prefix_match = re.match(r"([A-Z]+)", ref_id)
        if prefix_match:
            section_name = _REF_PREFIX_TO_SECTION.get(prefix_match.group(1))
            if section_name and section_name not in already_loaded and section_name in sections:
                if section_name not in needed_sections:
                    needed_sections.append(section_name)

    return needed_sections


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


def generate_memory_index(content: str) -> str:
    """Generate a compact index block from MEMORY.md content.

    The index has three tiers:
    - **Permanent**: COEs + Key Decisions (never age out)
    - **Active**: Recent Context + Lessons (<90d / <180d)
    - **Archived**: Older Recent Context entries (count only)

    Each entry includes keyword aliases for enhanced recall.

    Args:
        content: Full MEMORY.md content (may already contain an index block).

    Returns:
        Index content WITHOUT markers.  Callers that need a complete block
        should use ``inject_index_into_memory()`` or wrap with
        ``MEMORY_INDEX_START`` / ``MEMORY_INDEX_END`` themselves.
    """
    sections = parse_memory_sections(content)
    today = date.today()

    # ── Count entries per section ──
    # For Open Threads, only count active entries (exclude ✅ resolved).
    counts: dict[str, int] = {}
    for sec_name in MEMORY_PREFIX_MAP:  # Only current sections (not legacy)
        sec_content = sections.get(sec_name, "")
        entries = _parse_entries(sec_content)
        if sec_name == "Open Threads":
            entries = [e for e in entries if "\u2705" not in e["full_text"]]
        counts[sec_name] = len(entries)

    # ── Build Permanent tier (meta-cognitive + COEs — never age out) ──
    permanent_lines: list[str] = []
    _permanent_scan = [s for s in PERMANENT_SECTIONS if sections.get(s)]
    for sec_name in _permanent_scan:
        prefix = SECTION_KEY_PREFIX.get(sec_name, "PRM")
        entries = _parse_entries(sections.get(sec_name, ""))
        for i, entry in enumerate(entries, 1):
            key = f"{prefix}{i:02d}"
            aliases = _extract_keywords(entry["full_text"])
            date_prefix = f"{entry['date_str']} " if entry.get("date_str") else ""
            # D1: cap the title to a pointer (full prose stays in the body).
            title = _cap_index_title(entry["title"])
            # Gate-2 F1: keep section-selection lossless — recover keyword tokens
            # the cap dropped from the title into the aliases tail.
            aliases = _recall_safe_aliases(entry["title"], title, aliases)
            # Safe shrink: drop recall-neutral date aliases + within-list dups
            # (run-ids/CJK/title-recovery tokens preserved — run_2f4d92da).
            aliases = _compress_aliases(aliases)
            alias_str = ", ".join(aliases) if aliases else ""
            refs = _extract_refs(entry["full_text"], key)
            line = f"- [{key}] {date_prefix}{title}"
            if refs:
                line += f" | refs: {', '.join(refs)}"
            if alias_str:
                line += f" | {alias_str}"
            permanent_lines.append(line)

    # ── Build Active tier (operational + cognitive — decay-managed) ──
    # Open Threads is in ACTIVE_SECTIONS (layer="operational") but is emitted
    # by its OWN dedicated ot_lines block below — which is canonical because it
    # filters ✅-resolved entries (this generic loop does not). Excluding it
    # here prevents every OTxx being double-listed (once rich here, once bare
    # below). Mirrors the same OT-exclusion already done at
    # context_health_hook.py _staleness_scan (s != "Open Threads").
    active_lines: list[str] = []
    _active_scan = [
        s for s in ACTIVE_SECTIONS if sections.get(s) and s != "Open Threads"
    ]
    for sec_name in _active_scan:
        prefix = SECTION_KEY_PREFIX.get(sec_name, "ACT")
        entries = _parse_entries(sections.get(sec_name, ""))
        for i, entry in enumerate(entries, 1):
            key = f"{prefix}{i:02d}"
            aliases = _extract_keywords(entry["full_text"])
            date_prefix = f"{entry['date_str']} " if entry.get("date_str") else ""
            # D1: cap the title to a pointer (full prose stays in the body).
            title = _cap_index_title(entry["title"])
            # Gate-2 F1: keep section-selection lossless — recover keyword tokens
            # the cap dropped from the title into the aliases tail.
            aliases = _recall_safe_aliases(entry["title"], title, aliases)
            # Safe shrink (run_2f4d92da) — see permanent-tier comment above.
            aliases = _compress_aliases(aliases)
            alias_str = ", ".join(aliases) if aliases else ""
            refs = _extract_refs(entry["full_text"], key)

            line = f"- [{key}] {date_prefix}{title}"
            if refs:
                line += f" | refs: {', '.join(refs)}"
            if alias_str:
                line += f" | {alias_str}"
            active_lines.append(line)

    # ── Build Open Threads entries ──
    # Only index active entries — ✅ resolved entries excluded from index.
    # They remain in the body under "### Resolved (archive)" for reference,
    # but indexing them causes stale counts and misleading suggestions.
    ot_lines: list[str] = []
    ot_entries = _parse_entries(sections.get("Open Threads", ""))
    active_ot = [e for e in ot_entries if "\u2705" not in e["full_text"]]
    for i, entry in enumerate(active_ot, 1):
        key = f"OT{i:02d}"
        # D1: cap OT titles too (third title-emitter — same pointer rule).
        title = _cap_index_title(entry["title"])
        # Gate-2 F1: recover dropped keyword tokens into an alias tail so a capped
        # OT title keeps the same match surface. (OT is always-loaded, so this is
        # belt-and-suspenders, but keeps all 3 emitters consistent.)
        lost = _recall_safe_aliases(entry["title"], title, [])
        # Safe shrink (run_2f4d92da) — same recall-neutral prune as the other
        # two emitters; keeps all 3 title-emitters consistent.
        lost = _compress_aliases(lost)
        tail = f" | {', '.join(lost)}" if lost else ""
        ot_lines.append(f"- [{key}] {title}{tail}")

    # ── Assemble index ──
    count_parts = []
    for sec_name, count in counts.items():
        if count > 0:
            count_parts.append(f"{count} {sec_name.lower()}")

    header = " | ".join(count_parts) if count_parts else "empty"

    lines = [
        "## Memory Index",
        header,
    ]

    if permanent_lines:
        lines.append("")
        lines.append("### Permanent (COEs + Architectural Decisions — never age out)")
        lines.extend(permanent_lines)

    if active_lines or ot_lines:
        lines.append("")
        lines.append("### Active (Recent Context + Lessons)")
        lines.extend(active_lines)
        if ot_lines:
            lines.extend(ot_lines)

    return "\n".join(lines)


# ── Keyword Relevance ─────────────────────────────────────────────────


def keyword_relevance(
    user_message: str,
    entry_summary: str,
    aliases: list[str],
) -> float:
    """Score relevance of a memory index entry to a user message.

    Matches against both the entry summary text AND keyword aliases.
    Alias hits are weighted 1.5x to reward curated recall paths.

    Uses the user's message token count as denominator so short queries
    like "COE" or "sandbox" score high when they hit an exact match,
    regardless of how many aliases the entry has.

    Args:
        user_message: The user's first message in the session.
        entry_summary: One-line summary from the index entry.
        aliases: Keyword aliases for this entry.

    Returns:
        Float relevance score (0.0 = no match, capped at 1.0).
    """
    msg_tokens = set(_tokenize_lower(user_message))
    if not msg_tokens:
        return 0.0

    entry_tokens = set(_tokenize_lower(entry_summary))
    alias_tokens = set(_tokenize_lower(" ".join(aliases)))

    if not entry_tokens and not alias_tokens:
        return 0.0

    # For English (space-separated) exact set intersection works.
    # For CJK (no word boundaries) we need flexible matching because
    # regex \w+ captures entire runs as single tokens:
    #   query: "竞品分析的结论是什么"  →  token: "竞品分析的结论是什么"
    #   entry: "竞品分析陷阱"          →  token: "竞品分析陷阱"
    # Neither is a substring of the other, but they share "竞品分析".
    #
    # Strategy for CJK tokens (bidirectional substring + shared prefix):
    # 1. qt ⊂ tt  →  "单进程" in "单进程架构保持不动"
    # 2. tt ⊂ qt  →  "竞品" in "竞品分析的结论是什么"
    # 3. shared prefix ≥ 2 CJK chars  →  "竞品分析陷阱" ~ "竞品分析的结论是什么"
    def _cjk_match(a: str, b: str) -> bool:
        """Check if two CJK tokens are related (substring or shared prefix)."""
        if a in b or b in a:
            return True
        # Shared prefix: count common leading characters
        common = 0
        for ca, cb in zip(a, b):
            if ca == cb:
                common += 1
            else:
                break
        return common >= 2

    def _match_count(query_toks: set[str], target_toks: set[str]) -> int:
        """Count query tokens that match target tokens (exact or CJK flexible)."""
        count = 0
        for qt in query_toks:
            if qt in target_toks:
                count += 1
            elif _CJK_RE.search(qt):
                if any(_cjk_match(qt, tt) for tt in target_toks if _CJK_RE.search(tt)):
                    count += 1
        return count

    title_hit_count = _match_count(msg_tokens, entry_tokens)
    alias_hit_count = _match_count(msg_tokens, alias_tokens)

    # Meaningful overlap (already filtered by _tokenize_lower)
    if title_hit_count == 0 and alias_hit_count == 0:
        return 0.0

    # Denominator = user's message tokens.  This measures "what fraction of
    # the user's query was satisfied by this entry" — a recall metric.
    # Short queries ("COE", "sandbox") score high on exact match instead of
    # being diluted by alias-rich entries (old bug: max(msg, entry) as
    # denominator made 2-token queries score ~0.15 against 20-alias entries).
    # Cap at 1.0 since alias_hits * 1.5 can exceed denominator.
    score = (title_hit_count + alias_hit_count * 1.5) / len(msg_tokens)
    return min(score, 1.0)


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


def _parse_index_entries(content: str) -> list[dict]:
    """Parse index entries from MEMORY.md content.

    Scopes to the ``<!-- MEMORY_INDEX_START/END -->`` block when present,
    so entries in the body's rendered copy are not double-counted.

    Returns list of dicts with: key, summary, aliases.
    """
    # Scope to marker block if markers exist
    idx_match = re.search(
        r"<!-- MEMORY_INDEX_START -->(.*?)<!-- MEMORY_INDEX_END -->",
        content, re.DOTALL,
    )
    text = idx_match.group(1) if idx_match else content

    entries = []
    for line in text.split("\n"):
        line = line.strip()
        # Match: - [KEY] summary | alias1, alias2
        m = re.match(r"^- \[(\w+)\]\s+(.+)$", line)
        if not m:
            continue

        key = m.group(1)
        rest = m.group(2)

        # Split on all | segments, filtering out refs: annotations
        if "|" in rest:
            parts = rest.split("|")
            summary = parts[0].strip()
            aliases = []
            for part in parts[1:]:
                p = part.strip()
                if p.startswith("refs:"):
                    continue  # Cross-reference annotations, not keywords
                aliases.extend(
                    a.strip() for a in p.split(",") if a.strip()
                )
        else:
            summary = rest
            aliases = []

        entries.append({
            "key": key,
            "summary": summary.strip(),
            "aliases": aliases,
        })

    return entries


def _adaptive_max_tokens(context_percent_used: float) -> int:
    """Return token budget for memory injection based on context window usage.

    Power-first principle: inject max memory at all times.  We have 1M context.
    Only apply budget pressure when genuinely near capacity.

    Tiers:
    - <50% used:  unlimited (999,999) — inject everything relevant
    - 50-75%:     generous  (50,000)  — still plenty of room in 1M window
    - 75-95%:     significant (20,000) — memory recall matters even late
    - >=95%:      minimum   (5,000)   — emergency, still inject index + top
    """
    if context_percent_used >= 95:
        return 5_000
    if context_percent_used >= 75:
        return 20_000
    if context_percent_used >= 50:
        return 50_000
    return 999_999  # effectively unlimited


# ── SessionRecall singleton cache ────────────────────────────────────

_session_recall_cache: dict[str, object] = {}  # db_path_str → SessionRecall
_embedding_client_cache: object | None = None  # cached EmbeddingClient instance


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


def _keyword_section_scores(
    user_message: str,
    index_block: str,
    superseded_keys: set[str] | None = None,
    *,
    section_name_signal: bool = False,
) -> dict[str, float]:
    """Score sections by keyword matching, applying temporal weight.

    Entries marked as superseded (via P2 temporal validity) score at
    0.1x weight, preventing stale decisions from dominating section selection.

    ``section_name_signal`` (default False, run_94e602ad Gate-2): when True, ALSO
    score the query against each section's own NAME (a query that names a
    category — "what cognitive PRINCIPLES govern judgment" — surfaces the
    Principles section even when no per-entry summary shares the query's words).
    This is ONLY safe on the RECALL READ path (recall_context), NOT the L1
    INJECTION path: `select_memory_sections` injects EVERY returned section into
    the system prompt (score discarded, :1160), so a name signal there would
    spuriously inject a section whenever a normal chat message happens to contain
    a category noun ("the *principles* of good API design" → inject Principles).
    Default OFF keeps the injection path byte-identical to pre-fix behavior; the
    recall caller opts in. (Two-consumer defect caught by Gate-2.)
    """
    index_entries = _parse_index_entries(index_block)
    matched: dict[str, float] = {}
    _superseded = superseded_keys or set()
    # A section is name-eligible ONLY if it has >=1 NON-superseded entry — else a
    # section of purely stale/reversed content would re-surface at full strength
    # via its name, bypassing the superseded 0.1x guard (Gate-2 defect #2).
    live_sections: set[str] = set()

    for entry in index_entries:
        score = keyword_relevance(
            user_message, entry["summary"], entry["aliases"]
        )
        sec_name = _key_to_section(entry["key"])
        if sec_name and entry["key"] not in _superseded:
            live_sections.add(sec_name)
        # Apply temporal weight: superseded entries get 0.1x.
        if entry["key"] in _superseded:
            score *= SUPERSEDED_WEIGHT
        if score >= KEYWORD_THRESHOLD:
            if sec_name and (sec_name not in matched or score > matched[sec_name]):
                matched[sec_name] = score

    # Section-NAME signal — recall READ path only (see docstring). Only sections
    # with a live (non-superseded) entry are name-eligible. Additive max-merge:
    # can only RAISE a section already scoreable, never lower a summary hit. Does
    # NOT bridge the synonym gap ("mistakes"→Pitfalls needs the deferred semantic
    # leg) — only the case where the query uses the section's own name.
    if section_name_signal:
        for sec_name in live_sections:
            name_score = keyword_relevance(user_message, sec_name, [])
            if name_score >= KEYWORD_THRESHOLD:
                if sec_name not in matched or name_score > matched[sec_name]:
                    matched[sec_name] = name_score

    return matched


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


# ── Full Injection Helpers ────────────────────────────────────────────


def _full_injection(memory_content: str) -> str:
    """Full injection mode: entire MEMORY.md body, NO in-prompt index.

    Power-first: Claude reads everything. No selection, no filtering.

    Unified-retrieval STEP3 (2026-08-14): the in-prompt navigation index is NO
    LONGER injected. In full-injection mode the whole body is already present, so
    a table-of-contents pointing INTO that same body is pure duplication (~15K/turn).
    Recall selection now scans the ## section BODY directly (STEP1), so the index
    serves no consumer. It is still GENERATED/maintained on disk (strangler-fig:
    deleted in STEP5). Guiding principle: SwarmAI TECH.md § Architecture "活/冷二分".
    """
    body = extract_body_without_index(memory_content)
    return body if body.strip() else ""


def _channel_minimal(memory_content: str) -> str:
    """Channel sessions: Open Threads only (no personal sections, no in-prompt index).

    STEP3 (2026-08-14): index no longer injected (see _full_injection)."""
    sections = parse_memory_sections(memory_content)
    parts: list[str] = []

    ot_content = sections.get("Open Threads", "")
    if ot_content.strip():
        parts.append(f"## Open Threads\n{ot_content}")

    parts.append(
        f"\n[Full MEMORY.md available via Read tool — "
        f"{len(sections)} sections not loaded]"
    )
    return "\n\n".join(parts)


def select_memory_sections(
    memory_content: str,
    user_message: str = "",
    session_signals: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context_percent_used: float = 0.0,
    memory_embeddings: bool = False,
) -> str:
    """Assemble MEMORY.md content for system prompt injection.

    Two modes, auto-selected by token count:

    **Full injection** (<FULL_INJECTION_THRESHOLD tokens):
      Inject index + entire MEMORY.md body. Claude reads everything.
      No keyword matching, no vector search, no section selection.
      This is the default for typical memory sizes (~100-300 entries).

    **Selective injection** (≥FULL_INJECTION_THRESHOLD tokens):
      Inject index + keyword/hybrid-matched sections within budget.
      Falls back to keyword-only if vector search fails.

    Args:
        memory_content: Full MEMORY.md content.
        user_message: User's first message (for keyword matching).
        session_signals: Dict with keys like is_channel, is_resume, etc.
        max_tokens: Maximum tokens for selective injection mode.
        context_percent_used: Current context window usage (0-100).
        memory_embeddings: If True, use hybrid vector+keyword in selective mode.

    Returns:
        Assembled content string for system prompt.
    """
    from .context_directory_loader import ContextDirectoryLoader

    signals = session_signals or {}

    # ── Auto-detect mode: full injection vs selective ──
    total_tokens = ContextDirectoryLoader.estimate_tokens(memory_content)

    # Channel sessions: always minimal (index + Open Threads only)
    if signals.get("is_channel"):
        return _channel_minimal(memory_content)

    # Full injection mode: MEMORY.md is small enough to inject entirely.
    # Power-first: Claude reads everything, no selection needed.
    if total_tokens < FULL_INJECTION_THRESHOLD:
        return _full_injection(memory_content)

    # ── Selective injection mode (MEMORY.md exceeds threshold) ──
    logger.info(
        "Memory exceeds full-injection threshold (%d >= %d tokens), "
        "using selective injection",
        total_tokens, FULL_INJECTION_THRESHOLD,
    )

    # Adaptive budget for selective mode
    if context_percent_used > 0:
        max_tokens = _adaptive_max_tokens(context_percent_used)

    sections = parse_memory_sections(memory_content)

    # ── STEP3 (2026-08-14): the in-prompt index is NO LONGER injected. Section
    # selection scans the ## section BODY directly (STEP1 body-BM25), so an
    # in-prompt table-of-contents serves no consumer — injecting it was ~15K/turn
    # of pure duplication. Index is still generated on disk (strangler-fig; deleted
    # STEP5). Output = evergreen (always) + operational (budgeted), no index prefix.
    parts: list[str] = []
    # Operational budget accounting starts from ZERO (no index prefix). Evergreen is
    # a FLOOR that sits OUTSIDE this operational cap (see below) — deliberately NOT
    # subtracted from `used_tokens`, so a large evergreen core can never starve the
    # operational keyword-matched sections. `used_tokens` bounds ONLY the
    # operational/ref/recall loop; evergreen is unconditional.
    used_tokens = 0

    # ── Always load: evergreen sections, FIRST + UNCONDITIONALLY (CYCLE 2/7) ──
    # Evergreen (principle/correction/COE/standing-preference/open-threads) is
    # query-INDEPENDENT judgment — it shapes every turn, so it is injected in FULL
    # regardless of keyword match AND is NOT budget-gated (a token cap must never
    # drop a load-bearing principle; that was the cross-language recall gap, and
    # capping it would violate PRI13 "power over token budget" + O030 "no
    # truncating-容灾"). It loads FIRST, in schema order (MEMORY_SECTION_NAMES) for a
    # stable, cache-friendly prefix. CRUCIALLY its size does NOT consume the
    # operational budget below — the two are decoupled floors (Gate-2 D fix:
    # otherwise a ~6K evergreen core at the 5K emergency tier zeroed out every
    # operational section). Evergreen overshooting the emergency cap is BY DESIGN
    # (power-first); the downstream loader does not truncate cognition (PRI08).
    for sec_name in MEMORY_SECTION_NAMES:
        if sec_name not in ALWAYS_LOAD_SECTIONS:
            continue
        sec_content = sections.get(sec_name, "")
        if not sec_content.strip():
            continue
        parts.append(f"## {sec_name}\n{sec_content}")

    # ── Rule-based section loading ──
    sections_to_load: set[str] = set()

    # NOTE: COE Registry + Open Threads are now always-loaded (evergreen), so the
    # is_resume / is_first_session_today / has_coe→COE adds would be redundant
    # (harmlessly skipped by the ALWAYS_LOAD guard below). Only has_coe→Guidelines
    # (an OPERATIONAL section) still does real work here.
    if signals.get("has_coe"):
        sections_to_load.add("Guidelines")

    # ── Temporal validity: extract superseded keys for scoring ──
    superseded = _extract_superseded_keys(memory_content)

    # ── Section scoring: body-BM25 over OPERATIONAL sections (unified-retrieval
    # STEP1, 2026-08-14). Selection no longer READS the in-prompt index — it scores
    # the ## section BODY directly (mirrors DDD's _ddd_section_scores), unifying
    # memory + DDD onto ONE index-free scorer. The index is still GENERATED above
    # (strangler-fig: old path alive-but-unread until STEP5 deletes it). Evergreen
    # is always-injected (CYCLE 2), so only operational sections are scored here.
    # The vector leg was removed 2026-06-28; ``memory_embeddings`` is RETAINED
    # (default False) but inert — asserted by test_memory_wiring.py; removing it
    # would KeyError that guard.
    if user_message:
        if memory_embeddings:
            logger.warning(
                "select_memory_sections called with memory_embeddings=True, but "
                "the vector leg was removed (pure-filesystem design §3.3); using "
                "keyword-only scoring."
            )
        matched_sections = _operational_section_scores(user_message, sections, superseded)

        # Add matched sections (sorted by score, best first)
        for sec_name, _ in sorted(
            matched_sections.items(), key=lambda x: x[1], reverse=True
        ):
            sections_to_load.add(sec_name)

    # ── Load selected sections within token budget ──
    for sec_name in sections_to_load:
        if sec_name in ALWAYS_LOAD_SECTIONS:
            continue  # Already loaded above

        sec_content = sections.get(sec_name, "")
        if not sec_content.strip():
            continue

        sec_text = f"## {sec_name}\n{sec_content}"
        sec_tokens = ContextDirectoryLoader.estimate_tokens(sec_text)

        if used_tokens + sec_tokens <= max_tokens:
            parts.append(sec_text)
            used_tokens += sec_tokens
        # else: skip this section but keep trying smaller ones

    # ── EntryRefs 1-hop loading: pull in referenced sections ──
    # Parse refs: annotations from loaded entries, add referenced
    # sections that aren't already loaded (cap at 3 additional).
    ref_sections = _load_referenced_sections(parts, sections, sections_to_load)
    refs_added = 0
    for sec_name in ref_sections:
        if refs_added >= MAX_REF_SECTIONS:
            break
        sec_content = sections.get(sec_name, "")
        if not sec_content.strip():
            continue
        sec_text = f"## {sec_name}\n{sec_content}"
        sec_tokens = ContextDirectoryLoader.estimate_tokens(sec_text)
        if used_tokens + sec_tokens <= max_tokens:
            parts.append(sec_text)
            used_tokens += sec_tokens
            refs_added += 1

    # ── SessionRecall: supplementary context from past sessions ──
    # Always run when user_message is available — not just as a fallback.
    # SessionRecall adds conversational context that keyword matching misses.
    # Module-level cache avoids re-creating SessionRecall (and its 2
    # sqlite connections) on every session start.  Cap at 2 snippets.
    if user_message:
        try:
            # DB path is the single source of truth for SessionRecall, and
            # _get_session_recall does its own SessionRecall import + caching.
            # (Two dead imports lived here — `SessionRecall` (unused) and
            # `app_config_manager`, which does NOT exist: the module exports the
            # AppConfigManager CLASS / .instance(). The latter raised ImportError
            # on every call and the bare-except silently killed this whole block,
            # so SessionRecall was NEVER injected in prod selective mode. run_edfad326.)
            from jobs.paths import DB_PATH as _db_path_recall
            db_path = _db_path_recall
            if db_path.exists():
                recall = _get_session_recall(db_path)
                recall_text = recall.recall_about(user_message, max_sessions=2)
                if recall_text:
                    recall_tokens = ContextDirectoryLoader.estimate_tokens(recall_text)
                    if used_tokens + recall_tokens <= max_tokens:
                        parts.append(recall_text)
                        used_tokens += recall_tokens
        except Exception as exc:
            # GC19: log the failure type — a bare `except: pass` here is exactly
            # what hid the dead-import bug above. Loud-on-degradation.
            logger.warning("SessionRecall injection skipped: %s: %s",
                           type(exc).__name__, exc)

    # ── Footer: hint about remaining content ──
    loaded_section_names = {
        p.split("\n")[0].replace("## ", "")
        for p in parts
        if p.startswith("## ")
    }
    unloaded = set(sections.keys()) - loaded_section_names - {"Memory"}
    if unloaded:
        # Named manifest (Reversible Context Recall, run_9de88af9): emit the
        # excluded section NAMES — not just a count — plus the recall path, so
        # the agent knows WHICH sections it can retrieve and HOW. Sorted + capped
        # for a stable, ≤1-line, cache-friendly tail (never perturbs the prefix).
        names = sorted(unloaded)
        shown = names[: MANIFEST_MAX_NAMES]
        more = len(names) - len(shown)
        suffix = f" +{more} more" if more > 0 else ""
        parts.append(
            f"\n[Not loaded ({len(unloaded)}): {', '.join(shown)}{suffix} — "
            f"recall_context(\"MEMORY.md\", query) to retrieve, "
            f"or Read .context/MEMORY.md]"
        )

    return "\n\n".join(parts)


# ── Index Injection / Extraction ──────────────────────────────────────


def inject_index_into_memory(content: str) -> str:
    """Insert or replace the index block in MEMORY.md content.

    Places the index block at the very top of the file (before any other
    content), or replaces an existing index block.

    Args:
        content: Full MEMORY.md content.

    Returns:
        Content with index block at the top.
    """
    # Remove existing index if present
    body = extract_body_without_index(content)

    # Generate fresh index from the body content
    index_content = generate_memory_index(body)

    return (
        MEMORY_INDEX_START + "\n"
        + index_content + "\n"
        + MEMORY_INDEX_END + "\n\n"
        + body
    )


def extract_index_from_memory(content: str) -> Optional[str]:
    """Extract the index block from MEMORY.md content.

    Returns the full index block including markers, or None if no index
    block is found.
    """
    start = content.find(MEMORY_INDEX_START)
    end = content.find(MEMORY_INDEX_END)

    if start == -1 or end == -1 or end <= start:
        return None

    return content[start : end + len(MEMORY_INDEX_END)]


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
