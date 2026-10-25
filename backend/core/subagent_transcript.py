"""Read an expert sub-agent's own transcript (Claude-format JSONL): the spawn prompt at
its head, and its final text. Used by the SwarmAI session's SubagentStop hook
(``runtime_hooks``) and by ``python-backend hook subagent-stop`` (``core.host_hooks``).

Public symbols: ``read_prompt_head``, ``read_final_text``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def read_prompt_head(transcript_path: str) -> str:
    """Return the sub-agent's spawn prompt from the HEAD of its transcript (the
    first sidechain user message), or "" on any error. The spawn prompt carries
    the EXPERT-ID line; unlike the error-capture hook (which reads the TAIL for
    errors), it lives at the head. Bounded read, fail-soft."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(12):  # scan only the first few records
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message", rec)
                content = msg.get("content", "") if isinstance(msg, dict) else msg
                if isinstance(content, list):  # content-block array
                    # Join with "\n", NOT " ": the SDK can split the first user
                    # message into multiple text blocks (observed: a resume-history
                    # block + the prompt block). A SPACE join collapses the newline
                    # after the `EXPERT-ID: <id>` anchor line into trailing text on
                    # that line, so the `^EXPERT-ID: <id>\s*$` extractor (which
                    # requires the id to END the line) fails → expert_id=None → the
                    # D7 commit-gate false-blocks a completed review. "\n" preserves
                    # the real line boundary so the anchor stays its own line, while
                    # genuinely authored trailing junk on one line stays junk (the
                    # extractor regex is UNCHANGED — the fail-safe tests still hold).
                    content = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                if isinstance(content, str) and content.strip():
                    return content[:2000]
        return ""
    except Exception:
        logger.debug("read_prompt_head: suppressed exception on a fail-safe path", exc_info=True)
        return ""


_GATE_ANSWER_TAIL_BYTES = 256 * 1024  # fallback transcript read; the SDK input carries the text


def read_final_text(transcript_path: str) -> str:
    """The text of the sub-agent's LAST assistant message (bounded tail read), or ""."""
    try:
        p = Path(transcript_path)
        size = p.stat().st_size
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            if size > _GATE_ANSWER_TAIL_BYTES:
                f.seek(size - _GATE_ANSWER_TAIL_BYTES)
                f.readline()  # skip the partial first line
            lines = f.read().splitlines()
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message", rec)
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(content, str) and content.strip():
                return content
        return ""
    except Exception:
        logger.debug("read_final_text: suppressed exception on a fail-safe path", exc_info=True)
        return ""
