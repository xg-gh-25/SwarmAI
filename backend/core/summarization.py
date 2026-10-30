"""The activity record of one chat session for one day (DailyActivity).

Every session with an exchange is summarized by the model: a title naming the work, a
sentence saying what was done and where it ended, its deliverables, follow-ups and the
follow-ups it resolved (design docs/design/2026-10-31-daily-activity-useful.md). The
rule-based fields (topics, decisions, files, open questions) are the user's words and
tool calls: they feed the noise filter and the self-heal checkpoint, never the row.

Key public symbols:

- ``StructuredSummary``     — one session's record for one day.
- ``SummarizationPipeline`` — messages in, ``StructuredSummary`` out.

Used by ``DailyActivityExtractionHook`` and ``scripts/da_resummarize.py``.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.tool_summarizer import MAX_SUMMARY_LENGTH

logger = logging.getLogger(__name__)

# Every session with an exchange (a message and a reply) is summarized by the model;
# below this the record is the derived title and nothing else.
LLM_ENRICHMENT_THRESHOLD = 1

# Output budget for the answer. At 2048 a long session's answer (mostly CJK, ~1 token
# per character) was cut and the rule-based fallback stood, so the row repeated the
# user's words. Nine fields fit far below 16000; a reply that still reaches it is
# reported as truncated (jobs.bedrock.structured) and the fallback stands. One call
# reads at most 150 s; the hook writes after each part and declares its own 900 s
# hang guard (DailyActivityExtractionHook.timeout_seconds).
_ENRICHMENT_MAX_TOKENS = 16000
_ENRICHMENT_READ_TIMEOUT_S = 150

# The answer, shaped by the API (jobs.bedrock.structured(); design
# docs/design/2026-10-28-structured-model-output.md). Only fields something reads.
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary_sentence": {"type": "string"},
        "deliverables": _STR_LIST, "key_outputs": _STR_LIST,
        "validation_status": {"type": "string"}, "next_items": _STR_LIST, "resolves": _STR_LIST,
        "coe_signal": {"type": "string"}, "coe_topic": {"type": "string"},
    },
    "required": ["title", "summary_sentence", "deliverables", "key_outputs",
                 "validation_status", "next_items", "resolves", "coe_signal", "coe_topic"],
}

# Centralized patterns — shared with distillation_hook.py to prevent drift.
# Summarization uses the BROAD variant (runs on raw conversation).
# structural_noise wraps is_noise_entry. Matches on all non-empty input; differs only
# on empty/whitespace (structural_noise→True vs is_noise_entry→False) — unreachable
# here (the call site gates on _DECISION_PATTERNS.search first, so an empty sentence
# never reaches it).
from .extraction_patterns import (
    DECISION_PATTERNS_BROAD as _DECISION_PATTERNS,
    AGENT_MONOLOGUE as _AGENT_MONOLOGUE,
    NOISE_PATTERNS as _NOISE_PATTERNS,
    structural_noise as _is_noise_entry,
)

# Tool names whose input contains file paths
_FILE_TOOL_NAMES = {"Write", "Edit", "Read", "Bash"}

# Write-only tools (for Files Modified, exclude Read)
_WRITE_TOOL_NAMES = {"Write", "Edit"}


@dataclass
class StructuredSummary:
    """One session's record for one day.

    Rule-based, from the messages: ``topics`` (user messages), ``decisions``,
    ``files_modified`` (write tool calls), ``open_questions`` and a derived
    ``session_title``. Written by the model (``enriched`` says it answered):
    ``session_title``, ``summary_sentence``, ``deliverables``, ``key_outputs``,
    ``validation_status``, ``next_items`` (``[{"next_id", "text"}]`` once the session id
    is known), ``resolves`` (ids from the open list), ``coe_signal``
    ("" | "candidate" | "resolution") and ``coe_topic``. ``git_commits`` is the
    session's commits that day, set by the hook.
    """

    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    key_outputs: list[str] = field(default_factory=list)
    validation_status: str = ""
    coe_signal: str = ""
    coe_topic: str = ""
    git_commits: list[str] = field(default_factory=list)
    session_title: str = ""
    timestamp: str = ""  # HH:MM of the last message summarized
    summary_sentence: str = ""
    next_items: list[dict[str, str]] = field(default_factory=list)
    resolves: list[str] = field(default_factory=list)
    enriched: bool = False


_ENRICHMENT_PROMPT = """\
You are writing the activity record of one chat session for one day. The next session
reads it to learn what was done and what is still open, so describe the work and its
result, not the conversation.
{earlier}{so_far}
## Conversation on {day}{part}:
{conversation}

## Files written by tool calls: {files}

## Open follow-ups from the previous 7 days
{open_next_items}

Record it with the summary tool, in the language the session is held in. Never truncate
a sentence or add an ellipsis. Use an empty string or array when there is nothing.
- title: a few words naming the work, as a noun phrase. Not the user's request quoted.
- summary_sentence: what was done and where it ended, in one or two short sentences
  (about 40 words, or 80 characters in Chinese). Name the result (built, fixed,
  decided, answered, dropped, still in progress), not that a discussion took place.
  The details go in deliverables.
- deliverables: results that exist at the end of this transcript, each understandable
  alone: a commit or file and what it does, a decision and its reason, the answer to a
  question. Not plans and not the user's requests.
- key_outputs: where the results are: file paths, commits, documents, recipients.
- validation_status: what was verified and how; what is still unverified.
- next_items: follow-ups agreed or clearly left open and not done in this transcript,
  each a complete action someone can pick up without the conversation. Empty when the
  work is finished. Do not assign ids.
- resolves: ids from the open list whose completion is explicit in this transcript.
  Not by similarity, and not plans.
- coe_signal: "candidate" when the session reports a production problem that is still
  being investigated, "resolution" when it fixes one, otherwise "".
- coe_topic: that problem, or "". If it is one of these open problems, copy its title
  exactly, character for character: {open_problems}
"""

_SO_FAR_BLOCK = """
## The record so far for this day (from the earlier part of the conversation)
{record}
The conversation below continues the same day. Answer with the record for the whole
day: keep what still holds, add what is new, and leave out follow-ups that got done.
"""

# Conversation per model call (estimated tokens). The model's window is 200K; a long
# day is summarized part by part, each part carrying the record so far, so every
# message reaches the model whole (no message is ever cut).
_CHUNK_TOKENS = 100_000

def record_of(row: dict[str, Any]) -> dict[str, Any]:
    """The record so far, from a sidecar row: the answer's fields, follow-ups as text."""
    return {
        "title": str(row.get("title") or ""),
        "summary_sentence": str(row.get("summary_sentence") or ""),
        "deliverables": list(row.get("deliverables") or []),
        "key_outputs": list(row.get("key_outputs") or []),
        "validation_status": str(row.get("validation_status") or ""),
        "next_items": [str(i.get("text")) for i in row.get("next_items") or []
                       if isinstance(i, dict) and i.get("text")],
        "resolves": list(row.get("resolves") or []),
        "coe_signal": str(row.get("coe_signal") or ""),
        "coe_topic": str(row.get("coe_topic") or ""),
    }


_EARLIER_BLOCK = """
## Earlier in this session
Already recorded on {day}: {title}: {sentence}
Record only what happened on the day of the conversation below.
"""


def _bash_written_files(cmd: str, *, truncated: bool) -> list[str]:
    """Files a Bash command writes, by the SSOT parser the file tracker uses
    (core.file_change_classifier.parse_bash_write_targets): /dev/null, fd dups,
    quoted text and heredoc bodies never count. Directory targets (``cp a /tmp/``)
    are not files. When the command was cut (a stored summary is capped), a
    target that runs to the cut is a fragment, so it is dropped.
    """
    if not cmd:
        return []
    from core.file_change_classifier import parse_bash_write_targets
    try:
        targets = parse_bash_write_targets(cmd)
    except Exception:  # noqa: BLE001 — the parser is best-effort; no files is safe
        logger.debug("_bash_written_files: parse failed", exc_info=True)
        return []
    tail = cmd.rstrip()
    out = []
    for t in targets:
        if not t or t.endswith("/"):
            continue
        if truncated and tail.endswith(t):
            continue
        out.append(t)
    return out


class SummarizationPipeline:
    """Messages of one session-day in, its ``StructuredSummary`` out.

    The rule-based fields are always extracted. With an exchange, the model writes the
    record (``_enrich_with_llm``); without its answer the record keeps the derived
    title and nothing the user said is presented as done or open.
    """

    async def summarize(
        self,
        messages: list[dict],
        *,
        session_id: str = "",
        open_next_items: list[dict[str, str]] | None = None,
        open_problems: list[str] | None = None,
        earlier: dict[str, str] | None = None,
        day: str = "",
        enrich: bool = True,
        so_far: dict[str, Any] | None = None,
    ) -> StructuredSummary:
        """Summarize *messages* (one session, one day).

        ``so_far``: the record already written for the earlier messages of this day
        (``record_of``); *messages* then continue it and the answer is the record for
        the whole day so far.
        ``open_next_items``: open follow-ups of the previous 7 days; the model may
        resolve only these ids. ``open_problems``: titles of the open COEs
        (core.coe_threads), copied when the session reports the same problem.
        ``earlier``: ``{"day", "title", "sentence"}`` of this session's last row on an
        earlier day, so the model records only what is new. ``enrich=False`` keeps
        the rule-based fields only (the self-heal checkpoint cannot wait for a model).
        Only Exception is caught around the model call: CancelledError must propagate.
        """
        summary = StructuredSummary(
            topics=self._extract_topics(messages),
            decisions=self._extract_decisions(messages),
            files_modified=self._extract_files_modified(messages),
            open_questions=self._extract_open_questions(messages),
            session_title=self._derive_title(messages),
            timestamp=self._last_time(messages),
        )
        if enrich and (so_far or len(messages) > LLM_ENRICHMENT_THRESHOLD):
            try:
                summary = await self._enrich_with_llm(
                    summary, messages, open_next_items=open_next_items or [],
                    open_problems=open_problems or [], earlier=earlier, day=day,
                    so_far=so_far,
                )
            except Exception as exc:
                logger.warning("Activity summary model call failed: %s", exc)

        summary.summary_sentence = self._one_line(summary.summary_sentence)
        self._assign_next_ids(summary, session_id)
        # the record so far keeps the ids it resolved: they are no longer open
        allowed_resolutions = {
            item.get("next_id", "") for item in (open_next_items or [])
        } | set((so_far or {}).get("resolves") or [])
        summary.resolves = [item for item in summary.resolves if item in allowed_resolutions]
        return summary

    @staticmethod
    def _last_time(messages: list[dict]) -> str:
        """HH:MM of the last message (naive local ``created_at``), else now."""
        for msg in reversed(messages):
            raw = msg.get("created_at")
            if not raw:
                continue
            try:
                return datetime.fromisoformat(str(raw)).strftime("%H:%M")
            except ValueError:
                continue
        return datetime.now().strftime("%H:%M")

    def _extract_topics(self, messages: list[dict]) -> list[str]:
        """Extract meaningful topics from user messages.

        Improvements over v1:
        - Filters noise (ok/yes/thanks) and very short messages (<10 chars)
        - Uses sentence boundary (. or first 120 chars) not just truncation
        - Merges consecutive short user messages into one topic
        - Strips leading question words for cleaner topic lines
        """
        seen: set[str] = set()
        topics: list[str] = []
        pending_short: list[str] = []

        for msg in messages:
            if msg.get("role") != "user":
                # Flush pending short messages when non-user message arrives
                if pending_short:
                    merged = "; ".join(pending_short)
                    if merged not in seen:
                        seen.add(merged)
                        topics.append(merged)
                    pending_short = []
                continue

            text = self._get_text_content(msg).strip()
            if not text or len(text) < 10:
                continue

            # Filter noise
            if _NOISE_PATTERNS.match(text) and len(text) < 30:
                continue

            # Extract first meaningful sentence
            sentence = self._extract_first_sentence(text)
            if not sentence or len(sentence) < 10:
                continue

            # Short messages get merged
            if len(sentence) < 40:
                pending_short.append(sentence)
                if len(pending_short) >= 3:
                    merged = "; ".join(pending_short)
                    if merged not in seen:
                        seen.add(merged)
                        topics.append(merged)
                    pending_short = []
            else:
                # Flush any pending shorts first
                if pending_short:
                    merged = "; ".join(pending_short)
                    if merged not in seen:
                        seen.add(merged)
                        topics.append(merged)
                    pending_short = []

                if sentence not in seen:
                    seen.add(sentence)
                    topics.append(sentence)

        # Flush remaining
        if pending_short:
            merged = "; ".join(pending_short)
            if merged not in seen:
                topics.append(merged)

        return topics

    @staticmethod
    def _extract_first_sentence(text: str) -> str:
        """Return the first complete sentence or line without slicing it."""
        match = re.search(r"[.!?\n]", text)
        if match is None:
            return text.strip()
        end = match.end()
        return text[:end].strip()

    def _extract_decisions(self, messages: list[dict]) -> list[str]:
        """Extract sentences with decision-indicator patterns from assistant msgs.

        Improved: deduplicates by normalized content, caps per-message.
        """
        decisions: list[str] = []
        seen_normalized: set[str] = set()

        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            text = self._get_text_content(msg)
            if not text:
                continue
            for sentence in re.split(r"[.!?\n]", text):
                sentence = sentence.strip()
                if not sentence or len(sentence) < 20:
                    continue
                if _DECISION_PATTERNS.search(sentence):
                    # Filter agent monologue and table fragments
                    if _AGENT_MONOLOGUE.match(sentence):
                        continue
                    if _is_noise_entry(sentence):
                        continue
                    # Normalize for dedup
                    normalized = sentence.lower().strip()
                    if normalized in seen_normalized:
                        continue
                    seen_normalized.add(normalized)

                    decisions.append(sentence)

        return decisions

    def _extract_files_modified(self, messages: list[dict]) -> list[str]:
        """Extract file paths from write tool_use events.

        DB-stored messages use a summarized format from ``_format_message``:
        ``{"type": "tool_use", "name": "Write", "summary": "Writing to path/file",
        "category": "write"}``.  The raw ``input`` dict is NOT stored.

        This method handles both formats:
        - **Summarized** (DB path): uses ``category`` and parses paths from ``summary``
        - **Raw** (direct SDK path): uses ``name`` and reads ``input`` dict

        Only write-category tools count as modifications (not reads).
        """
        seen: set[str] = set()
        files: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue

                category = block.get("category", "")
                tool_name = block.get("name", "")
                summary = block.get("summary", "")
                inp = block.get("input", {})

                # Path 1: Summarized format (DB-stored messages)
                if category == "write" and summary:
                    # Summary format: "Writing to path/to/file"
                    path = summary.removeprefix("Writing to ").strip()
                    if path and "/" in path and path not in seen:
                        seen.add(path)
                        files.append(path)
                    continue

                if category == "bash" and summary:
                    # Summary format: "Running: command..." (cut at MAX_SUMMARY_LENGTH)
                    cmd = summary.removeprefix("Running: ").strip()
                    # Redaction runs before the cut and can shorten the text, so a cut
                    # summary may sit a little under the cap: treat "near the cap" as cut.
                    for path in _bash_written_files(cmd, truncated=len(summary) >= MAX_SUMMARY_LENGTH - 30):
                        if path not in seen:
                            seen.add(path)
                            files.append(path)
                    continue

                # Path 2: Raw format (direct SDK messages, fallback)
                if tool_name in _WRITE_TOOL_NAMES and isinstance(inp, dict):
                    for key in ("file_path", "path", "filename"):
                        val = inp.get(key, "")
                        if isinstance(val, str) and val and "/" in val:
                            if val not in seen:
                                seen.add(val)
                                files.append(val)
                elif tool_name == "Bash" and isinstance(inp, dict):
                    for path in _bash_written_files(inp.get("command", "") or "", truncated=False):
                        if path not in seen:
                            seen.add(path)
                            files.append(path)
        return files

    # Keep backward compatibility — old callers may use _extract_files
    _extract_files = _extract_files_modified

    def _extract_open_questions(self, messages: list[dict]) -> list[str]:
        """Extract open questions from the conversation.

        ``AskUserQuestion`` events are NOT stored as tool_use content blocks
        in the DB — they're yielded as separate SSE events.  So we can't
        extract them from stored messages directly.

        Instead, we look for user messages that appear to be answers to
        questions (contain JSON with ``"answers"`` key from
        ``continue_with_answer``), which indicates a question was asked.
        This is a best-effort heuristic.
        """
        questions: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                # Check for the raw SDK format (in case messages come from
                # a non-DB source like the on-demand skill)
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name == "AskUserQuestion" or name == "ask_user_question":
                        inp = block.get("input", {})
                        q = inp.get("question", "")
                        if q:
                            questions.append(q)
        return questions

    @staticmethod
    def _get_text_content(msg: dict) -> str:
        """Extract plain text from a message's content field."""
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return " ".join(parts)
        return ""

    @staticmethod
    def _derive_title(messages: list[dict]) -> str:
        """Derive a session title from the most substantive user message.

        Improved: picks the longest non-trivial user message instead of
        the first, and cleans it up for use as a title.
        """
        best_text = ""
        best_len = 0

        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = SummarizationPipeline._get_text_content(msg).strip()
            if not text or len(text) < 10:
                continue
            # Skip noise
            if _NOISE_PATTERNS.match(text) and len(text) < 30:
                continue
            # Prefer the longest substantive message (within first 10 user msgs)
            if len(text) > best_len:
                best_text = text
                best_len = len(text)

        if not best_text:
            return "Untitled session"

        # Extract first sentence and clean up
        title = SummarizationPipeline._extract_first_sentence(best_text)
        return title.rstrip(".")

    @staticmethod
    def _one_line(text: str) -> str:
        """*text* on one line, whitespace collapsed; nothing cut."""
        return " ".join((text or "").split())

    @staticmethod
    def _assign_next_ids(summary: StructuredSummary, session_id: str) -> None:
        """Assign stable short ids from session identity, ordinal, and full text."""
        import hashlib

        assigned: list[dict[str, str]] = []
        for ordinal, item in enumerate(summary.next_items, 1):
            text = item.get("text", "") if isinstance(item, dict) else str(item)
            text = text.strip()
            if not text:
                continue
            material = f"{session_id}\0{ordinal}\0{text}".encode("utf-8")
            assigned.append({
                "next_id": hashlib.sha256(material).hexdigest()[:12],
                "text": text,
            })
        summary.next_items = assigned

    async def _enrich_with_llm(
        self,
        summary: StructuredSummary,
        messages: list[dict],
        *,
        open_next_items: list[dict[str, str]] | None = None,
        open_problems: list[str] | None = None,
        earlier: dict[str, str] | None = None,
        day: str = "",
        so_far: dict[str, Any] | None = None,
    ) -> StructuredSummary:
        """Fill the model-written fields of *summary*; unchanged when no answer."""
        chunks = [self._format_conversation_for_llm(part) for part in self.parts(messages)]
        chunks = [chunk for chunk in chunks if chunk.strip()]
        if not chunks:
            return summary

        earlier_text = ""
        if earlier and (earlier.get("title") or earlier.get("sentence")):
            earlier_text = _EARLIER_BLOCK.format(
                day=earlier.get("day", ""), title=earlier.get("title", ""),
                sentence=earlier.get("sentence", ""),
            )
        # Dedicated 'llm' pool (run_c8ad52f8), not the default ThreadPoolExecutor:
        # this runs in the post-session hook window — if the user sends a message
        # during it, a default-pool model call would compete with the readiness
        # sampler. The dedicated pool insulates it.
        from core import executors
        answer: dict = dict(so_far or {})
        for index, conversation in enumerate(chunks, 1):
            prompt = _ENRICHMENT_PROMPT.format(
                earlier=earlier_text,
                so_far=(_SO_FAR_BLOCK.format(record=json.dumps(answer, ensure_ascii=False))
                        if answer else ""),
                day=day or datetime.now().date().isoformat(),
                part=f" (part {index} of {len(chunks)})" if len(chunks) > 1 else "",
                conversation=conversation,
                files="; ".join(summary.files_modified) or "(none)",
                open_next_items=json.dumps(open_next_items or [], ensure_ascii=False),
                open_problems="; ".join(f'"{x}"' for x in (open_problems or [])) or "(none)",
            )
            answer = await executors.run_in("llm", self._call_enrichment_llm, prompt)
            if not answer:
                return summary  # the call failed; _call_enrichment_llm logged why
        enriched = self._normalize_enrichment(answer)

        if enriched["title"]:
            summary.session_title = enriched["title"]
        summary.summary_sentence = enriched["summary_sentence"]
        summary.deliverables = enriched["deliverables"]
        summary.key_outputs = enriched["key_outputs"]
        summary.validation_status = enriched["validation_status"]
        summary.next_items = [{"text": item} for item in enriched["next_items"]]
        summary.resolves = enriched["resolves"]
        summary.coe_signal = enriched["coe_signal"]
        summary.coe_topic = enriched["coe_topic"]
        summary.enriched = True
        return summary

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """A conservative token estimate: ~1.1 per CJK character, ~1 per 3 others."""
        cjk = sum(1 for ch in text if "\u3400" <= ch <= "\u9fff")
        return int(cjk * 1.1 + (len(text) - cjk) / 3) + 1

    @classmethod
    def parts(cls, messages: list[dict]) -> list[list[dict]]:
        """*messages* in parts of about ``_CHUNK_TOKENS``, split between messages; a
        message longer than that is a part of its own, whole."""
        parts: list[list[dict]] = []
        current: list[dict] = []
        used = 0
        for msg in messages:
            cost = cls._estimate_tokens(cls._format_conversation_for_llm([msg]))
            if current and used + cost > _CHUNK_TOKENS:
                parts.append(current)
                current, used = [], 0
            current.append(msg)
            used += cost
        if current:
            parts.append(current)
        return parts

    @staticmethod
    def _format_conversation_for_llm(messages: list[dict]) -> str:
        """The messages as ``User:`` / ``Assistant:`` text, every message whole.

        Text blocks are kept; a tool call is shown by its name only; tool results are
        not included. Nothing is cut.
        """
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if isinstance(content, list):
                text_parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[Tool: {block.get('name', '?')}]")
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)

            if not content or not content.strip():
                continue

            prefix = "User" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {content.strip()}")

        return "\n\n".join(lines)

    @staticmethod
    def _call_enrichment_llm(prompt: str) -> dict:
        """The model's enrichment answer for *prompt*, or {} when there is none.

        Budget: runs inside DailyActivityExtractionHook (its own 900 s hang guard,
        one call per part), in the background after the session ends. Asked through
        jobs.bedrock.structured() with a fail-fast client (read timeout
        _ENRICHMENT_READ_TIMEOUT_S, no boto retry); best-effort: on any failure the
        rule-based summary is the safety net. Model and region come from the user's
        AppConfigManager, so enrichment works in whatever region is configured.
        """
        try:
            from core.app_config_manager import AppConfigManager
            cfg = AppConfigManager.instance()
            region = cfg.get("aws_region") or "us-east-1"
            model_id = (cfg.get("bedrock_model_map") or {}).get(
                "claude-sonnet-4-6", "us.anthropic.claude-sonnet-4-6")
        except Exception:
            region = "us-east-1"
            model_id = "us.anthropic.claude-sonnet-4-6"

        from jobs.bedrock import StructuredOutputError, structured
        try:
            return structured(prompt, schema=_ENRICHMENT_SCHEMA, model_id=model_id, feature="summarize.session",
                              max_tokens=_ENRICHMENT_MAX_TOKENS, temperature=0.0,
                              name="summary", description="Record the session summary.",
                              region=region, read_timeout=_ENRICHMENT_READ_TIMEOUT_S,
                              max_attempts=1)
        except StructuredOutputError as e:
            logger.warning("Enrichment reply %s (%s); keeping the rule-based summary: %.200s",
                           e.kind, e.detail, e.raw)
            return {}
        except Exception as e:
            # Read/connection timeouts to Bedrock are transient and harmless
            # here — enrichment is best-effort and the caller keeps the summary.
            # Log those at INFO to stop the recurring WARNING noise (×7/day);
            # keep genuine (non-transient) failures at WARNING.
            _name = type(e).__name__
            if _name in (
                "ReadTimeoutError", "ConnectTimeoutError",
                "ConnectionError", "EndpointConnectionError",
            ) or "timeout" in str(e).lower():
                logger.info(
                    "Enrichment LLM call timed out (transient — skipping enrichment): %s", e
                )
            else:
                logger.warning("Enrichment LLM call failed: %s", e)
            return {}

    @staticmethod
    def _normalize_enrichment(data: dict) -> dict[str, Any]:
        """The answer's fields, normalized: every string on one line (a line break in a
        title or follow-up would add a line of its own to the day file and the prompt),
        non-string and empty entries dropped, an unknown coe_signal reset to ""."""
        result: dict[str, Any] = {}
        for key in ("deliverables", "key_outputs", "next_items", "resolves"):
            entries = data.get(key, [])
            entries = entries if isinstance(entries, list) else []
            result[key] = [SummarizationPipeline._one_line(e) for e in entries
                           if isinstance(e, str) and e.strip()]
        for key in ("title", "summary_sentence", "validation_status", "coe_signal",
                    "coe_topic"):
            val = data.get(key, "")
            result[key] = SummarizationPipeline._one_line(val) if isinstance(val, str) else ""
        if result["coe_signal"] not in ("", "candidate", "resolution"):
            result["coe_signal"] = ""
        return result
