"""LLM-powered memory extraction from chat sessions.

Extracts key decisions, lessons learned, open threads, and context from
a conversation and submits them to Cognitive Distillation Flow.

This module powers the one-click "Save to Memory" button in the frontend.
The LLM only extracts complete candidates; CDF owns admission, deduplication,
routing, and all governed writes.

Key public symbols:

- ``extract_and_save``      — Main entry point: extract from messages → enqueue.
- ``MemoryExtractionResult`` — Dataclass with counts of accepted candidates.
- ``EXTRACTION_MODEL``       — Default Bedrock model ID for extraction (Sonnet 4.6).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

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

## Answer
Record what to save with the save tool (an empty list for a section with nothing to save)."""


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


# The four sections the extraction answers, shaped by the API
# (jobs.bedrock.structured(); design docs/design/2026-10-28-structured-model-output.md).
_SECTIONS = ("key_decisions", "lessons_learned", "open_threads", "recent_context")
_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "array", "items": {"type": "string"}} for k in _SECTIONS},
    "required": list(_SECTIONS),
}
_EXTRACTION_MAX_TOKENS = 1024
# Transport: a fail-fast client (30 s read) with boto's standard retry, which backs
# off on throttling and transient network errors; a credential error is retried once
# with fresh credentials (jobs.bedrock.converse_with_retry).
_EXTRACTION_TRANSPORT = {"read_timeout": 30, "max_attempts": 3}


def _extract_sections(prompt: str, config: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """The model's answer to the extraction *prompt*: section -> non-empty entries.

    Model and region come from *config* (``memory_extraction_model``, ``aws_region``).
    Raises jobs.bedrock.StructuredOutputError for an unusable reply and a Bedrock
    error as is; the caller reports either, never as "nothing to save".
    """
    from jobs.bedrock import structured

    config = config or {}
    data = structured(prompt, schema=_EXTRACTION_SCHEMA,
                      model_id=config.get("memory_extraction_model", EXTRACTION_MODEL),
                      max_tokens=_EXTRACTION_MAX_TOKENS, temperature=0.0, name="save",
                      description="Record what to save to memory.",
                      region=config.get("aws_region", "us-east-1"), **_EXTRACTION_TRANSPORT)
    return {k: [e for e in data[k] if e.strip()] for k in _SECTIONS}


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

    # 5. Extract candidate statements. Existing-corpus comparison and all
    # admission decisions belong to CDF, not this source.
    today = date.today().isoformat()
    prompt = EXTRACTION_PROMPT.format(
        memory_content="(CDF performs corpus comparison)",
        conversation=conversation,
        today=today,
    )

    # Run LLM call on the dedicated 'llm' pool (run_c8ad52f8), NOT the default
    # ThreadPoolExecutor — a background memory-extraction burst must never
    # saturate the default pool the readiness sampler shares (→ false offline).
    from jobs.bedrock import StructuredOutputError
    try:
        from core import executors
        extracted = await executors.run_in("llm", _extract_sections, prompt, config)
    except StructuredOutputError as exc:
        logger.warning("Memory extraction reply %s for session %s (%s): %.200s",
                       exc.kind, session_id, exc.detail, exc.raw)
        return MemoryExtractionResult(
            next_message_idx=total_messages,
            error=f"LLM extraction reply {exc.kind}",
        )
    except Exception as exc:
        logger.error(
            "LLM extraction failed for session %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return MemoryExtractionResult(
            next_message_idx=total_messages,
            error=f"LLM extraction failed: {type(exc).__name__}",
        )

    # 7. Nothing to save is a valid answer (every section empty)
    if all(len(v) == 0 for v in extracted.values()):
        return MemoryExtractionResult(
            next_message_idx=total_messages, error="Nothing new to save")

    from core.ingestion import api
    from core.session_inputs import derive_envelope
    envelope = derive_envelope(session_id, (), writer="memory_extractor")
    result = MemoryExtractionResult(next_message_idx=total_messages)
    type_hints = {
        "key_decisions": "decision",
        "lessons_learned": "guideline",
        "open_threads": "process",
        "recent_context": "model",
    }
    for key, entries in extracted.items():
        accepted = 0
        for entry in entries:
            payload = {"kind": "extracted", "text": entry,
                       "type_hint": type_hints.get(key)}
            slot = "entry:" + hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            queued = api.enqueue(
                "memory_extractor", session_id, slot, payload,
                envelope=envelope, project_snapshot={},
            )
            if queued.status in {"queued", "duplicate"}:
                accepted += 1
        if hasattr(result, key):
            setattr(result, key, accepted)
            result.total_saved += accepted
    logger.info("Memory extraction for session %s: queued %d entries",
                session_id, result.total_saved)
    return result
