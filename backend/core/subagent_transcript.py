"""Read an expert sub-agent's own transcript (Claude-format JSONL): the spawn prompt at
its head, and its final text. Used by the SwarmAI session's SubagentStop hook
(``runtime_hooks``) and by ``python-backend hook subagent-stop`` (``core.host_hooks``).

``spawn_evidence`` is what an answer needs before it is recorded (handout-by-file
design §3.3): the hashes of the sub-agent's whole first message, and whether it read
a given file from the top without the read failing.

Public symbols: ``read_prompt_head``, ``read_final_text``, ``spawn_evidence``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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


_EVIDENCE_SCAN_BYTES = 16 * 1024 * 1024   # same bound as runtime_hooks._prompt_reads
_CAT_RE = re.compile(r"(?:^|[;&|(]\s*|\s)cat\s+(?:-\w+\s+)*(['\"]?)([^\s'\"`;|&]+)\1")


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def _message_text(content) -> str:
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def spawn_evidence(transcript_path: str, prompt_file: "str | None") -> "dict | None":
    """``{"first_shas": set, "read_ok": bool}`` for an expert's transcript, or None when
    it cannot be read. ``first_shas``: sha256 of the WHOLE first user message as given and
    with one final newline added. ``read_ok``: a Read of *prompt_file* starting at its top
    (no offset, or 0 / 1) or a ``cat`` of it, whose tool_result is not an error. Paths
    compare by realpath. A later paged Read does not matter; a Read that starts in the
    middle is not a read of the top."""
    try:
        p = Path(transcript_path)
        if not p.is_file() or p.stat().st_size > _EVIDENCE_SCAN_BYTES:
            return None
        target = _real(prompt_file) if prompt_file else None
        first: "str | None" = None
        pending: set[str] = set()
        read_ok = False
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                msg = rec.get("message", rec)
                content = msg.get("content") if isinstance(msg, dict) else None
                if rec.get("type") == "user" and first is None:
                    first = _message_text(content)
                    continue
                if target is None or not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        hit = False
                        if b.get("name") == "Read" and isinstance(inp.get("file_path"), str):
                            hit = (inp.get("offset") in (None, 0, 1)
                                   and _real(inp["file_path"]) == target)
                        elif b.get("name") == "Bash" and isinstance(inp.get("command"), str):
                            hit = any(_real(m[1]) == target for m in _CAT_RE.findall(inp["command"]))
                        if hit and isinstance(b.get("id"), str):
                            pending.add(b["id"])
                    elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        if not b.get("is_error"):
                            read_ok = True
        if first is None:
            return None
        variants = {first, first + "\n"}   # a host may drop the final newline
        return {"first_shas": {hashlib.sha256(v.encode("utf-8")).hexdigest() for v in variants},
                "read_ok": read_ok}
    except Exception:
        logger.debug("spawn_evidence: suppressed exception on a fail-safe path", exc_info=True)
        return None
