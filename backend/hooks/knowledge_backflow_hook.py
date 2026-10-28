"""Knowledge Backflow Hook — auto-capture high-value session outputs as wiki pages.

Implements the "query result backflow" pattern from Karpathy's LLM Wiki:
good answers get filed back into the wiki as new pages, so explorations
compound in the knowledge base just like ingested sources do.

Fires after DailyActivity extraction. Scans assistant messages for
high-value outputs (deep analyses, comparisons, syntheses) and submits
self-contained Backflow candidates to Cognitive Distillation Flow.

Key public symbols:

- ``KnowledgeBackflowHook``  — Implements ``SessionLifecycleHook``.
- ``_is_high_value_output``  — Heuristic detector for wiki-worthy content.
- ``_generate_slug``         — Title-to-filename slug generator.
- ``_build_knowledge_page``  — Formats content as a Knowledge page with frontmatter.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime

from core.session_hooks import HookContext
from database import db

logger = logging.getLogger(__name__)

# ─── Detection thresholds ───

# Minimum word count for high-value consideration (prose only, excluding code)
_MIN_WORDS = 500
_TITLE_MAX = 120          # a first line longer than this is not used as a title

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

    # Fallback: the first non-empty line that is not a markdown marker, whole, when it
    # is short enough to be a title; a title is never a cut-down sentence
    for line in prose.splitlines():
        line = line.strip()
        if line and not line.startswith(("```", "---", "|", ">")):
            return line if len(line) <= _TITLE_MAX else "Session Insight"

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
        # 1. Every message of the session, in order (the DailyActivity hook's reader):
        #    the 500 most recent left an earlier message of a long session unread
        from hooks.daily_activity_hook import _read_complete_transcript
        messages = await _read_complete_transcript(context.session_id, repo=db.messages)

        if not messages:
            return

        # 2. Scan assistant messages for high-value content
        candidates: list[tuple[str, str, str]] = []  # (content, title, message_id)

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
                message_id = str(msg.get("id") or hashlib.sha256(content.encode()).hexdigest())
                candidates.append((content, title, message_id))

        if not candidates:
            return

        # 3. Enqueue top candidates (capped at MAX per session). Prefer later
        # messages because they tend to be the refined/final version. Backflow
        # cannot yet map an assistant row to exact input events, so §9 requires
        # the conservative whole-session ledger fallback.
        from core.ingestion import api
        from core.session_inputs import derive_envelope

        candidates = candidates[-_MAX_CAPTURES_PER_SESSION:]
        envelope = derive_envelope(context.session_id, (), writer="backflow")
        queued = 0
        for content, title, message_id in candidates:
            result = api.enqueue(
                "backflow",
                message_id,
                "document",
                {"kind": "backflow", "title": title, "body": content,
                 "message_id": message_id},
                envelope=envelope,
                project_snapshot={},
            )
            if result.status in {"queued", "duplicate"}:
                queued += 1
        if queued:
            logger.info(
                "KnowledgeBackflowHook: %d candidate(s) queued for session %s",
                queued, context.session_id,
            )
