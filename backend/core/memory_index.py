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

# Default max tokens for L0+L1 combined injection
DEFAULT_MAX_TOKENS = 10_000

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
    for line in section_content.split("\n"):
        line = line.strip()
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
    counts: dict[str, int] = {}
    for sec_name in SECTION_KEY_PREFIX:
        sec_content = sections.get(sec_name, "")
        entries = _parse_entries(sec_content)
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
            line = f"- [{key}] {date_prefix}{title}"
            if alias_str:
                line += f" | {alias_str}"
            permanent_lines.append(line)

    # ── Build Active tier (Recent Context + Lessons, within age limits) ──
    active_lines: list[str] = []
    archived_count = 0

    for sec_name, age_limit_days in [("Recent Context", 90), ("Lessons Learned", 180)]:
        prefix = SECTION_KEY_PREFIX[sec_name]
        entries = _parse_entries(sections.get(sec_name, ""))
        for i, entry in enumerate(entries, 1):
            key = f"{prefix}{i:02d}"
            aliases = _extract_keywords(entry["full_text"])
            alias_str = ", ".join(aliases) if aliases else ""
            date_prefix = f"{entry['date_str']} " if entry.get("date_str") else ""
            title = entry["title"]

            # Check age
            entry_date = entry.get("date")
            if entry_date and (today - entry_date).days > age_limit_days:
                archived_count += 1
                continue

            line = f"- [{key}] {date_prefix}{title}"
            if alias_str:
                line += f" | {alias_str}"
            active_lines.append(line)

    # ── Build Open Threads entries ──
    ot_lines: list[str] = []
    ot_entries = _parse_entries(sections.get("Open Threads", ""))
    for i, entry in enumerate(ot_entries, 1):
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

    if archived_count > 0:
        lines.append("")
        lines.append(
            f"### Archived ({archived_count} entries — detail available via Read tool)"
        )

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

    Args:
        user_message: The user's first message in the session.
        entry_summary: One-line summary from the index entry.
        aliases: Keyword aliases for this entry.

    Returns:
        Float relevance score (0.0 = no match).
    """
    msg_tokens = set(_tokenize_lower(user_message))
    if not msg_tokens:
        return 0.0

    entry_tokens = set(_tokenize_lower(entry_summary))
    alias_tokens = set(_tokenize_lower(" ".join(aliases)))
    all_matchable = entry_tokens | alias_tokens

    if not all_matchable:
        return 0.0

    title_hits = msg_tokens & entry_tokens
    alias_hits = msg_tokens & alias_tokens

    # Meaningful overlap (already filtered by _tokenize_lower)
    if not title_hits and not alias_hits:
        return 0.0

    # Alias hits weighted 1.5x
    score = (len(title_hits) + len(alias_hits) * 1.5) / len(all_matchable)
    return score


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


def select_memory_sections(
    memory_content: str,
    user_message: str = "",
    session_signals: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """Select MEMORY.md sections for L1 injection.

    Combines rule-based triggers (session signals) with keyword matching
    to select relevant sections.  Always includes the index block and
    Open Threads.

    Args:
        memory_content: Full MEMORY.md content.
        user_message: User's first message (for keyword matching).
        session_signals: Dict with keys like is_channel, is_resume, etc.
        max_tokens: Maximum tokens for the combined L0+L1 output.

    Returns:
        Assembled content string: index + selected sections.
    """
    from .context_directory_loader import ContextDirectoryLoader

    signals = session_signals or {}
    sections = parse_memory_sections(memory_content)

    # ── L0: Always include index ──
    index_block = extract_index_from_memory(memory_content)
    if not index_block:
        # Generate index on the fly if missing
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

    # ── Channel sessions: minimal (index + Open Threads only) ──
    if signals.get("is_channel"):
        parts.append(
            f"\n[Full MEMORY.md available via Read tool — "
            f"{len(sections)} sections not loaded]"
        )
        return "\n\n".join(parts)

    # ── Rule-based section loading ──
    sections_to_load: set[str] = set()

    if signals.get("is_resume"):
        sections_to_load.add("Recent Context")

    if signals.get("has_coe"):
        sections_to_load.add("COE Registry")
        sections_to_load.add("Lessons Learned")

    if signals.get("is_first_session_today"):
        sections_to_load.add("Recent Context")

    # ── Keyword matching against index entries ──
    if user_message:
        index_entries = _parse_index_entries(index_block)
        matched_sections: dict[str, float] = {}

        for entry in index_entries:
            score = keyword_relevance(
                user_message, entry["summary"], entry["aliases"]
            )
            if score >= KEYWORD_THRESHOLD:
                # Determine which section this entry belongs to
                key = entry["key"]
                if key.startswith("RC"):
                    sec_name = "Recent Context"
                elif key.startswith("KD"):
                    sec_name = "Key Decisions"
                elif key.startswith("LL"):
                    sec_name = "Lessons Learned"
                elif key.startswith("COE"):
                    sec_name = "COE Registry"
                else:
                    continue

                if sec_name not in matched_sections or score > matched_sections[sec_name]:
                    matched_sections[sec_name] = score

        # Add keyword-matched sections (sorted by score, best first)
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
        else:
            # Budget exceeded — skip remaining sections
            break

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
