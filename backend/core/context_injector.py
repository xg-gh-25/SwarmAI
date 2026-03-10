"""Conversation context injection for resumed sessions.

This module provides a stateless function that loads recent messages from
SQLite, filters tool-only turns, formats them with role prefixes, enforces
a token budget, and returns a formatted string for system prompt injection.

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


def _format_message(message: dict) -> str | None:
    """Format a single message as ``Role: content`` with placeholder handling.

    Extracts text blocks and joins them with newline separators.  Image blocks
    become ``[image attachment]``, document blocks become
    ``[document attachment]``.  Tool-use and tool-result blocks are silently
    skipped.

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
            # tool_use and tool_result are silently skipped

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

        total = sum(ContextDirectoryLoader.estimate_tokens(m) for m in messages)

        while total > token_budget and messages:
            removed = messages.pop(0)
            total -= ContextDirectoryLoader.estimate_tokens(removed)
            was_truncated = True

        return (messages, was_truncated)
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


async def build_resume_context(
    app_session_id: str,
    max_messages: int = 10,
    db_fetch_limit: int = 30,
    token_budget: int = 2000,
) -> str:
    """Load recent messages and format them for system prompt injection.

    Imports ``db`` from ``backend.database`` (the module-level singleton)
    to query messages.  The function is async because the DB call is async.

    Args:
        app_session_id: The stable tab-level session ID to query messages for.
        max_messages: Maximum number of human-readable messages in the final
            output (default 10).
        db_fetch_limit: Number of messages to fetch from DB before filtering
            (default 30).  Set higher than max_messages to account for
            tool-only messages being filtered out.
        token_budget: Maximum estimated tokens for the formatted output
            (default 2000).

    Returns:
        Formatted context string with section header, preamble, and message
        turns.  Returns empty string if no injectable messages exist or on
        any error.
    """
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
