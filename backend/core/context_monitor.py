"""Context window usage monitor.

Estimates current session context window usage by analyzing the Claude
transcript ``.jsonl`` file.  Returns a structured status dict that the
agent manager uses to decide whether to emit a warning SSE event.

This is the Python-native replacement for the Node.js
``s_context-monitor/context-check.mjs`` script, avoiding subprocess
overhead and integrating directly into the response pipeline.

Key design decisions:
- Reads only the latest transcript in the projects directory.
- Detects compaction boundaries and only counts post-compaction content.
- Uses a mixed-language char/token ratio (~3 chars/token) for estimation.
- Adds a configurable baseline for system prompts + skill injections.
- Returns level ``ok`` | ``warn`` | ``critical`` with thresholds at 70%/85%.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Defaults ---
DEFAULT_WINDOW_TOKENS = 200_000
DEFAULT_BASELINE_TOKENS = 40_000
WARN_PCT = 70
CRITICAL_PCT = 85
CHARS_PER_TOKEN = 3  # compromise between English (~4) and CJK (~1.5)

# Check interval: run the monitor every N user turns.
CHECK_INTERVAL_TURNS = 5

# Compaction marker substring (injected by Claude SDK on context compaction).
_COMPACTION_MARKER = "continued from a previous conversation that ran out of context"


@dataclass
class ContextStatus:
    """Result of a context window usage check."""

    tokens_est: int = 0
    pct: int = 0
    level: str = "ok"  # "ok" | "warn" | "critical"
    message: str = ""
    content_chars: int = 0
    content_tokens: int = 0
    baseline_tokens: int = DEFAULT_BASELINE_TOKENS
    user_messages: int = 0
    assistant_messages: int = 0
    tool_use_blocks: int = 0
    tool_result_blocks: int = 0
    compacted: bool = False
    transcript_file: str = ""
    transcript_size: int = 0

    def to_dict(self) -> dict:
        return {
            "tokensEst": self.tokens_est,
            "pct": self.pct,
            "level": self.level,
            "message": self.message,
            "details": {
                "contentChars": self.content_chars,
                "contentTokens": self.content_tokens,
                "baselineTokens": self.baseline_tokens,
                "userMessages": self.user_messages,
                "assistantMessages": self.assistant_messages,
                "toolCalls": self.tool_use_blocks,
                "toolResults": self.tool_result_blocks,
                "compacted": self.compacted,
                "transcriptFile": self.transcript_file,
                "transcriptSize": self.transcript_size,
            },
        }


def _find_latest_transcript(projects_dir: str) -> Optional[tuple[str, int]]:
    """Find the most recently modified .jsonl transcript.

    Returns (path, size) or None.
    """
    try:
        p = Path(projects_dir)
        if not p.is_dir():
            return None
        candidates = []
        for f in p.iterdir():
            if f.suffix == ".jsonl" and f.is_file():
                stat = f.stat()
                candidates.append((str(f), stat.st_mtime, stat.st_size))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1], reverse=True)
        return (candidates[0][0], candidates[0][2])
    except OSError:
        return None


def _count_content_chars(content) -> int:
    """Recursively count characters in a message content field."""
    total = 0
    if isinstance(content, str):
        total += len(content)
    elif isinstance(content, list):
        for block in content:
            if not block or not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                total += len(block["text"])
            elif btype == "tool_use":
                inp = block.get("input")
                if isinstance(inp, str):
                    total += len(inp)
                elif inp is not None:
                    total += len(json.dumps(inp))
                total += len(block.get("name", "")) + 50  # overhead
            elif btype == "tool_result":
                sub = block.get("content")
                if isinstance(sub, str):
                    total += len(sub)
                elif isinstance(sub, list):
                    for s in sub:
                        if isinstance(s, dict) and isinstance(s.get("text"), str):
                            total += len(s["text"])
                total += 50  # overhead
    return total


def check_context_usage(
    projects_dir: Optional[str] = None,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
) -> ContextStatus:
    """Analyze the latest transcript and return context usage status.

    This is a synchronous function (file I/O only, no network) that can
    be called from the agent manager's response pipeline.

    Args:
        projects_dir: Path to the Claude projects directory containing
            ``.jsonl`` transcript files.  Defaults to
            ``~/.claude/projects/-Users-<user>-...``.
        window_tokens: Total context window size in tokens.

    Returns:
        ContextStatus with usage estimate and level classification.
    """
    if projects_dir is None:
        home = os.path.expanduser("~")
        # Auto-detect: find the most recent projects subdirectory
        claude_projects = Path(home) / ".claude" / "projects"
        if claude_projects.is_dir():
            subdirs = [
                d for d in claude_projects.iterdir()
                if d.is_dir() and any(d.glob("*.jsonl"))
            ]
            if subdirs:
                # Pick the subdir with the most recently modified .jsonl
                best = max(subdirs, key=lambda d: max(
                    f.stat().st_mtime for f in d.glob("*.jsonl")
                ))
                projects_dir = str(best)

    if not projects_dir:
        return ContextStatus(message="No projects directory found")

    result = _find_latest_transcript(projects_dir)
    if not result:
        return ContextStatus(message="No active transcript found")

    file_path, file_size = result
    status = ContextStatus(
        transcript_file=file_path,
        transcript_size=file_size,
    )

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Failed to read transcript %s: %s", file_path, e)
        return status

    # First pass: find last compaction point
    last_compaction_idx = -1
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "user":
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, str) and _COMPACTION_MARKER in content:
            last_compaction_idx = i
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    if _COMPACTION_MARKER in block["text"]:
                        last_compaction_idx = i
                        break

    status.compacted = last_compaction_idx >= 0
    start_idx = last_compaction_idx if last_compaction_idx >= 0 else 0

    # Second pass: count content from after last compaction
    total_chars = 0
    for i in range(start_idx, len(lines)):
        line = lines[i].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        otype = obj.get("type")
        # Skip streaming/progress events (duplicated in assistant messages)
        if otype in ("progress", "queue-operation", "last-prompt"):
            continue

        msg = obj.get("message")
        if not msg:
            continue

        role = msg.get("role")
        content = msg.get("content")

        if role == "user":
            status.user_messages += 1
        elif role == "assistant":
            status.assistant_messages += 1

        # Count content characters
        chars = _count_content_chars(content)
        total_chars += chars

        # Count tool blocks for diagnostics
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        status.tool_use_blocks += 1
                    elif block.get("type") == "tool_result":
                        status.tool_result_blocks += 1

    # Token estimation
    status.content_chars = total_chars
    status.content_tokens = total_chars // CHARS_PER_TOKEN
    status.tokens_est = status.content_tokens + status.baseline_tokens

    # Percentage and level
    status.pct = round((status.tokens_est / window_tokens) * 100) if window_tokens > 0 else 0
    tokens_k = round(status.tokens_est / 1000)
    window_k = window_tokens // 1000

    if status.pct >= CRITICAL_PCT:
        status.level = "critical"
        status.message = (
            f"**Context alert**: Session is {status.pct}% full "
            f"(~{tokens_k}K/{window_k}K tokens). "
            f"Recommend: save context and start a new session."
        )
    elif status.pct >= WARN_PCT:
        status.level = "warn"
        status.message = (
            f"Heads up — we've used about {status.pct}% of this session's "
            f"context window (~{tokens_k}K/{window_k}K tokens). "
            f"Consider saving context soon if more heavy tasks remain."
        )
    else:
        status.level = "ok"
        status.message = (
            f"Context {status.pct}% full "
            f"(~{tokens_k}K/{window_k}K tokens). Plenty of room."
        )

    return status
