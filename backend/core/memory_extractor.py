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

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# Direct import — avoids sys.executable subprocess which breaks in PyInstaller bundles
from scripts.locked_write import locked_read_modify_write, LockedWriteError
from core.ddd_entry_lifecycle import MEMORY_TYPE_TO_SECTION

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


# Map extraction keys to MEMORY.md section headers. Values MUST be current
# section names from the MEMORY_SECTIONS SSoT — the legacy "Key Decisions" /
# "Lessons Learned" / "Recent Context" names were removed in PRI01, and writing
# to a missing section silently orphans the entry into a "## Distilled" fallback
# that the index/recall/caps never manage (R3 write-target drift fix).
#   key_decisions  → Decisions
#   lessons_learned→ Guidelines (a lesson is a guideline in the 7-type schema)
#   open_threads / recent_context → Processes (Gate-2 F3: NOT "Open Threads" —
#       that section is structured with ### P0/P1/P2 subsections, and prepend
#       writes would land an unstructured bullet above the structure, breaking
#       the format the deleted comment warned about. Processes is a flat bullet
#       list (process-type), the correct home for unstructured in-flight notes.)
_SECTION_MAP = {
    "key_decisions": MEMORY_TYPE_TO_SECTION["decision"],
    "lessons_learned": MEMORY_TYPE_TO_SECTION["guideline"],
    "open_threads": MEMORY_TYPE_TO_SECTION["process"],
    "recent_context": MEMORY_TYPE_TO_SECTION["process"],
}


def _write_entries(
    entries: list[str],
    section: str,
    memory_path: Path,
) -> bool:
    """Write extracted entries to MEMORY.md via locked_read_modify_write.

    Each extracted line (``- YYYY-MM-DD: Title — body``) is written as a canonical
    entry of the section's type (``canonical_entry_from_text``), so the
    context-health sweep can parse it and remove an exact re-save. All entries for a
    section are joined into a single prepend call. Calls the function directly
    instead of spawning a subprocess (which breaks in PyInstaller bundles where
    ``sys.executable`` points to the bundled binary, not Python).

    Returns:
        True if write succeeded, False otherwise.
    """
    if not entries:
        return True

    from core.ddd_entry_lifecycle import MEMORY_SECTIONS, canonical_entry_from_text
    etype = MEMORY_SECTIONS.get(section, {}).get("type", "guideline")
    today = date.today().isoformat()
    text = "\n".join(
        canonical_entry_from_text(e, etype, today, source="manual") for e in entries
    )

    try:
        # Written whole: write-time dedup was deleted (design
        # docs/design/2026-10-25-memory-write-entry-dedup.md); a re-saved entry is an
        # exact duplicate the sweep removes at session close.
        locked_read_modify_write(memory_path, section, text, mode="prepend")
        return True
    except LockedWriteError as e:
        logger.error("locked_write failed for section %s: %s", section, e)
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
    # OFF-LOOP (run_a1f4c2d8): MEMORY.md is the largest context file in the system and
    # this read feeds the dedup prompt — exists + read in one hop.
    def _read_memory() -> str:
        if not memory_path.exists():
            return ""
        return memory_path.read_text(encoding="utf-8")

    memory_content = ""
    try:
        memory_content = await asyncio.to_thread(_read_memory)
    except Exception:
        pass

    # 6. Build prompt and call LLM
    today = date.today().isoformat()
    prompt = EXTRACTION_PROMPT.format(
        memory_content=memory_content or "(empty)",
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
            next_message_idx=total_messages,
            error="Nothing new to save",
        )

    # 7.5 JUDGE GATE (run_04fd397c, XG decision A): the manual "Save to Memory"
    # button was a MEMORY BACKDOOR — it wrote LLM-extracted entries with only a dedup
    # check, never the self_adversarial judge, violating "the judge is the sole admit
    # authority" (P8). Even a user-initiated save now goes through the judge (decision
    # A: user intent does NOT bypass it; judge fail-closed is the floor). Each entry is
    # routed through admit_memory_lesson; only verdict=="auto" survives. A judge-discard
    # is dropped (not written) — logged for observability. Section stays as the LLM
    # classified it for the manual-save UX (the judge decides admit/reject, not re-route).
    try:
        from core.ingestion_gate import admit_memory_lesson
        from core import executors as _executors
        gated: dict[str, list[str]] = {}
        _dropped = 0
        for key, section_header in _SECTION_MAP.items():
            kept = []
            for entry in extracted.get(key, []):
                # NOTE (adversarial BUG#2, judged unfounded): _sec is deliberately
                # ignored. The LLM extraction already sorted this entry into `key`,
                # and _SECTION_MAP[key] is its correct type-section (decisions→Decisions
                # etc.). The judge's job here is ADMIT/REJECT only — re-routing to the
                # judge's section would BREAK the extraction bucket→section consistency.
                # One blocking Bedrock call per entry: the llm pool, never the loop.
                verdict, _sec, reason, distilled = await _executors.run_in(
                    "llm", admit_memory_lesson, entry)
                if verdict == "auto":
                    # ROOT-FIX (capture-vs-distill): write the DISTILLED rule when the
                    # gate rewrote a shape-dirty entry; fail-open → distilled=None keeps
                    # the original. The writer never finalizes its own text.
                    kept.append(distilled or entry)
                elif verdict == "pending":
                    # RECOVERABLE (judge budget-exhausted / infra down) — DEFER to the
                    # shared distill-pending.jsonl, never drop. Even a user's manual
                    # "Save to Memory" is not lost when the judge is transiently down;
                    # the next distillation cycle re-judges it with a fresh budget.
                    try:
                        from hooks.distillation_hook import requeue_pending_lesson
                        requeue_pending_lesson(memory_path.parent, "lesson", entry)
                        logger.info("memory_extractor: entry deferred (%s): %.80s", reason, entry)
                    except Exception:  # noqa: BLE001 — defer is best-effort
                        logger.warning("memory_extractor: pending defer FAILED: %.80s", entry)
                else:
                    _dropped += 1
                    logger.info("memory_extractor: judge DISCARD (%s): %.80s", reason, entry)
            if kept:
                gated[key] = kept
        extracted = gated
        if _dropped:
            logger.info("memory_extractor: judge dropped %d entries (session %s)", _dropped, session_id)
        if not extracted:
            return MemoryExtractionResult(
                next_message_idx=total_messages,
                error="Nothing passed the memory admission judge",
            )
    except Exception as exc:  # noqa: BLE001 — gate error must fail-CLOSED (write nothing)
        logger.error("memory_extractor: judge gate crashed, writing NOTHING (fail-closed): %s", exc)
        return MemoryExtractionResult(
            next_message_idx=total_messages,
            error=f"Memory admission judge failed: {type(exc).__name__}",
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
