"""Post-session hook that detects skill invocations and records metrics.

Scans assistant messages for tool_use blocks and text patterns indicating
skill invocations, then records each detected invocation to SkillMetricsStore.

Key public symbols:

- ``SkillMetricsHook`` -- Hook that detects and records skill usage metrics.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from core.extraction_patterns import CORRECTION_PATTERNS as _CORRECTION_PATTERNS
from core.session_hooks import HookContext

logger = logging.getLogger(__name__)

# Patterns in assistant text content indicating skill invocation
_SKILL_TEXT_PATTERNS = re.compile(
    r"(?:Using Skill|Launching skill|Invoking skill)[:\s]+['\"]?(\S+)",
    re.IGNORECASE,
)


class SkillMetricsHook:
    """Post-session hook that records skill invocation metrics.

    Implements SessionLifecycleHook protocol.
    Runs post-session (like DailyActivityExtractionHook):
    1. Loads messages from DB for the session
    2. Scans assistant messages for tool_use blocks (Skill tool invocations)
    3. Scans assistant text content for "Using Skill:" / "Launching skill:" patterns
    4. Records each detected skill invocation to SkillMetricsStore
    5. Determines outcome: "success" if no user correction follows, "correction" otherwise
    6. Estimates duration from timestamps between skill invocation and next user message
    """

    def __init__(self) -> None:
        self._store: "SkillMetricsStore | None" = None

    def _get_store(self, db_path: "Path") -> "SkillMetricsStore":
        """Return a cached SkillMetricsStore instance, creating on first call."""
        if self._store is None:
            from core.skill_metrics import SkillMetricsStore
            self._store = SkillMetricsStore(db_path)
        return self._store

    @property
    def name(self) -> str:
        return "skill-metrics"

    async def execute(self, context: HookContext) -> None:
        """Detect skill invocations in session messages and record metrics."""
        try:
            from database import db
            from config import get_app_data_dir

            # Load messages for this session
            messages_raw = await db.messages.list(
                filters={"session_id": context.session_id}
            )
            if not messages_raw:
                logger.debug("No messages for session %s, skipping skill metrics", context.session_id)
                return

            # Detect skill invocations
            invocations = _detect_skill_invocations(messages_raw)
            if not invocations:
                return

            # Record each invocation (cached store avoids re-creation per session)
            db_path = get_app_data_dir() / "data.db"
            store = self._get_store(db_path)
            for inv in invocations:
                store.record(
                    skill_name=inv["skill_name"],
                    session_id=context.session_id,
                    outcome=inv["outcome"],
                    duration_seconds=inv["duration_seconds"],
                    user_satisfaction=inv["user_satisfaction"],
                )
            logger.info(
                "SkillMetricsHook: recorded %d skill invocation(s) for session %s",
                len(invocations), context.session_id,
            )

        except Exception as exc:
            logger.error("SkillMetricsHook failed: %s", exc, exc_info=True)


def _detect_skill_invocations(messages: list[dict]) -> list[dict]:
    """Scan messages for skill invocations and determine outcomes.

    Returns list of dicts with keys:
        skill_name, outcome, duration_seconds, user_satisfaction
    """
    invocations: list[dict] = []

    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content", "")
        skill_names: list[str] = []
        msg_timestamp = msg.get("created_at", "")

        # 1. Check for tool_use blocks (content may be a JSON list)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    if tool_name == "Skill" or tool_name.startswith("Skill"):
                        # Extract skill name from input
                        inp = block.get("input", {})
                        skill_arg = inp.get("skill", "") if isinstance(inp, dict) else ""
                        if skill_arg:
                            skill_names.append(skill_arg)
            # Also extract text content from blocks for pattern matching
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content_text = " ".join(text_parts)
        elif isinstance(content, str):
            content_text = content
        else:
            continue

        # 2. Check text content for skill invocation patterns
        for match in _SKILL_TEXT_PATTERNS.finditer(content_text):
            skill_name = match.group(1).strip("'\"")
            if skill_name and skill_name not in skill_names:
                skill_names.append(skill_name)

        if not skill_names:
            continue

        # 3. Determine outcome by checking next user message
        user_satisfaction = "accepted"
        outcome = "success"
        duration_seconds = 0.0

        # Find next user message
        next_user_msg = None
        for j in range(i + 1, len(messages)):
            if messages[j].get("role") == "user":
                next_user_msg = messages[j]
                break

        if next_user_msg is not None:
            next_content = next_user_msg.get("content", "")
            if isinstance(next_content, list):
                text_parts = []
                for block in next_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                next_text = " ".join(text_parts)
            elif isinstance(next_content, str):
                next_text = next_content
            else:
                next_text = ""

            if _CORRECTION_PATTERNS.search(next_text):
                user_satisfaction = "correction"
                outcome = "partial"

            # Estimate duration from timestamps
            next_timestamp = next_user_msg.get("created_at", "")
            duration_seconds = _estimate_duration(msg_timestamp, next_timestamp)

        # NOTE: when multiple skills appear in the same assistant message,
        # all share the same outcome/satisfaction from the single next user
        # message. Separating per-skill correction detection would require
        # NLU to map correction text to specific skills — deferred.
        for skill_name in skill_names:
            invocations.append({
                "skill_name": skill_name,
                "outcome": outcome,
                "duration_seconds": duration_seconds,
                "user_satisfaction": user_satisfaction,
            })

    return invocations


def _estimate_duration(start_ts: str, end_ts: str) -> float:
    """Estimate duration between two ISO timestamps."""
    if not start_ts or not end_ts:
        return 0.0
    try:
        start = datetime.fromisoformat(start_ts)
        end = datetime.fromisoformat(end_ts)
        return max(0.0, (end - start).total_seconds())
    except (ValueError, TypeError):
        return 0.0
