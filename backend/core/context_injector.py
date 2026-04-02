"""Conversation context injection for resumed sessions.

Two-layer resume: structured checkpoint (~1-3K tokens) + recent turns
(~2-5K tokens).  The checkpoint gives the new agent an instant picture
of what was happening; the recent turns provide enough conversational
context to continue naturally.

**Stability contract**: if checkpoint extraction fails for any reason,
the module falls back to the legacy raw-history injection.  Every
extraction helper is wrapped in its own try/except — one failure never
cascades to another.

Public API (unchanged):
- ``build_resume_context(app_session_id, ...)`` → str
"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_TOOL_ONLY_TYPES = {"tool_use", "tool_result"}

# ─── Section headers & preamble ─────────────────────────────────────

_SECTION_HEADER = "## Session Resume"

_CHECKPOINT_PREAMBLE = (
    "The previous session ended (app restart, timeout, or eviction). "
    "Below is a structured checkpoint extracted from that session's "
    "message history, followed by the last few conversation turns.\n"
    "\n"
    "RULES:\n"
    "- Do NOT re-execute any actions, tool calls, or code changes.\n"
    "- Use the checkpoint to understand what was happening.\n"
    "- Use the recent turns to understand conversational context.\n"
    "- Wait for the user's NEW message and respond ONLY to that.\n"
    "- If the user says 'resume' or 'continue', pick up the task "
    "described in the checkpoint."
)

# Legacy preamble kept for fallback path
_LEGACY_PREAMBLE = (
    "The user resumed this chat after an app restart. The turns below are "
    "READ-ONLY history from the previous session — treat them as background "
    "context, NOT as prompts to respond to.\n"
    "\n"
    "RULES:\n"
    "- Do NOT re-answer, re-apologize, or re-explain anything from the history.\n"
    "- Do NOT re-execute any actions, tool calls, or code changes mentioned below.\n"
    "- Do NOT reference this history section unless the user explicitly asks about it.\n"
    "- Wait for the user's NEW message (after this section) and respond ONLY to that."
)

_TRUNCATION_NOTE = "[Earlier messages truncated to fit token budget]"


# ─── Message helpers (shared by both paths) ──────────────────────────

def _filter_tool_only_messages(messages: list[dict]) -> list[dict]:
    """Remove messages whose content blocks are exclusively tool_use or tool_result."""
    result = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list) or len(content) == 0:
            continue
        has_non_tool = any(
            block.get("type") not in _TOOL_ONLY_TYPES
            for block in content
            if isinstance(block, dict)
        )
        if has_non_tool:
            result.append(msg)
    return result


def _compact_tool_args(inp: dict) -> str:
    """Produce a compact summary of tool arguments (file paths, key params)."""
    parts: list[str] = []
    for key in ("file_path", "path", "command", "pattern", "query", "content"):
        val = inp.get(key)
        if val is not None:
            s = str(val)
            if len(s) > 80:
                s = s[:77] + "..."
            parts.append(f"{key}={s}")
        if len(parts) >= 2:
            break
    return ", ".join(parts) if parts else ""


def _summarize_tool_blocks(content: list[dict]) -> list[str]:
    """Summarize tool_use blocks as compact action descriptions."""
    summaries: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            name = block.get("name", "unknown")
            inp = block.get("input", {})
            brief = _compact_tool_args(inp) if isinstance(inp, dict) else ""
            summaries.append(f"  → {name}({brief})")
    return summaries


def _format_message(message: dict) -> str | None:
    """Format a single message as ``Role: content`` with placeholder handling."""
    try:
        role = message.get("role", "")
        prefix = "User:" if role == "user" else "Assistant:"

        content = message.get("content", [])
        if not isinstance(content, list):
            return None

        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")
            if block_type == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
            elif block_type == "image":
                parts.append("[image attachment]")
            elif block_type == "document":
                parts.append("[document attachment]")

        has_text = any(
            isinstance(b, dict) and b.get("type") == "text" and b.get("text")
            for b in content
        )
        if not has_text:
            tool_summaries = _summarize_tool_blocks(content)
            if tool_summaries:
                parts.append("[Tools used:]")
                parts.extend(tool_summaries)

        if not parts:
            return None

        return f"{prefix} {chr(10).join(parts)}"
    except Exception:
        logger.warning("Failed to format message: %s", message.get("id", "unknown"), exc_info=True)
        return None


def _apply_token_budget(
    formatted_messages: list[str], token_budget: int
) -> tuple[list[str], bool]:
    """Remove oldest messages until total estimated tokens fit within budget."""
    try:
        from .context_directory_loader import ContextDirectoryLoader

        messages = list(formatted_messages)
        was_truncated = False

        token_counts = [ContextDirectoryLoader.estimate_tokens(m) for m in messages]
        total = sum(token_counts)

        start_idx = 0
        while total > token_budget and start_idx < len(messages):
            total -= token_counts[start_idx]
            start_idx += 1
            was_truncated = True

        return (messages[start_idx:], was_truncated)
    except Exception:
        logger.warning("Token budget estimation failed", exc_info=True)
        return ([], False)


# ─── Checkpoint extraction helpers ───────────────────────────────────
# Each function is independently guarded.  Returns empty/default on error.

def _extract_text_from_content(content: list | None) -> str:
    """Pull concatenated text blocks from a message's content list."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts)


def _find_last_user_text(messages: list[dict]) -> str:
    """Return the text of the last user message."""
    try:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                text = _extract_text_from_content(msg.get("content"))
                if text:
                    # Cap at 500 chars — enough to identify the task
                    return text[:500]
        return ""
    except Exception:
        return ""


def _extract_tool_summary(messages: list[dict]) -> dict[str, set[str]]:
    """Scan recent messages for tool_use blocks → {tool_name: {key args}}.

    Returns a dict like {"Read": {"agent.py"}, "Bash": {"git status..."}}.
    Only scans the last ``messages`` provided (caller should slice).

    NOTE: DB persists tool_use as {name, summary, category} without the
    full ``input`` dict.  We extract info from both ``input`` (live) and
    ``summary`` (DB) to work in all contexts.
    """
    try:
        summary: dict[str, set[str]] = {}
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if not name:
                    continue

                arg = ""
                inp = block.get("input")

                if isinstance(inp, dict) and inp:
                    # Live path: full input dict available
                    if name in ("Read", "Write", "Edit", "Glob"):
                        arg = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
                    elif name == "Bash":
                        arg = (inp.get("command") or "")[:60]
                    elif name == "Grep":
                        arg = (inp.get("pattern") or "")[:60]
                    elif name == "Agent":
                        arg = (inp.get("description") or "")[:80]
                    elif name == "Skill":
                        arg = inp.get("skill") or ""
                    else:
                        arg = _compact_tool_args(inp)[:60]
                else:
                    # DB path: only summary string available
                    s = block.get("summary", "")
                    if s:
                        arg = s[:80]

                if name not in summary:
                    summary[name] = set()
                if arg:
                    summary[name].add(arg)
        return summary
    except Exception:
        logger.debug("Tool summary extraction failed", exc_info=True)
        return {}


def _extract_files_touched(tool_summary: dict[str, set[str]]) -> list[str]:
    """From tool summary, extract unique file paths that were read/edited.

    Works with both live (input.file_path) and DB (summary string) data.
    Summary strings look like 'Reading /path/to/file.py' or
    'Editing /path/to/file.py'.
    """
    try:
        files: set[str] = set()
        for tool_name in ("Read", "Write", "Edit"):
            for arg in tool_summary.get(tool_name, set()):
                if not arg:
                    continue
                # Try to find a path in the string
                # Match patterns like /path/to/file.ext or file_path=path
                for token in arg.split():
                    if "/" in token and "." in token.rsplit("/", 1)[-1]:
                        basename = token.rsplit("/", 1)[-1]
                        # Clean trailing punctuation
                        basename = basename.rstrip(".,;:\"')")
                        if basename and len(basename) < 60:
                            files.add(basename)
                            break
        return sorted(files)[:20]
    except Exception:
        return []


def _extract_git_activity(messages: list[dict]) -> list[str]:
    """Scan Bash tool calls for git commit commands → extract commit messages.

    Works with both live (input.command) and DB (summary) paths.
    """
    try:
        commits: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Bash":
                    continue
                # Try input.command first (live), then summary (DB)
                inp = block.get("input")
                cmd = ""
                if isinstance(inp, dict):
                    cmd = inp.get("command", "")
                if not cmd:
                    cmd = block.get("summary", "")
                if "git commit" in cmd:
                    # Try standard -m "msg" first
                    m = re.search(r'-m\s+["\']([^"\']+)', cmd)
                    if m:
                        msg_text = m.group(1).strip()
                        # Skip HEREDOC markers like "$(cat <<'EOF'"
                        if msg_text and not msg_text.startswith("$("):
                            commits.append(msg_text[:80])
        return commits[-5:]
    except Exception:
        return []


def _extract_agent_spawns(messages: list[dict]) -> list[str]:
    """Scan for Agent tool calls → extract descriptions of sub-tasks.

    Works with both live (input.description) and DB (summary) paths.
    """
    try:
        spawns: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Agent":
                    continue
                # Try input.description first (live), then summary (DB)
                inp = block.get("input")
                desc = ""
                if isinstance(inp, dict):
                    desc = inp.get("description", "")
                if not desc:
                    desc = block.get("summary", "")
                if desc:
                    # Strip "Agent: " prefix from summary
                    if desc.startswith("Agent: "):
                        desc = desc[7:]
                    spawns.append(desc[:100])
        return spawns[-5:]
    except Exception:
        return []


def _extract_skill_invocations(messages: list[dict]) -> list[str]:
    """Scan for Skill tool calls → extract skill names.

    Works with both live (input.skill) and DB (summary) paths.
    """
    try:
        skills: list[str] = []
        seen: set[str] = set()
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Skill":
                    continue
                inp = block.get("input")
                skill_name = ""
                if isinstance(inp, dict):
                    skill_name = inp.get("skill", "")
                if not skill_name:
                    skill_name = block.get("summary", "")[:60]
                if skill_name and skill_name not in seen:
                    seen.add(skill_name)
                    skills.append(skill_name)
        return skills
    except Exception:
        return []


def _estimate_session_timespan(messages: list[dict]) -> str:
    """Estimate session duration from first and last message timestamps."""
    try:
        first_ts = messages[0].get("created_at", "")
        last_ts = messages[-1].get("created_at", "")
        if not first_ts or not last_ts:
            return ""
        # Parse ISO timestamps
        t0 = datetime.fromisoformat(first_ts)
        t1 = datetime.fromisoformat(last_ts)
        delta = t1 - t0
        minutes = int(delta.total_seconds() / 60)
        return f"{t0.strftime('%H:%M')} → {t1.strftime('%H:%M')} ({minutes} min)"
    except Exception:
        return ""


def _count_user_turns(messages: list[dict]) -> int:
    """Count user messages (= number of conversation turns)."""
    try:
        return sum(1 for m in messages if m.get("role") == "user")
    except Exception:
        return 0


# ─── Checkpoint assembly ─────────────────────────────────────────────

def _build_checkpoint(messages: list[dict]) -> str:
    """Build a structured task checkpoint from raw messages.

    Pure mechanical extraction — no LLM calls.  Each section is
    independently guarded; partial checkpoints are valid.

    Returns empty string only if all extractions fail.
    """
    sections: list[str] = []

    # 1. Last user request (= the task)
    last_request = _find_last_user_text(messages)
    if last_request:
        sections.append(f"**Last request:** {last_request}")

    # 2. Session stats
    timespan = _estimate_session_timespan(messages)
    turns = _count_user_turns(messages)
    if timespan or turns:
        stats_parts = []
        if turns:
            stats_parts.append(f"{turns} turns")
        if timespan:
            stats_parts.append(timespan)
        sections.append(f"**Session:** {', '.join(stats_parts)}")

    # 3. Tool usage summary (last 40 messages)
    tool_summary = _extract_tool_summary(messages[-40:])

    # 4. Files touched
    files = _extract_files_touched(tool_summary)
    if files:
        sections.append(f"**Files touched:** {', '.join(files)}")

    # 5. Git commits
    commits = _extract_git_activity(messages)
    if commits:
        commit_lines = "\n".join(f"  - {c}" for c in commits)
        sections.append(f"**Git commits:**\n{commit_lines}")

    # 6. Sub-agent tasks
    spawns = _extract_agent_spawns(messages[-20:])
    if spawns:
        spawn_lines = "\n".join(f"  - {s}" for s in spawns)
        sections.append(f"**Sub-tasks spawned:**\n{spawn_lines}")

    # 7. Skills used
    skills = _extract_skill_invocations(messages)
    if skills:
        sections.append(f"**Skills used:** {', '.join(skills)}")

    # 8. Key tool stats
    tool_counts = {name: len(args) for name, args in tool_summary.items()}
    if tool_counts:
        top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:6]
        tool_str = ", ".join(f"{name}×{count}" for name, count in top_tools)
        sections.append(f"**Tool activity:** {tool_str}")

    if not sections:
        return ""

    return "### Task Checkpoint\n" + "\n".join(sections)


# ─── Recent turns formatting ─────────────────────────────────────────

def _format_recent_turns(messages: list[dict], max_turns: int = 5) -> str:
    """Format the last N user-assistant turn pairs.

    Returns a compact section with just enough conversational context
    for the new agent to understand what was being discussed.
    """
    try:
        filtered = _filter_tool_only_messages(messages)
        # Drop last assistant message (anti-duplication, same as legacy)
        if filtered and filtered[-1].get("role") == "assistant":
            filtered = filtered[:-1]

        # Take last max_turns * 2 messages (pairs of user + assistant)
        recent = filtered[-(max_turns * 2):]

        formatted: list[str] = []
        for msg in recent:
            text = _format_message(msg)
            if text is not None:
                # Cap each message at 800 chars for recent turns
                if len(text) > 800:
                    text = text[:797] + "..."
                formatted.append(text)

        if not formatted:
            return ""

        return "### Recent Conversation\n" + "\n\n".join(formatted)
    except Exception:
        logger.debug("Recent turns formatting failed", exc_info=True)
        return ""


# ─── Budget computation ──────────────────────────────────────────────

def _compute_resume_budget(
    model_context_window: int, is_channel: bool = False
) -> tuple[int, int, int]:
    """Compute resume context limits scaled to model context window.

    Returns:
        Tuple of ``(token_budget, max_messages, db_fetch_limit)``.
    """
    if is_channel:
        return (32_000, 50, 120)

    if model_context_window >= 500_000:
        return (200_000, 500, 1000)
    elif model_context_window >= 200_000:
        return (40_000, 100, 250)
    else:
        return (12_000, 40, 100)


# ─── Legacy assembly (kept for backward compat + fallback) ───────────

def _assemble_context(messages: list[str], was_truncated: bool) -> str:
    """Wrap formatted messages in section header and preamble.

    Kept for backward compatibility with existing tests and callers.
    """
    if not messages:
        return ""

    parts: list[str] = ["## Previous Conversation Context", "", _LEGACY_PREAMBLE]
    if was_truncated:
        parts.append("")
        parts.append(_TRUNCATION_NOTE)
    for msg in messages:
        parts.append("")
        parts.append(msg)
    return "\n".join(parts)


# ─── Legacy raw-history builder (fallback) ───────────────────────────

def _build_legacy_context(raw_messages: list[dict], max_messages: int,
                          token_budget: int) -> str:
    """Original raw-history injection — used as fallback."""
    filtered = _filter_tool_only_messages(raw_messages)
    if filtered and filtered[-1].get("role") == "assistant":
        filtered = filtered[:-1]
    recent = filtered[-max_messages:]

    formatted: list[str] = []
    for msg in recent:
        text = _format_message(msg)
        if text is not None:
            formatted.append(text)

    if not formatted:
        return ""

    surviving, was_truncated = _apply_token_budget(formatted, token_budget)
    return _assemble_context(surviving, was_truncated)


# ─── Per-session checkpoint cache ────────────────────────────────────
# Key: session_id, Value: (msg_count_at_build_time, result_string).
# Messages are append-only → count change = cache invalid.
# LRU eviction: cap at 50 entries (~100-250KB) to prevent unbounded
# growth in long-running daemon.  OrderedDict for O(1) eviction.
from collections import OrderedDict

_RESUME_CACHE_MAX = 50
_resume_cache: OrderedDict[str, tuple[int, str]] = OrderedDict()


# ─── Public API ──────────────────────────────────────────────────────

async def build_resume_context(
    app_session_id: str,
    model_context_window: int = 200_000,
    max_messages: int | None = None,
    db_fetch_limit: int | None = None,
    token_budget: int | None = None,
    is_channel: bool = False,
) -> str:
    """Load recent messages and build resume context for system prompt.

    Two-layer approach:
    1. **Structured checkpoint** (~1-3K tokens) — mechanical extraction
       of task state, files touched, git activity, sub-tasks.
    2. **Recent turns** (~2-5K tokens) — last 5 conversation turn pairs
       for conversational continuity.

    Falls back to legacy raw-history injection if the structured path
    produces nothing.

    Args:
        app_session_id: The stable tab-level session ID to query.
        model_context_window: Model's context window in tokens.
        max_messages: Max messages for legacy fallback path.
        db_fetch_limit: Messages to fetch from DB.
        token_budget: Token budget for legacy fallback path.
        is_channel: Channel session (tighter budget).

    Returns:
        Formatted context string, or ``""`` on error/no messages.
    """
    auto_budget, auto_max, auto_fetch = _compute_resume_budget(
        model_context_window, is_channel=is_channel
    )
    token_budget = token_budget if token_budget is not None else auto_budget
    max_messages = max_messages if max_messages is not None else auto_max
    db_fetch_limit = db_fetch_limit if db_fetch_limit is not None else auto_fetch
    if app_session_id is None:
        return ""

    try:
        from database import db

        # ── Cache check: skip DB fetch if msg_count unchanged ──
        msg_count = await db.messages.count_by_session(app_session_id)
        cached = _resume_cache.get(app_session_id)
        if cached and cached[0] == msg_count:
            _resume_cache.move_to_end(app_session_id)  # LRU touch
            logger.info("Resume context cache hit: session=%s count=%d",
                        app_session_id[:12], msg_count)
            return cached[1]

        raw_messages = await db.messages.list_by_session_paginated(
            app_session_id, limit=db_fetch_limit
        )

        if not raw_messages:
            logger.info("Resume context skipped: no messages for session %s",
                        app_session_id)
            return ""

        # ── Try structured checkpoint + recent turns ──────────────
        checkpoint = ""
        recent = ""
        try:
            checkpoint = _build_checkpoint(raw_messages)
            recent = _format_recent_turns(raw_messages, max_turns=5)
        except Exception:
            logger.warning("Structured checkpoint extraction failed",
                           exc_info=True)

        if checkpoint or recent:
            parts = [_SECTION_HEADER, "", _CHECKPOINT_PREAMBLE]
            if checkpoint:
                parts.append("")
                parts.append(checkpoint)
            if recent:
                parts.append("")
                parts.append(recent)
            result = "\n".join(parts)
            _resume_cache[app_session_id] = (msg_count, result)
            if len(_resume_cache) > _RESUME_CACHE_MAX:
                _resume_cache.popitem(last=False)  # evict oldest
            logger.info(
                "Resume context built (structured): checkpoint=%d chars, "
                "recent=%d chars, total=~%d tokens",
                len(checkpoint), len(recent), len(result) // 4,
            )
            return result

        # ── Fallback: legacy raw-history injection ────────────────
        logger.info("Structured resume empty — falling back to legacy "
                    "raw-history injection")
        result = _build_legacy_context(raw_messages, max_messages, token_budget)
        if result:
            _resume_cache[app_session_id] = (msg_count, result)
            if len(_resume_cache) > _RESUME_CACHE_MAX:
                _resume_cache.popitem(last=False)  # evict oldest
            logger.info(
                "Resume context built (legacy fallback): ~%d tokens",
                len(result) // 4,
            )
        else:
            logger.info("Resume context skipped: no injectable messages")
        return result

    except Exception:
        logger.warning(
            "Failed to build resume context for session %s",
            app_session_id,
            exc_info=True,
        )
        return ""
