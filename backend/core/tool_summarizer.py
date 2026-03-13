"""Tool call summarization and result truncation for SSE/DB optimization.

This module provides pure functions for converting raw tool call inputs into
short human-readable summaries and truncating large tool result content.
The Claude Agent SDK manages full conversation context internally; these
functions only transform content for the UI replay path (SSE + SQLite).

- ``summarize_tool_use``       — Generates ≤200-char summary from tool name + input
- ``get_tool_category``        — Returns category string for icon mapping on frontend
- ``truncate_tool_result``     — Truncates content to configurable limit, sets flag
- ``_sanitize_command``        — Redacts sensitive tokens from bash command strings
- ``_extract_mcp_tool_name``   — Extracts server/tool from ``mcp__Server__tool`` names
- ``MAX_SUMMARY_LENGTH``       — 200 characters
- ``DEFAULT_TRUNCATION_LIMIT`` — 500 characters
- ``SENSITIVE_PATTERNS``       — Compiled regexes for token redaction
- ``TRUNCATION_LIMIT``         — Effective limit (env var override or default)
"""

import os as _os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SUMMARY_LENGTH: int = 200
DEFAULT_TRUNCATION_LIMIT: int = 500

# Read env var override ONCE at module load time (not per-call).
# Invalid values fall back to the default.
try:
    _env_limit = _os.environ.get("TOOL_RESULT_TRUNCATION_LIMIT")
    TRUNCATION_LIMIT: int = (
        int(_env_limit) if _env_limit else DEFAULT_TRUNCATION_LIMIT
    )
except (ValueError, TypeError):
    TRUNCATION_LIMIT: int = DEFAULT_TRUNCATION_LIMIT

# ---------------------------------------------------------------------------
# Sensitive token redaction patterns
# ---------------------------------------------------------------------------

SENSITIVE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(token|bearer)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(aws[_-]?secret|aws[_-]?access)\s*[=:]\s*\S+"),
]

# ---------------------------------------------------------------------------
# Tool name → category mapping (case-insensitive via name.lower())
# ---------------------------------------------------------------------------

_BASH_NAMES: set[str] = {"bash"}
_READ_NAMES: set[str] = {"read", "readfile", "view"}
_WRITE_NAMES: set[str] = {"write", "writefile", "create", "edit"}
_SEARCH_NAMES: set[str] = {"grep", "search", "find", "glob"}
_TODOWRITE_NAMES: set[str] = {"todowrite"}
_WEB_FETCH_NAMES: set[str] = {"webfetch", "fetch", "httpget", "urlget"}
_WEB_SEARCH_NAMES: set[str] = {"websearch"}
_TOOL_SEARCH_NAMES: set[str] = {"toolsearch"}
_SKILL_NAMES: set[str] = {"skill"}
_LIST_DIR_NAMES: set[str] = {"listdirectory", "ls", "listdir"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _sanitize_command(command: str) -> str:
    """Redact sensitive tokens from a bash command string.

    Iterates ``SENSITIVE_PATTERNS`` and replaces each match with
    ``[REDACTED]``.  If a pattern fails on malformed input the original
    command is returned unchanged (fail-open for display purposes).
    """
    try:
        sanitized = command
        for pattern in SENSITIVE_PATTERNS:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized
    except Exception:
        # Fail-open: return original command if regex fails
        return command


def _extract_mcp_tool_name(name: str) -> tuple[str | None, str]:
    """Extract server name and tool name from MCP tool name format.

    MCP tools follow the ``mcp__ServerName__tool_name`` pattern.
    Returns ``(server_name, tool_name)`` for MCP tools, or
    ``(None, name)`` for non-MCP tools.

    Edge cases:
    - Empty name → ``(None, "")``
    - ``"mcp__"`` only → ``(None, "mcp__")``
    - 4+ segments → last segment as tool_name, second as server_name
    """
    segments = name.split("__")
    if segments[0].lower() == "mcp" and len(segments) >= 3:
        return (segments[1], segments[-1])
    return (None, name)


# All category sets collected for token-based matching
_CATEGORY_SETS: list[tuple[str, set[str]]] = [
    ("bash", _BASH_NAMES),
    ("read", _READ_NAMES),
    ("write", _WRITE_NAMES),
    ("search", _SEARCH_NAMES),
    ("web_fetch", _WEB_FETCH_NAMES),
    ("web_search", _WEB_SEARCH_NAMES),
    ("tool_search", _TOOL_SEARCH_NAMES),
    ("skill", _SKILL_NAMES),
    ("list_dir", _LIST_DIR_NAMES),
    ("todowrite", _TODOWRITE_NAMES),
]


def _match_last_token_category(tool_name: str) -> str | None:
    """Match the LAST underscore-delimited token of *tool_name* against
    category sets.

    Only the last token is checked to avoid collisions (e.g.,
    ``bash_runner`` → ``runner`` → no match, NOT ``bash``).

    Returns the category string if matched, else ``None``.
    """
    tokens = tool_name.lower().split("_")
    last_token = tokens[-1] if tokens else ""
    if not last_token:
        return None
    for category, name_set in _CATEGORY_SETS:
        if last_token in name_set:
            return category
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def summarize_tool_use(name: str, input_data: Optional[dict]) -> str:
    """Generate a human-readable summary from tool name and input fields.

    Uses ``name.lower()`` for case-insensitive category matching against
    single lowercase sets.  Logs the chosen category at DEBUG level for
    troubleshooting summary generation.

    Args:
        name: The tool name (e.g., "Bash", "Read", "Write").
        input_data: The tool input dict (may be None).

    Returns:
        A summary string, guaranteed ≤ MAX_SUMMARY_LENGTH characters.
    """
    data = input_data if input_data else {}
    lower_name = name.lower()

    # Determine category and build summary
    if lower_name in _BASH_NAMES:
        category = "bash"
        cmd = data.get("command", "")
        sanitized_cmd = _sanitize_command(cmd) if cmd else ""
        summary = f"Running: {sanitized_cmd}" if sanitized_cmd else f"Using {name}"
    elif lower_name in _READ_NAMES:
        category = "read"
        path = data.get("path") or data.get("file_path", "")
        summary = f"Reading {path}" if path else f"Using {name}"
    elif lower_name in _WRITE_NAMES:
        category = "write"
        path = data.get("path") or data.get("file_path", "")
        summary = f"Writing to {path}" if path else f"Using {name}"
    elif lower_name in _SEARCH_NAMES:
        category = "search"
        pattern = data.get("pattern") or data.get("query", "")
        summary = f"Searching for {pattern}" if pattern else f"Using {name}"
    elif lower_name in _WEB_FETCH_NAMES:
        category = "web_fetch"
        url = data.get("url") or data.get("uri", "")
        summary = f"Fetching {url}" if url else f"Using {name}"
    elif lower_name in _WEB_SEARCH_NAMES:
        category = "web_search"
        query = data.get("query") or data.get("search_query") or data.get("q", "")
        summary = f"Searching web for {query}" if query else f"Using {name}"
    elif lower_name in _TOOL_SEARCH_NAMES:
        category = "tool_search"
        raw_query = data.get("query") or ""
        # Strip the "select:" prefix the SDK prepends to tool selection queries
        # e.g. "select:Bash,Read,Grep" → "Bash, Read, Grep"
        if raw_query.startswith("select:"):
            tools_csv = raw_query[len("select:"):]
            display_query = ", ".join(t.strip() for t in tools_csv.split(",") if t.strip())
        else:
            display_query = raw_query
        summary = f"Loading tools: {display_query}" if display_query else f"Using {name}"
    elif lower_name in _SKILL_NAMES:
        category = "skill"
        skill_name = (
            data.get("skill_name")
            or data.get("skillName")
            or data.get("skill_folder")
            or ""
        )
        if not skill_name:
            # Fallback: try "name" but only if it doesn't look like a generic
            # tool name (the SDK sets name="Skill" which is unhelpful).
            raw_name = data.get("name", "")
            if raw_name and raw_name.lower() != "skill":
                skill_name = raw_name
        summary = f"Using skill: {skill_name}" if skill_name else f"Using {name}"
    elif lower_name in _LIST_DIR_NAMES:
        category = "list_dir"
        path = data.get("path") or data.get("directory", "")
        summary = f"Listing {path}" if path else f"Using {name}"
    elif lower_name in _TODOWRITE_NAMES:
        category = "todowrite"
        todos = data.get("todos", [])
        summary = f"Writing {len(todos)} todos"
    else:
        # --- MCP detection + last-token category matching ---
        server_name, tool_name_extracted = _extract_mcp_tool_name(name)

        if server_name is not None:
            # MCP tool detected — try last-token category matching
            matched_category = _match_last_token_category(tool_name_extracted)

            if matched_category is not None:
                category = matched_category
            else:
                category = "fallback"

            # MCP labels always use the clean tool_name format
            context = (
                data.get("query")
                or data.get("url")
                or data.get("path")
                or data.get("file_path")
                or data.get("command")
                or data.get("name")
                or data.get("title")
            )
            if context:
                summary = f"mcp: {tool_name_extracted} \u2014 {context}"
            else:
                summary = f"mcp: {tool_name_extracted}"
        else:
            # Non-MCP fallback (existing behavior unchanged)
            category = "fallback"
            context = (
                data.get("query")
                or data.get("url")
                or data.get("path")
                or data.get("file_path")
                or data.get("command")
                or data.get("name")
            )
            if context:
                summary = f"{name}: {context}"
            else:
                summary = f"Using {name}"

    logger.debug("Summarizer: %s → category=%s", name, category)

    # Truncate to MAX_SUMMARY_LENGTH and guarantee non-empty
    summary = summary[:MAX_SUMMARY_LENGTH]
    if not summary:
        summary = f"Using {name}"[:MAX_SUMMARY_LENGTH]

    return summary


def get_tool_category(name: str) -> str:
    """Return a category string for the given tool name.

    Used by ``_format_message()`` to emit a ``category`` field alongside
    ``summary`` so the frontend can pick a per-category icon without
    duplicating the name-matching logic.

    Categories: bash, read, write, search, web_fetch, web_search,
    list_dir, todowrite, fallback.
    """
    lower_name = name.lower()
    if lower_name in _BASH_NAMES:
        return "bash"
    if lower_name in _READ_NAMES:
        return "read"
    if lower_name in _WRITE_NAMES:
        return "write"
    if lower_name in _SEARCH_NAMES:
        return "search"
    if lower_name in _WEB_FETCH_NAMES:
        return "web_fetch"
    if lower_name in _WEB_SEARCH_NAMES:
        return "web_search"
    if lower_name in _TOOL_SEARCH_NAMES:
        return "tool_search"
    if lower_name in _SKILL_NAMES:
        return "skill"
    if lower_name in _LIST_DIR_NAMES:
        return "list_dir"
    if lower_name in _TODOWRITE_NAMES:
        return "todowrite"
    # MCP detection + last-token category matching
    server_name, tool_name_extracted = _extract_mcp_tool_name(name)
    if server_name is not None:
        matched = _match_last_token_category(tool_name_extracted)
        if matched is not None:
            return matched
    return "fallback"


def truncate_tool_result(
    content: Optional[str],
    limit: int = TRUNCATION_LIMIT,
) -> tuple[str, bool]:
    """Truncate tool result content if it exceeds the limit.

    Args:
        content: Raw tool result content string.
        limit: Maximum character count.

    Returns:
        Tuple of (possibly truncated content, was_truncated flag).
    """
    if not content:
        return ("", False)
    if len(content) <= limit:
        return (content, False)
    return (content[:limit], True)
