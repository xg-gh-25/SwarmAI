"""LLM-powered memory extraction from chat sessions.

Extracts key decisions, lessons learned, open threads, and context from
a conversation and writes them to MEMORY.md via ``locked_read_modify_write()``.

This module powers the one-click "Save to Memory" button in the frontend.
The LLM handles quality control — extracting only genuinely important entries,
deduplicating against existing MEMORY.md content, and enforcing concise format.

Key public symbols:

- ``extract_and_save``      — Main entry point: extract from messages → write to MEMORY.md.
- ``MemoryExtractionResult`` — Dataclass with counts of saved entries per section.
- ``EXTRACTION_MODEL``       — Default Bedrock model ID for extraction (Sonnet 4.6).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Direct import — avoids sys.executable subprocess which breaks in PyInstaller bundles
from scripts.locked_write import locked_read_modify_write

logger = logging.getLogger(__name__)

# Default model for extraction — Sonnet is fast and cheap enough for structured extraction.
# Uses the Bedrock cross-region inference profile ID directly.
EXTRACTION_MODEL = "us.anthropic.claude-sonnet-4-6"

# Minimum messages to justify an extraction (skip trivially short sessions)
MIN_MESSAGES_FOR_EXTRACTION = 3

# Maximum conversation tokens to send (truncate older messages if over)
MAX_CONVERSATION_CHARS = 30_000

EXTRACTION_PROMPT = """\
You are a memory curator for SwarmAI. Extract important entries from this \
conversation for long-term memory storage.

## Current MEMORY.md (for dedup — do NOT re-extract anything already here):
{memory_content}

## Conversation to extract from:
{conversation}

## Rules:
- Each entry: one line, max 120 characters, prefixed with "- {today}: "
- Only extract genuinely important decisions, lessons, or status changes
- Skip: routine operations, greetings, confirmations, debugging steps, tool outputs
- Skip: anything already captured in MEMORY.md above (dedup)
- If nothing important to save, return all empty arrays
- Categorize precisely:
  - key_decisions: architectural choices, technology picks, policy decisions
  - lessons_learned: debugging insights, gotchas, best practices discovered
  - open_threads: unfinished work, pending investigations, follow-ups needed
  - recent_context: current project status, what was worked on

## Output (valid JSON only, no markdown fences, no explanation):
{{"key_decisions": [], "lessons_learned": [], "open_threads": [], "recent_context": []}}"""


@dataclass
class MemoryExtractionResult:
    """Result of a memory extraction operation."""

    key_decisions: int = 0
    lessons_learned: int = 0
    open_threads: int = 0
    recent_context: int = 0
    total_saved: int = 0
    next_message_idx: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key_decisions": self.key_decisions,
            "lessons_learned": self.lessons_learned,
            "open_threads": self.open_threads,
            "recent_context": self.recent_context,
            "total_saved": self.total_saved,
            "next_message_idx": self.next_message_idx,
        }
        if self.error:
            d["error"] = self.error
        return d


def _format_conversation(messages: list[dict], since_idx: int = 0) -> str:
    """Format messages into a readable conversation string for the LLM.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        since_idx: Only include messages from this index onward.

    Returns:
        Formatted conversation string, truncated to MAX_CONVERSATION_CHARS.
    """
    lines: list[str] = []
    for msg in messages[since_idx:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Content can be a string or a list of content blocks
        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        text_parts.append(f"[Tool: {block.get('name', '?')}]")
                    elif block.get("type") == "tool_result":
                        # Skip verbose tool results
                        continue
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)

        if not content or not content.strip():
            continue

        prefix = "User" if role == "user" else "Assistant"
        lines.append(f"{prefix}: {content.strip()}")

    result = "\n\n".join(lines)

    # Truncate from the beginning if too long (keep recent messages)
    if len(result) > MAX_CONVERSATION_CHARS:
        result = "...[truncated older messages]...\n\n" + result[-MAX_CONVERSATION_CHARS:]

    return result


_LLM_MAX_RETRIES = 3
_LLM_RETRY_DELAY = 1.0  # seconds, doubles each retry


def _call_llm(prompt: str, config: dict[str, Any] | None = None) -> str:
    """Make a direct Bedrock API call for extraction with retry.

    Uses boto3 bedrock-runtime to invoke the model.  Retries up to
    ``_LLM_MAX_RETRIES`` times on transient errors (network, throttling,
    zlib decompression failures from corrupted HTTP responses).

    Args:
        prompt: The fully formatted extraction prompt.
        config: Optional app config dict for model/region overrides.

    Returns:
        Raw LLM response text, or ``"{}"`` if all retries fail.
    """
    import time
    import boto3
    from botocore.config import Config as BotoConfig

    region = "us-east-1"
    model_id = EXTRACTION_MODEL

    if config:
        region = config.get("aws_region", region)
        model_id = config.get("memory_extraction_model", model_id)

    # Configure boto3 with built-in retry for standard AWS errors,
    # plus we add our own retry loop for non-standard errors (zlib, etc.)
    boto_config = BotoConfig(
        retries={"max_attempts": 2, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=30,
    )

    last_error: Exception | None = None
    delay = _LLM_RETRY_DELAY

    for attempt in range(1, _LLM_MAX_RETRIES + 1):
        try:
            client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=boto_config,
            )
            response = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1024,
                    "temperature": 0,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                }),
            )
            result = json.loads(response["body"].read())
            content = result.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "{}")
            return "{}"
        except Exception as e:
            last_error = e
            if attempt < _LLM_MAX_RETRIES:
                logger.warning(
                    "LLM extraction attempt %d/%d failed: %s — retrying in %.1fs",
                    attempt,
                    _LLM_MAX_RETRIES,
                    e,
                    delay,
                )
                time.sleep(delay)
                delay *= 2  # exponential backoff
            else:
                logger.error(
                    "LLM extraction failed after %d attempts: %s",
                    _LLM_MAX_RETRIES,
                    e,
                    exc_info=True,
                )

    return "{}"


def _parse_extraction(raw: str) -> dict[str, list[str]]:
    """Parse the LLM's JSON response into section -> entries mapping.

    Handles common LLM output quirks: markdown fences, trailing commas,
    extra text before/after JSON.

    Returns:
        Dict with keys: key_decisions, lessons_learned, open_threads, recent_context.
        Each value is a list of entry strings. Empty dict on parse failure.
    """
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        logger.warning("No JSON object found in LLM response")
        return {}

    json_str = text[start:end]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse extraction JSON: %s", e)
        return {}

    # Validate structure
    result: dict[str, list[str]] = {}
    valid_sections = {"key_decisions", "lessons_learned", "open_threads", "recent_context"}
    for section in valid_sections:
        entries = data.get(section, [])
        if isinstance(entries, list):
            # Filter to only valid string entries
            result[section] = [e for e in entries if isinstance(e, str) and e.strip()]

    return result


# Map extraction keys to MEMORY.md section headers
_SECTION_MAP = {
    "key_decisions": "Key Decisions",
    "lessons_learned": "Lessons Learned",
    "open_threads": "Open Threads",
    "recent_context": "Recent Context",
}


def _write_entries(
    entries: list[str],
    section: str,
    memory_path: Path,
) -> bool:
    """Write extracted entries to MEMORY.md via locked_read_modify_write.

    All entries for a section are joined into a single prepend call
    to minimize lock acquisitions.  Calls the function directly instead
    of spawning a subprocess (which breaks in PyInstaller bundles where
    ``sys.executable`` points to the bundled binary, not Python).

    Returns:
        True if write succeeded, False otherwise.
    """
    if not entries:
        return True

    text = "\n".join(entries)

    try:
        locked_read_modify_write(memory_path, section, text, mode="prepend")
        return True
    except SystemExit as e:
        # locked_read_modify_write calls sys.exit(1) on lock timeout
        logger.error("locked_write failed for section %s: exit code %s", section, e.code)
        return False
    except Exception as e:
        logger.error("locked_write failed for section %s: %s", section, e)
        return False


async def extract_and_save(
    session_id: str,
    since_message_idx: int = 0,
    workspace_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> MemoryExtractionResult:
    """Extract memory entries from a chat session and save to MEMORY.md.

    This is the main entry point for the one-click "Save to Memory" feature.

    Args:
        session_id: The chat session ID to extract from.
        since_message_idx: Only process messages from this index onward
            (enables incremental saves).
        workspace_path: Path to the SwarmAI workspace. Auto-detected if None.
        config: Optional app config dict. Auto-loaded if None.

    Returns:
        MemoryExtractionResult with counts of saved entries per section.
    """
    import asyncio
    from database import db

    # 1. Resolve workspace path
    if workspace_path is None:
        try:
            from core.initialization_manager import initialization_manager
            workspace_path = Path(initialization_manager.get_cached_workspace_path())
        except Exception as e:
            logger.error("Cannot resolve workspace path: %s", e)
            return MemoryExtractionResult(error="Workspace not initialized")

    memory_path = workspace_path / ".context" / "MEMORY.md"

    # 2. Load config if not provided
    if config is None:
        try:
            from routers.settings import get_config_manager
            mgr = get_config_manager()
            config = mgr.load()
        except Exception:
            config = {}

    # 3. Load session messages
    try:
        messages = await db.messages.list_by_session(session_id)
    except Exception as e:
        logger.error("Failed to load messages for session %s: %s", session_id, e)
        return MemoryExtractionResult(error="Failed to load session messages")

    total_messages = len(messages)

    if total_messages < MIN_MESSAGES_FOR_EXTRACTION:
        return MemoryExtractionResult(
            error="Not enough conversation to save",
            next_message_idx=total_messages,
        )

    # 4. Format conversation
    conversation = _format_conversation(messages, since_idx=since_message_idx)
    if not conversation.strip():
        return MemoryExtractionResult(
            error="Nothing new to save",
            next_message_idx=total_messages,
        )

    # 5. Load current MEMORY.md for dedup context
    memory_content = ""
    if memory_path.exists():
        try:
            memory_content = memory_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # 6. Build prompt and call LLM
    today = date.today().isoformat()
    prompt = EXTRACTION_PROMPT.format(
        memory_content=memory_content or "(empty)",
        conversation=conversation,
        today=today,
    )

    # Run LLM call in thread to avoid blocking async event loop
    try:
        raw_response = await asyncio.to_thread(_call_llm, prompt, config)
    except Exception as exc:
        # Catch errors that bypass _call_llm's internal try/except
        # (e.g. zlib decompression errors from boto3/urllib3 response handling)
        logger.error(
            "LLM thread call failed for session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return MemoryExtractionResult(
            next_message_idx=total_messages,
            error=f"LLM extraction failed: {type(exc).__name__}",
        )

    # 7. Parse extraction result
    extracted = _parse_extraction(raw_response)
    if not extracted or all(len(v) == 0 for v in extracted.values()):
        return MemoryExtractionResult(
            next_message_idx=total_messages,
            error="Nothing new to save",
        )

    # 8. Write entries to MEMORY.md
    result = MemoryExtractionResult(next_message_idx=total_messages)
    write_failures = 0

    for key, section_header in _SECTION_MAP.items():
        entries = extracted.get(key, [])
        if entries:
            try:
                success = await asyncio.to_thread(
                    _write_entries, entries, section_header, memory_path
                )
            except Exception as exc:
                logger.error(
                    "Write thread failed for section %s, session %s: %s",
                    section_header,
                    session_id,
                    exc,
                    exc_info=True,
                )
                success = False
            if success:
                count = len(entries)
                setattr(result, key, count)
                result.total_saved += count
            else:
                write_failures += 1

    if write_failures > 0 and result.total_saved == 0:
        result.error = "Failed to write to MEMORY.md"

    logger.info(
        "Memory extraction for session %s: saved %d entries (%d decisions, %d lessons, %d threads, %d context)",
        session_id,
        result.total_saved,
        result.key_decisions,
        result.lessons_learned,
        result.open_threads,
        result.recent_context,
    )

    return result
