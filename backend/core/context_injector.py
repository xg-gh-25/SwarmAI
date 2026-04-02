"""Conversation context injection for resumed sessions.

This module provides a stateless function that loads recent messages from
SQLite, filters tool-only turns, formats them with role prefixes, enforces
a token budget, and returns a formatted string for system prompt injection.

**Budget scaling**: Limits scale with model context window.  1M models
(Claude 4.6) get up to 200K tokens / 500 messages — effectively the full
conversation.  Small models (<200K) use a conservative 12K / 40 message
budget.  Channel sessions (Slack) use a fixed 32K / 50 message
budget regardless of model size — enough continuity for "continue where
we left off" without the massive prefill cost on frequent cold resumes.
See ``_compute_resume_budget()`` for the tier logic.

- ``_compute_resume_budget``        — Scale limits by model context window
- ``build_resume_context``          — Public async entry point
- ``_filter_tool_only_messages``    — Remove tool-only messages
- ``_format_message``               — Format a single message with role prefix
- ``_apply_token_budget``           — Enforce token budget, oldest-first truncation
- ``_assemble_context``             — Wrap in section header and preamble
"""

import logging

logger = logging.getLogger(__name__)

_TOOL_ONLY_TYPES = {"tool_use", "tool_result"}

_SECTION_HEADER = "## Previous Conversation Context"

_PREAMBLE = (
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


def _filter_tool_only_messages(messages: list[dict]) -> list[dict]:
    """Remove messages whose content blocks are exclusively tool_use or tool_result.

    A message is retained if it has at least one content block with a ``type``
    not in ``{"tool_use", "tool_result"}`` (e.g. text, image, document).

    Args:
        messages: List of message dicts, each with a ``content`` key
            containing a list of content block dicts.

    Returns:
        Filtered list containing only messages with human-readable content.
    """
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
    """Produce a compact summary of tool arguments (file paths, key params).

    Keeps file_path, command (first 80 chars), and pattern fields.
    Returns a short string like ``file_path=agent_manager.py`` or
    ``command=git status...``.
    """
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
    """Summarize tool_use blocks as compact action descriptions.

    Returns a list of strings like ``→ Read(file_path=agent_manager.py)``.
    """
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
    """Format a single message as ``Role: content`` with placeholder handling.

    Extracts text blocks and joins them with newline separators.  Image blocks
    become ``[image attachment]``, document blocks become
    ``[document attachment]``.  Tool-use blocks are summarized as compact
    action descriptions so the resumed agent knows what tools were used.

    Args:
        message: A message dict with ``role`` and ``content`` keys.

    Returns:
        Formatted string like ``"User: hello\\nworld"`` or ``None`` on error.
    """
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
            # tool_use and tool_result handled below

        # Summarize tool usage ONLY when the message has no text blocks.
        # When text is present, it already provides context (e.g. "I read
        # the file and found...").  Tool summaries are most valuable for
        # messages that would otherwise be empty after filtering.
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
    """Remove oldest messages until total estimated tokens fit within budget.

    Uses ``ContextDirectoryLoader.estimate_tokens`` for the heuristic token
    count.  Messages are removed from the front (oldest first).

    Args:
        formatted_messages: Pre-formatted message strings in chronological order.
        token_budget: Maximum allowed estimated tokens.

    Returns:
        Tuple of ``(surviving_messages, was_truncated)`` where
        ``was_truncated`` is True if any messages were dropped.
    """
    try:
        from .context_directory_loader import ContextDirectoryLoader

        messages = list(formatted_messages)
        was_truncated = False

        token_counts = [ContextDirectoryLoader.estimate_tokens(m) for m in messages]
        total = sum(token_counts)

        # O(n) index-based truncation instead of O(n²) pop(0)
        start_idx = 0
        while total > token_budget and start_idx < len(messages):
            total -= token_counts[start_idx]
            start_idx += 1
            was_truncated = True

        return (messages[start_idx:], was_truncated)
    except Exception:
        logger.warning("Token budget estimation failed", exc_info=True)
        return ([], False)


def _assemble_context(messages: list[str], was_truncated: bool) -> str:
    """Wrap formatted messages in section header and preamble.

    Returns an empty string when ``messages`` is empty.  When
    ``was_truncated`` is True, a truncation note is prepended before the
    message turns.

    Args:
        messages: Pre-formatted message strings in chronological order.
        was_truncated: Whether older messages were dropped to fit the
            token budget.

    Returns:
        Assembled context string ready for system prompt injection,
        or ``""`` if no messages.
    """
    if not messages:
        return ""

    parts: list[str] = [_SECTION_HEADER, "", _PREAMBLE]

    if was_truncated:
        parts.append("")
        parts.append(_TRUNCATION_NOTE)

    for msg in messages:
        parts.append("")
        parts.append(msg)

    return "\n".join(parts)


def _compute_resume_budget(
    model_context_window: int, is_channel: bool = False
) -> tuple[int, int, int]:
    """Compute resume context limits scaled to model context window.

    For 1M models, we inject the full conversation — no practical truncation.
    For smaller models, use conservative limits to leave room for new work.

    Channel sessions (Slack) use a tight budget regardless of model
    size.  Channel conversations are quick exchanges — injecting hundreds of
    messages causes massive prefill latency on cold resume (the channel
    subprocess is evicted frequently since there's only 1 channel slot).

    Returns:
        Tuple of ``(token_budget, max_messages, db_fetch_limit)``.
    """
    if is_channel:
        # Channel sessions: last ~50 messages / 32K tokens.
        # Covers ~25 round-trips — enough for "continue where we left off"
        # without the 200K prefill cost that makes cold resume sluggish.
        return (32_000, 50, 120)

    if model_context_window >= 500_000:
        # 1M models: 200K budget, 500 messages, fetch 1000 from DB.
        # With 1M context, conversation history is valuable — don't discard it.
        return (200_000, 500, 1000)
    elif model_context_window >= 200_000:
        # 200K models: 40K budget, 100 messages
        return (40_000, 100, 250)
    else:
        # Small models (<200K): conservative 12K budget
        return (12_000, 40, 100)


async def build_resume_context(
    app_session_id: str,
    model_context_window: int = 200_000,
    max_messages: int | None = None,
    db_fetch_limit: int | None = None,
    token_budget: int | None = None,
    is_channel: bool = False,
) -> str:
    """Load recent messages and format them for system prompt injection.

    Limits scale with model context window — 1M models get the full
    conversation, small models get a conservative subset.  Explicit
    overrides take precedence over auto-computed values.

    Args:
        app_session_id: The stable tab-level session ID to query messages for.
        model_context_window: Model's context window in tokens.  Used to
            auto-compute token_budget, max_messages, and db_fetch_limit
            when they are not explicitly provided.
        max_messages: Maximum number of human-readable messages in the final
            output.  Auto-computed from model_context_window if None.
        db_fetch_limit: Number of messages to fetch from DB before filtering.
            Auto-computed from model_context_window if None.
        token_budget: Maximum estimated tokens for the formatted output.
            Auto-computed from model_context_window if None.
        is_channel: Whether this is a channel session (Slack).
            Channel sessions use a tighter budget to avoid slow prefill
            from accumulated conversation history.

    Returns:
        Formatted context string with section header, preamble, and message
        turns.  Returns empty string if no injectable messages exist or on
        any error.
    """
    # Auto-compute limits from model context window, allow explicit overrides
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

        raw_messages = await db.messages.list_by_session_paginated(
            app_session_id, limit=db_fetch_limit
        )

        if not raw_messages:
            logger.info("Resume context skipped: no messages for session %s", app_session_id)
            return ""

        filtered = _filter_tool_only_messages(raw_messages)
        # Drop the last assistant message — Claude will generate a fresh
        # response to the user's new message.  Keeping it in the injected
        # context triggers the "re-answer" duplication pattern where Claude
        # sees its own previous response and paraphrases it again.
        if filtered and filtered[-1].get("role") == "assistant":
            filtered = filtered[:-1]
        recent = filtered[-max_messages:]

        formatted: list[str] = []
        for msg in recent:
            text = _format_message(msg)
            if text is not None:
                formatted.append(text)

        if not formatted:
            logger.info("Resume context skipped: no injectable messages after filtering")
            return ""

        surviving, was_truncated = _apply_token_budget(formatted, token_budget)
        result = _assemble_context(surviving, was_truncated)

        if result:
            logger.info(
                "Resume context built: %d messages, truncated=%s",
                len(surviving),
                was_truncated,
            )
        else:
            logger.info("Resume context skipped: empty after token budget enforcement")

        return result
    except Exception:
        logger.warning(
            "Failed to build resume context for session %s",
            app_session_id,
            exc_info=True,
        )
        return ""
