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

import json
import re
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

MEMORY_INDEX_START = "<!-- MEMORY_INDEX_START -->"
MEMORY_INDEX_END = "<!-- MEMORY_INDEX_END -->"

# Sections whose entries never age out of the index (Permanent tier)
PERMANENT_SECTIONS = {"COE Registry", "Key Decisions"}

# Sections that appear in the Active tier
ACTIVE_SECTIONS = {"Recent Context", "Lessons Learned"}

# Section always loaded in L1 regardless of matching
ALWAYS_LOAD_SECTIONS = {"Open Threads"}

# Keyword relevance threshold for L1 section loading
KEYWORD_THRESHOLD = 0.15

# Default max tokens for selective injection (only used above threshold)
DEFAULT_MAX_TOKENS = 50_000

# Full-injection threshold: below this, inject entire MEMORY.md.
# At 30K tokens (~375 entries), MEMORY.md uses 30% of 100K system prompt budget.
# Below this, Claude reads everything — no selection needed.
FULL_INJECTION_THRESHOLD = 30_000

# Maximum additional sections to load via EntryRefs 1-hop expansion.
# Scale up as MEMORY.md grows and token budget allows.
MAX_REF_SECTIONS = 3

# Prefix patterns for index entry keys
SECTION_KEY_PREFIX = {
    "Recent Context": "RC",
    "Key Decisions": "KD",
    "Lessons Learned": "LL",
    "COE Registry": "COE",
    "Open Threads": "OT",
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


def _tokenize_lower(text: str) -> list[str]:
    """Split text into lowercase tokens, filtering short/stop words."""
    # Split on whitespace and punctuation, keep alphanumeric + hyphens
    tokens = re.findall(r"[a-zA-Z0-9_\-]+", text.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOP_WORDS]


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
    that would help with recall.
    """
    tokens = re.findall(r"[a-zA-Z0-9_\-]+", entry_text)

    # Score tokens by distinctiveness
    scored: dict[str, float] = {}
    for token in tokens:
        t_lower = token.lower()
        if len(t_lower) <= 2 or t_lower in _STOP_WORDS:
            continue

        score = 0.0
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
    # Extract full reference IDs (e.g. "COE02", "KD01") from bracketed refs
    full_refs = re.findall(r"\[(COE\d+|KD\d+|RC\d+|LL\d+|OT\d+)\]", entry_text)
    unique = sorted(set(full_refs) - {self_key})
    return unique


# Mapping from ref prefix → MEMORY.md section name
_REF_PREFIX_TO_SECTION: dict[str, str] = {
    "COE": "COE Registry",
    "KD": "Key Decisions",
    "RC": "Recent Context",
    "LL": "Lessons Learned",
    "OT": "Open Threads",
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
        Complete index block including start/end markers.
    """
    sections = parse_memory_sections(content)
    today = date.today()

    # ── Count entries per section ──
    # For Open Threads, only count active entries (exclude ✅ resolved).
    counts: dict[str, int] = {}
    for sec_name in SECTION_KEY_PREFIX:
        sec_content = sections.get(sec_name, "")
        entries = _parse_entries(sec_content)
        if sec_name == "Open Threads":
            entries = [e for e in entries if "\u2705" not in e["full_text"]]
        counts[sec_name] = len(entries)

    # ── Build Permanent tier (COEs + Key Decisions) ──
    permanent_lines: list[str] = []
    for sec_name in ("COE Registry", "Key Decisions"):
        prefix = SECTION_KEY_PREFIX[sec_name]
        entries = _parse_entries(sections.get(sec_name, ""))
        for i, entry in enumerate(entries, 1):
            key = f"{prefix}{i:02d}"
            aliases = _extract_keywords(entry["full_text"])
            alias_str = ", ".join(aliases) if aliases else ""
            date_prefix = f"{entry['date_str']} " if entry.get("date_str") else ""
            title = entry["title"]
            refs = _extract_refs(entry["full_text"], key)
            line = f"- [{key}] {date_prefix}{title}"
            if refs:
                line += f" | refs: {', '.join(refs)}"
            if alias_str:
                line += f" | {alias_str}"
            permanent_lines.append(line)

    # ── Build Active tier (Recent Context + Lessons — no age cutoff) ──
    # Power-first: every entry stays in Active tier until explicitly
    # archived by LLM maintenance (superseded by newer entry).
    active_lines: list[str] = []

    for sec_name in ("Recent Context", "Lessons Learned"):
        prefix = SECTION_KEY_PREFIX[sec_name]
        entries = _parse_entries(sections.get(sec_name, ""))
        for i, entry in enumerate(entries, 1):
            key = f"{prefix}{i:02d}"
            aliases = _extract_keywords(entry["full_text"])
            alias_str = ", ".join(aliases) if aliases else ""
            date_prefix = f"{entry['date_str']} " if entry.get("date_str") else ""
            title = entry["title"]
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
        title = entry["title"]
        ot_lines.append(f"- [{key}] {title}")

    # ── Assemble index ──
    count_parts = []
    for sec_name, count in counts.items():
        if count > 0:
            count_parts.append(f"{count} {sec_name.lower()}")

    header = " | ".join(count_parts) if count_parts else "empty"

    lines = [
        MEMORY_INDEX_START,
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

    lines.append(MEMORY_INDEX_END)

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

    title_hits = msg_tokens & entry_tokens
    alias_hits = msg_tokens & alias_tokens

    # Meaningful overlap (already filtered by _tokenize_lower)
    if not title_hits and not alias_hits:
        return 0.0

    # Denominator = user's message tokens.  This measures "what fraction of
    # the user's query was satisfied by this entry" — a recall metric.
    # Short queries ("COE", "sandbox") score high on exact match instead of
    # being diluted by alias-rich entries (old bug: max(msg, entry) as
    # denominator made 2-token queries score ~0.15 against 20-alias entries).
    # Cap at 1.0 since alias_hits * 1.5 can exceed denominator.
    score = (len(title_hits) + len(alias_hits) * 1.5) / len(msg_tokens)
    return min(score, 1.0)


# ── Section Selection ─────────────────────────────────────────────────


def _parse_index_entries(index_block: str) -> list[dict]:
    """Parse index entries from an index block.

    Returns list of dicts with: key, summary, aliases.
    """
    entries = []
    for line in index_block.split("\n"):
        line = line.strip()
        # Match: - [KEY] summary | alias1, alias2
        m = re.match(r"^- \[(\w+)\]\s+(.+)$", line)
        if not m:
            continue

        key = m.group(1)
        rest = m.group(2)

        # Split on | for aliases
        if "|" in rest:
            summary, alias_str = rest.split("|", 1)
            aliases = [a.strip() for a in alias_str.split(",") if a.strip()]
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

_KEY_TO_SECTION = {
    "RC": "Recent Context",
    "KD": "Key Decisions",
    "LL": "Lessons Learned",
    "COE": "COE Registry",
}


def _key_to_section(key: str) -> Optional[str]:
    """Map an index entry key like 'RC01' or 'COE03' to a section name."""
    for prefix, sec_name in _KEY_TO_SECTION.items():
        if key.startswith(prefix):
            return sec_name
    return None


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
) -> dict[str, float]:
    """Score sections by keyword matching, applying temporal weight.

    Entries marked as superseded (via P2 temporal validity) score at
    0.1x weight, preventing stale decisions from dominating section selection.
    """
    index_entries = _parse_index_entries(index_block)
    matched: dict[str, float] = {}
    _superseded = superseded_keys or set()

    for entry in index_entries:
        score = keyword_relevance(
            user_message, entry["summary"], entry["aliases"]
        )
        # Apply temporal weight: superseded entries get 0.1x
        if entry["key"] in _superseded:
            score *= _entry_temporal_weight(
                "<!-- valid_from: x | superseded_by: Y | confidence: high -->"
            )
        if score >= KEYWORD_THRESHOLD:
            sec_name = _key_to_section(entry["key"])
            if sec_name and (sec_name not in matched or score > matched[sec_name]):
                matched[sec_name] = score

    return matched


def _hybrid_section_scores(user_message: str) -> dict[str, float]:
    """Score sections using hybrid vector+keyword search.

    Queries the memory_vec SQLite table for vector similarity, combines
    with keyword scores, and returns section-level max scores.

    Falls back to empty dict on any failure (caller will use keyword-only).
    """
    import sqlite3 as _sqlite3
    from pathlib import Path

    db_path = Path.home() / ".swarm-ai" / "data.db"
    if not db_path.exists():
        return {}

    try:
        import sqlite_vec
        from .memory_embeddings import MemoryEmbeddingStore, hybrid_memory_search
        from .embedding_client import EmbeddingClient

        conn = _sqlite3.connect(str(db_path))
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

            store = MemoryEmbeddingStore(conn)

            # Check if tables exist and have data
            try:
                count = conn.execute("SELECT COUNT(*) FROM memory_vec").fetchone()[0]
            except _sqlite3.OperationalError:
                return {}

            if count == 0:
                return {}

            # Get vector scores
            client = EmbeddingClient()
            query_embedding = client.embed_text(user_message)

            vector_scores: dict[str, float] = {}
            if query_embedding is not None:
                raw = store.vector_search_raw(query_embedding, top_k=20)
                # sqlite-vec cosine distance = 2*(1-cos_sim). Convert to similarity.
                vector_scores = {key: max(0.0, 1.0 - dist / 2.0) for key, dist in raw}

            # Get keyword scores + entry→section mapping from stored entries
            keyword_scores: dict[str, float] = {}
            entry_sections: dict[str, str] = {}
            rows = conn.execute(
                "SELECT key, section, title, keywords FROM memory_entries"
            ).fetchall()
            for row in rows:
                key, section, title, kw_json = row
                entry_sections[key] = section
                aliases = json.loads(kw_json) if kw_json else []
                score = keyword_relevance(user_message, title, aliases)
                if score > 0:
                    keyword_scores[key] = score
        finally:
            conn.close()

        # Hybrid merge
        ranked = hybrid_memory_search(
            keyword_scores=keyword_scores,
            vector_scores=vector_scores,
        )

        # Aggregate to section level (max score per section)
        section_scores: dict[str, float] = {}
        for entry in ranked:
            sec_name = entry_sections.get(entry.key) or _key_to_section(entry.key)
            if sec_name:
                if sec_name not in section_scores or entry.hybrid > section_scores[sec_name]:
                    section_scores[sec_name] = entry.hybrid

        return section_scores

    except Exception as exc:
        logger.warning("Hybrid section scoring failed: %s", exc)
        return {}


# ── Full Injection Helpers ────────────────────────────────────────────


def _full_injection(memory_content: str) -> str:
    """Full injection mode: index + entire MEMORY.md body.

    Power-first: Claude reads everything. No selection, no filtering.
    The index serves as a navigation table of contents.
    """
    index_block = extract_index_from_memory(memory_content)
    if not index_block:
        index_block = generate_memory_index(memory_content)

    body = extract_body_without_index(memory_content)
    if not body.strip():
        return index_block

    return index_block + "\n\n" + body


def _channel_minimal(memory_content: str) -> str:
    """Channel sessions: index + Open Threads only (no personal sections)."""
    index_block = extract_index_from_memory(memory_content)
    if not index_block:
        index_block = generate_memory_index(memory_content)

    sections = parse_memory_sections(memory_content)
    parts = [index_block]

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

    # ── L0: Always include index ──
    index_block = extract_index_from_memory(memory_content)
    if not index_block:
        index_block = generate_memory_index(memory_content)

    parts: list[str] = [index_block]
    used_tokens = ContextDirectoryLoader.estimate_tokens(index_block)

    # ── Always load: Open Threads ──
    ot_content = sections.get("Open Threads", "")
    if ot_content.strip():
        ot_section = f"## Open Threads\n{ot_content}"
        ot_tokens = ContextDirectoryLoader.estimate_tokens(ot_section)
        if used_tokens + ot_tokens <= max_tokens:
            parts.append(ot_section)
            used_tokens += ot_tokens

    # ── Rule-based section loading ──
    sections_to_load: set[str] = set()

    if signals.get("is_resume"):
        sections_to_load.add("Recent Context")

    if signals.get("has_coe"):
        sections_to_load.add("COE Registry")
        sections_to_load.add("Lessons Learned")

    if signals.get("is_first_session_today"):
        sections_to_load.add("Recent Context")

    # ── Temporal validity: extract superseded keys for scoring ──
    superseded = _extract_superseded_keys(memory_content)

    # ── Section scoring: hybrid (vector+keyword) or keyword-only ──
    if user_message:
        if memory_embeddings:
            # Try hybrid scoring; fall back to keyword-only on any failure
            # or when hybrid returns empty (no embeddings in DB yet)
            try:
                matched_sections = _hybrid_section_scores(user_message)
            except Exception as exc:
                logger.warning("Hybrid scoring failed, falling back to keyword: %s", exc)
                matched_sections = {}

            # Hybrid empty = no embeddings yet or Bedrock down.
            # Merge in keyword results so we never lose recall.
            kw_sections = _keyword_section_scores(user_message, index_block, superseded)
            for sec, score in kw_sections.items():
                if sec not in matched_sections or score > matched_sections[sec]:
                    matched_sections[sec] = score
        else:
            matched_sections = _keyword_section_scores(user_message, index_block, superseded)

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
            from core.session_recall import SessionRecall
            from core.app_config_manager import app_config_manager
            # Resolve DB path from config (single source of truth),
            # falling back to the default location.
            data_dir = Path(
                app_config_manager.get("data_dir", str(Path.home() / ".swarm-ai"))
                if app_config_manager is not None
                else str(Path.home() / ".swarm-ai")
            )
            db_path = data_dir / "data.db"
            if db_path.exists():
                recall = _get_session_recall(db_path)
                recall_text = recall.recall_about(user_message, max_sessions=2)
                if recall_text:
                    recall_tokens = ContextDirectoryLoader.estimate_tokens(recall_text)
                    if used_tokens + recall_tokens <= max_tokens:
                        parts.append(recall_text)
                        used_tokens += recall_tokens
        except Exception:
            pass  # Graceful degradation

    # ── Footer: hint about remaining content ──
    loaded_section_names = {
        p.split("\n")[0].replace("## ", "")
        for p in parts
        if p.startswith("## ")
    }
    unloaded = set(sections.keys()) - loaded_section_names - {"Memory"}
    if unloaded:
        parts.append(
            f"\n[Full MEMORY.md available via Read tool — "
            f"{len(unloaded)} sections not loaded]"
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
    index_block = generate_memory_index(body)

    return index_block + "\n\n" + body


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

    Returns the original content if no index block is present.
    """
    start = content.find(MEMORY_INDEX_START)
    end = content.find(MEMORY_INDEX_END)

    if start == -1 or end == -1 or end <= start:
        return content

    # Remove from start marker to end marker (inclusive) + trailing whitespace
    after_index = content[end + len(MEMORY_INDEX_END):]
    before_index = content[:start]

    result = before_index + after_index
    # Clean up extra blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    # If stripping the index left nothing meaningful, return as-is
    # (avoids returning original content with index still in it)
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
