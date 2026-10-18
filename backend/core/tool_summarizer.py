"""Tool call summarization and result truncation for SSE/DB optimization.

This module provides pure functions for converting raw tool call inputs into
short human-readable summaries and truncating large tool result content.
The Claude Agent SDK manages full conversation context internally; these
functions only transform content for the UI replay path (SSE + SQLite).

- ``summarize_tool_use``       — Generates ≤200-char summary from tool name + input
- ``get_tool_category``        — Returns category string for icon mapping on frontend
- ``truncate_tool_result``     — Truncates content to configurable limit, sets flag
- ``summarize_tool_result``    — One-line outcome of a finished call (lines/matches/output)
- ``tool_result_text``         — Plain text of a ToolResultBlock's content
- ``redact_display``           — Credential redaction for any displayed tool text, run on a
                                 bounded window BEFORE the cut (summary, outcome, preview)
- ``_extract_mcp_tool_name``   — Extracts server/tool from ``mcp__Server__tool`` names
- ``MAX_SUMMARY_LENGTH``       — 200 characters
- ``DEFAULT_TRUNCATION_LIMIT`` — 500 characters
- ``_NAMED_SECRET``            — Key-name-anchored secret values (display only); value
                                 shapes come from ``core.credential_patterns`` (shared
                                 with channel egress)
- ``TRUNCATION_LIMIT``         — Effective limit (env var override or default)
"""

import os as _os
import re
import logging
from typing import Any, Optional

from core.credential_patterns import redact_credentials

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
# Name-anchored secret redaction (display only)
# ---------------------------------------------------------------------------

# ``<key> = <value>`` / ``<key>: <value>`` / ``"<key>": "<value>"`` where the key ENDS
# in a secret name. The key may carry any ``_``/``-``/camelCase prefix
# (``AWS_SESSION_TOKEN``, ``_authToken``, ``"SecretAccessKey"``, ``X-Api-Key``) — a
# leading ``\b`` would miss every ``*_TOKEN`` env var. Group ``key`` stays readable.
# The value is masked WHOLE, any length: a quoted value up to its closing quote
# (symbols included: ``'P@ss(w0rd)'``), an unquoted one to whitespace or ``'",;``.
# No "is this code?" guessing — every heuristic of that kind (type annotations,
# keyword arguments, ``x = name``) unmasked a real secret shape, and a masked
# identifier in a code preview costs nothing next to a shown password. The only
# exemptions cannot hide a secret: a ``$VAR`` reference (unquoted or in double
# quotes; single-quoted ``$`` is literal and masked), ``PWD`` / ``OLDPWD`` (the
# working directory), and a value equal to the key's own name.
_NAMED_SECRET = re.compile(
    r"""(?i)(?<![\w$-])(?P<key>[\w-]*?(?:password|passwd|pwd|api[_-]?key|secret[_-]?key"""
    r"""|access[_-]?key|aws[_-]?secret|token|bearer)['"]?[^\S\n]*[=:][^\S\n]*)"""
    r"""(?:(?P<q>['"])(?P<qv>[^\s'"][^\n]*?)(?:(?P=q)|(?=\n)|$)|(?P<uv>[^\s'",;]++))"""
)
_KEY_NAME = re.compile(r"([\w-]+)['\"]?\s*[=:]")


# HTTP basic auth header (what ``curl -u user:pass`` sends): the header name anchors
# it, so prose like "Basic configuration" is untouched; the value is masked.
_BASIC_AUTH = re.compile(r"(?i)(\bauthorization\s*:\s*basic\s+)[A-Za-z0-9+/=]{8,}")


def _mask_named(m: re.Match) -> str:
    key, quote = m.group("key"), m.group("q")
    value = m.group("qv") if quote else m.group("uv")
    tail = ""
    if not quote:
        # keep closers the value did not open: f(password=pw) -> password=[REDACTED])
        pairs = {")": "(", "]": "[", "}": "{"}
        while value and value[-1] in pairs and value.count(value[-1]) > value.count(pairs[value[-1]]):
            tail = value[-1] + tail
            value = value[:-1]
    name = _KEY_NAME.search(key)
    if name and name.group(1).upper() in ("PWD", "OLDPWD"):
        return m.group(0)  # the shell's working directory, not a password
    if value.startswith("$") and quote != "'":
        return m.group(0)  # a variable reference, not its value
    if not value or (name and value.lower() == name.group(1).lower()):
        return m.group(0)  # password=password
    if quote:
        closed = m.group(0).endswith(quote) and len(m.group(0)) > len(key) + 1 + len(value)
        return key + quote + "[REDACTED]" + (quote if closed else "")
    return key + "[REDACTED]" + tail


def _redact_named(text: str) -> str:
    """Name-anchored secrets (display only — too broad for channel prose)."""
    text = _NAMED_SECRET.sub(_mask_named, text)
    return _BASIC_AUTH.sub(lambda m: m.group(1) + "[REDACTED]", text)

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
_AGENT_NAMES: set[str] = {"agent", "subagent", "dispatch_agent", "task"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


# Display redaction runs on the cut text plus this much look-ahead, so a secret
# that crosses the display cut is redacted whole before the cut, while a
# multi-megabyte result never costs more than ``limit + _REDACT_SLACK`` of regex.
_REDACT_SLACK = 512

# A private-key header whose END line lies past the window (or never comes).
_PEM_UNTERMINATED = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----.*\Z", re.DOTALL
)

# The run of token-like characters at the window edge. When the text continues past
# the window, this run is a value cut in half: too short for a shape's minimum
# length, so it is dropped rather than shown (redaction can shrink the text and
# pull it inside ``limit``).
_TRAILING_RUN = re.compile(r"[^\s\"'<>(){}\[\],;`]+\Z")


def redact_display(text: str, limit: int) -> str:
    """Redact credentials from tool text shown in the UI, then cut it to *limit*.

    Every displayed tool string goes through here: a tool_use summary (any tool),
    a tool_result outcome line and a tool_result content preview. Order matters:
    redaction runs on ``text[:limit + _REDACT_SLACK]`` and the cut comes after, so a
    token straddling the cut is masked whole instead of showing its first part.

    Value shapes (Bearer, prefixed tokens, AWS key ids, JWT, URL credentials,
    ``curl -u user:pass``, PEM blocks) come from ``core.credential_patterns``;
    the name-anchored patterns (``password=`` / ``token:`` / ``Authorization: Basic``)
    are added here only,
    because they are too broad for channel prose.

    Residual (stated, not hidden): a window that is ONE run of token characters
    (a single-line base64 blob) has no boundary to cut back to and is kept; a
    secret with no known shape or key name is not recognised.
    """
    if not text:
        return text
    window = text[: limit + _REDACT_SLACK]
    if len(text) > len(window):
        edge = _TRAILING_RUN.search(window)
        if edge and edge.start() > 0:
            window = window[: edge.start()]
    redacted = _redact_named(redact_credentials(window))
    redacted = _PEM_UNTERMINATED.sub("[REDACTED]", redacted)
    return redacted[:limit]


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
        summary = f"Running: {cmd}" if cmd else f"Using {name}"
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
            data.get("skill")
            or data.get("skill_name")
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
        file_path = data.get("file_path") or data.get("path", "")
        if file_path:
            # Show filename + count: "Writing 7 todos → tasks.md"
            fname = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
            summary = f"Writing {len(todos)} todos → {fname}"
        else:
            summary = f"Writing {len(todos)} todos"
    elif lower_name in _AGENT_NAMES:
        category = "agent"
        # Extract the most useful context: task_description > prompt snippet > name
        # The Agent tool's own short `description` ("Review spec compliance") is
        # what the user needs on the row; a prompt snippet is a fallback.
        task_desc = data.get("description") or data.get("task_description") or data.get("task", "")
        prompt = data.get("prompt", "")
        agent_name = data.get("name") or data.get("agent_name", "")
        if task_desc:
            summary = f"Agent: {task_desc}"
        elif prompt:
            # Show first ~120 chars of the prompt for context
            # Redact BEFORE the 120-char cut (a secret across it is masked whole).
            snippet = redact_display(prompt.replace("\n", " ").strip(), 120)
            label = f"Agent ({agent_name})" if agent_name else "Agent"
            summary = f"{label}: {snippet}"
        elif agent_name:
            summary = f"Agent: {agent_name}"
        else:
            summary = f"Using {name}"
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
                summary = f"MCP: {tool_name_extracted} — {context}"
            else:
                summary = f"MCP: {tool_name_extracted}"
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

    # Redact, THEN cut to MAX_SUMMARY_LENGTH (a secret across the cut is masked
    # whole), and guarantee non-empty. Covers every tool, not only Bash.
    summary = redact_display(summary, MAX_SUMMARY_LENGTH)
    if not summary:
        summary = f"Using {name}"[:MAX_SUMMARY_LENGTH]

    return summary


def get_tool_category(name: str) -> str:
    """Return a category string for the given tool name.

    Used by ``_format_message()`` to emit a ``category`` field alongside
    ``summary`` so the frontend can pick a per-category icon without
    duplicating the name-matching logic.

    Categories: bash, read, write, search, web_fetch, web_search,
    list_dir, todowrite, agent, fallback.
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
    if lower_name in _AGENT_NAMES:
        return "agent"
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

    The preview is credential-redacted (``redact_display``): it is shown in the
    expanded tool row and stored for history replay. ``was_truncated`` still
    reports whether the ORIGINAL content exceeded *limit*.

    Returns:
        Tuple of (possibly truncated, redacted content, was_truncated flag).
    """
    if not content:
        return ("", False)
    return (redact_display(content, limit), len(content) > limit)


_OUTCOME_MAX = 120
_READ_LINE_RE = re.compile(r"^\s*(\d+)\t")


def tool_result_text(content: Any) -> str:
    """Plain text of a ToolResultBlock's content (str, or a list of text blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return str(content)


def _first_line(text: str) -> str:
    """First non-blank line, redacted then cut to ``_OUTCOME_MAX``."""
    for line in text.splitlines():
        if line.strip():
            return redact_display(line.strip(), _OUTCOME_MAX)
    return ""


def _last_line(text: str) -> str:
    """Last non-blank line, redacted then cut to ``_OUTCOME_MAX``."""
    for line in reversed(text.splitlines()):
        if line.strip():
            return redact_display(line.strip(), _OUTCOME_MAX)
    return ""


def summarize_tool_result(name: str, text: str, is_error: bool) -> str:
    """One short line saying what a finished tool call produced.

    Computed from the FULL result (the streamed content is truncated), e.g.
    ``lines 1–240``, ``12 matches``, the last line of a command's output, or the
    first line of an error. Shown next to the step so the expanded process says
    what each call returned, not only what was called.
    """
    lower = (name or "").lower()
    if is_error:
        return _first_line(text) or "failed"
    if lower in _READ_NAMES:
        nums = [int(m.group(1)) for m in map(_READ_LINE_RE.match, text.splitlines()) if m]
        if nums:
            return f"lines {nums[0]}–{nums[-1]}" if len(nums) > 1 else f"line {nums[0]}"
        return _first_line(text) or "empty"
    if lower in _SEARCH_NAMES or lower in ("glob", "grep"):
        first = _first_line(text)
        if not first or first.lower().startswith(("no matches", "no files")):
            return "no matches"
        if first.lower().startswith("found "):
            return first
        n = sum(1 for line in text.splitlines() if line.strip())
        return f"{n} match" if n == 1 else f"{n} matches"
    if lower in _BASH_NAMES:
        return _last_line(text) or "no output"
    if lower in _WRITE_NAMES or lower in ("edit", "multiedit", "notebookedit"):
        return "saved"
    if lower in ("agent", "task"):
        return "done"
    return _first_line(text)
