"""Knowledge Backflow Hook — auto-capture high-value session outputs as wiki pages.

Implements the "query result backflow" pattern from Karpathy's LLM Wiki:
good answers get filed back into the wiki as new pages, so explorations
compound in the knowledge base just like ingested sources do.

Fires after DailyActivity extraction. Scans assistant messages for
high-value outputs (deep analyses, comparisons, syntheses) and persists
them as Knowledge/Notes/ pages with source metadata.

Key public symbols:

- ``KnowledgeBackflowHook``  — Implements ``SessionLifecycleHook``.
- ``_is_high_value_output``  — Heuristic detector for wiki-worthy content.
- ``_generate_slug``         — Title-to-filename slug generator.
- ``_build_knowledge_page``  — Formats content as a Knowledge page with frontmatter.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

from core.session_hooks import HookContext
from database import db
from jobs.paths import SWARMWS

logger = logging.getLogger(__name__)

# ─── Detection thresholds ───

# Minimum word count for high-value consideration (prose only, excluding code)
_MIN_WORDS = 500

# Structural markers that indicate analytical content (not just prose)
_ANALYSIS_MARKERS = re.compile(
    r"(?:"
    r"##\s+(?:Analysis|Comparison|Summary|Findings|Research|Conclusion|Recommendation|Key\s+(?:Takeaway|Insight|Difference))"
    r"|(?:root\s+cause|trade-?off|architectural|the\s+evidence\s+suggests|based\s+on\s+(?:this\s+)?analysis)"
    r"|\|[^|]+\|[^|]+\|"  # markdown table row
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Negative markers — content that's mostly code, not analysis
_CODE_HEAVY = re.compile(r"```[\s\S]*?```", re.MULTILINE)

# Maximum outputs to capture per session (prevent flood)
_MAX_CAPTURES_PER_SESSION = 3

# Notes directory relative to SwarmWS
_NOTES_DIR = "Knowledge/Notes"


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks from text, returning prose only."""
    return _CODE_HEAVY.sub("", text)


def _is_high_value_output(text: str) -> bool:
    """Determine if an assistant text block is wiki-worthy.

    Criteria (ALL must be true):
    1. Prose word count >= 500 (code blocks excluded)
    2. Contains 2+ analysis markers in prose (structural indicator of synthesis)
    3. Code blocks are <50% of content (analysis, not code dump)

    Returns True if the content should be captured as a Knowledge page.
    """
    if not text or not text.strip():
        return False

    # Strip code blocks for prose analysis
    code_blocks = _CODE_HEAVY.findall(text)
    code_chars = sum(len(b) for b in code_blocks)
    total_chars = len(text)

    # Code-heavy content (>50% code) is not an analysis
    if total_chars > 0 and code_chars / total_chars > 0.5:
        return False

    # Prose-only text (code blocks removed) for word count and markers
    prose = _strip_code_blocks(text)

    # Word count on PROSE only — code tokens don't count as analysis
    words = prose.split()
    if len(words) < _MIN_WORDS:
        return False

    # Analysis marker check on prose — avoids false positives from pipes in code
    markers = _ANALYSIS_MARKERS.findall(prose)
    if len(markers) < 2:
        return False

    return True


def _generate_slug(title: str) -> str:
    """Generate a URL-safe filename slug from a title.

    Strips non-ASCII (except keeps basic transliteration), lowercases,
    replaces spaces with hyphens, and caps length at 60 chars.

    Falls back to 'session-insight' if title is empty or produces
    no valid slug characters (e.g., pure CJK titles).
    """
    if not title:
        return "session-insight"

    # Normalize unicode, strip non-ASCII
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Lowercase, replace non-alnum with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")

    # Remove consecutive hyphens
    slug = re.sub(r"-+", "-", slug)

    # Cap length
    if len(slug) > 60:
        slug = slug[:60].rstrip("-")

    return slug if slug else "session-insight"


def _extract_title(content: str) -> str:
    """Extract a title from the content.

    Looks for the first H1/H2 heading in prose (outside code blocks).
    Falls back to first significant line.
    """
    # Search in prose only — avoid matching headings inside code blocks
    prose = _strip_code_blocks(content)

    # Try H1/H2 heading
    heading = re.search(r"^#{1,2}\s+(.+)$", prose, re.MULTILINE)
    if heading:
        return heading.group(1).strip()

    # Fallback: first non-empty line that's not a markdown marker
    for line in prose.splitlines():
        line = line.strip()
        if line and not line.startswith(("```", "---", "|", ">")):
            return line[:80]

    return "Session Insight"


def _sanitize_yaml_string(value: str) -> str:
    """Escape a string for safe YAML double-quoted inclusion.

    Prevents YAML injection via titles containing quotes, backslashes,
    or newlines.
    """
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def _build_knowledge_page(
    content: str,
    session_id: str,
    title: str,
    date_str: str | None = None,
) -> str:
    """Build a Knowledge/Notes page with YAML frontmatter.

    Args:
        content: The high-value text to persist.
        session_id: Source session for traceability.
        title: Page title (extracted or generated).
        date_str: ISO date string (YYYY-MM-DD). If None, uses today.

    Returns:
        Full markdown page with frontmatter + content.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    safe_title = _sanitize_yaml_string(title)

    frontmatter = (
        "---\n"
        f'title: "{safe_title}"\n'
        f"date: {date_str}\n"
        f"source: session\n"
        f"session_id: {session_id}\n"
        f"auto_captured: true\n"
        "---\n\n"
    )

    return frontmatter + content


def _atomic_write(filepath: Path, content: str) -> None:
    """Write content to file atomically via temp file + rename.

    Prevents partial/corrupted files if process is killed mid-write.
    """
    dir_path = filepath.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(filepath))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class KnowledgeBackflowHook:
    """Captures high-value session outputs as persistent Knowledge pages.

    Implements the Karpathy LLM Wiki pattern: 'good answers filed back
    into wiki as new pages.' Fires after DailyActivity extraction.

    Design:
    - Scans assistant messages for substantive analysis (>500 words + markers)
    - Writes top 3 qualifying outputs per session to Knowledge/Notes/
    - Non-blocking: all errors caught, never prevents other hooks
    - Deduplication: filename includes content hash (date + slug + hash6)
    - Atomic writes: temp file + rename prevents corruption
    """

    name = "knowledge_backflow"

    async def execute(self, context: HookContext) -> None:
        """Scan session messages and capture high-value outputs."""
        try:
            await self._execute_inner(context)
        except Exception as exc:
            # Non-blocking: log but never propagate
            logger.warning(
                "KnowledgeBackflowHook failed for session %s: %s",
                context.session_id, exc,
            )

    async def _execute_inner(self, context: HookContext) -> None:
        """Core logic — isolated for testability."""
        # 1. Retrieve conversation messages (same limit as DailyActivity hook —
        #    intentional: 500 most recent messages covers typical sessions)
        messages = await db.messages.list_by_session_paginated(
            context.session_id, limit=500
        )

        if not messages:
            return

        # 2. Scan assistant messages for high-value content
        candidates: list[tuple[str, str]] = []  # (content, title)

        for msg in messages:
            if msg.get("role") != "assistant":
                continue

            content = msg.get("content", "")
            if not isinstance(content, str):
                # Content might be a list of blocks — extract text
                if isinstance(content, list):
                    content = "\n".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                else:
                    continue

            if _is_high_value_output(content):
                title = _extract_title(content)
                candidates.append((content, title))

        if not candidates:
            return

        # 3. Write top candidates (capped at MAX per session)
        #    Prefer LATER messages (tend to be more refined/final versions)
        candidates = candidates[-_MAX_CAPTURES_PER_SESSION:]

        notes_dir = Path(SWARMWS) / _NOTES_DIR
        notes_dir.mkdir(parents=True, exist_ok=True)

        # Compute date_str ONCE to avoid TOCTOU race at midnight
        date_str = datetime.now().strftime("%Y-%m-%d")
        captured = 0

        for content, title in candidates:
            slug = _generate_slug(title)
            # Include content hash for dedup — different content with same title
            # gets separate files; same content on re-run is deduplicated.
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:6]
            filename = f"{date_str}-{slug}-{content_hash}.md"
            filepath = notes_dir / filename

            # Dedup: skip if file already exists (same day + slug + content hash)
            if filepath.exists():
                logger.debug(
                    "Knowledge backflow: %s already exists, skipping", filename
                )
                continue

            page = _build_knowledge_page(
                content=content,
                session_id=context.session_id,
                title=title,
                date_str=date_str,
            )

            _atomic_write(filepath, page)
            captured += 1
            logger.info(
                "Knowledge backflow: captured '%s' → %s", title, filename
            )

        if captured:
            logger.info(
                "KnowledgeBackflowHook: %d page(s) captured for session %s",
                captured, context.session_id,
            )
