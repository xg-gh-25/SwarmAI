"""Hybrid rule-based + LLM conversation summarization pipeline.

Converts conversation logs into structured summaries for DailyActivity
files.  Short sessions use rule-based extraction (fast, deterministic).
Substantial sessions (>8 messages) use LLM enrichment to capture
handoff-level detail: reasoning, rejected approaches, next steps,
and validation status.

Key public symbols:

- ``StructuredSummary``       — Dataclass holding extracted topics,
                                decisions, files modified, open questions,
                                and enriched fields (actions, reasoning,
                                rejected approaches, continue-from).
- ``SummarizationPipeline``   — Stateless pipeline that accepts a list
                                of message dicts and returns a
                                ``StructuredSummary``.

Used by the automatic ``DailyActivityExtractionHook``.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Single source of truth for the 7-type schema (P8: never hardcode a second copy).
from core.ddd_entry_lifecycle import VALID_TYPES as _VALID_TYPES
from core.tool_summarizer import MAX_SUMMARY_LENGTH
VALID_TYPE_SET = frozenset(_VALID_TYPES)

logger = logging.getLogger(__name__)

# Threshold: sessions with more messages than this get LLM enrichment
LLM_ENRICHMENT_THRESHOLD = 8

# Output budget for the enrichment answer. At 512 the 12-field answer (Chinese
# entries cost ~1 token/char) was routinely cut; 2048 leaves headroom. A reply that
# still reaches it is reported as truncated (jobs.bedrock.structured) and the
# rule-based summary stands. The hook's budget is 180 s (main.py
# SessionLifecycleHookManager), so the read timeout covers a full-length generation.
_ENRICHMENT_MAX_TOKENS = 2048
_ENRICHMENT_READ_TIMEOUT_S = 90

# The enrichment answer, shaped by the API (jobs.bedrock.structured(); design
# docs/design/2026-10-28-structured-model-output.md). lesson_types and coe_signal stay
# plain strings: an unknown label is normalized below, not a reason to lose the answer.
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary_sentence": {"type": "string"},
        "deliverables": _STR_LIST, "key_outputs": _STR_LIST,
        "validation_status": {"type": "string"}, "next_items": _STR_LIST, "resolves": _STR_LIST,
        "rejected_approaches": _STR_LIST, "corrections": _STR_LIST,
        "process_reflection": {"type": "string"}, "signal_driven_actions": _STR_LIST,
        "coe_signal": {"type": "string"}, "coe_topic": {"type": "string"},
        # Not asked for by the CDF working-memory prompt; still read when present.
        "lessons": _STR_LIST, "lesson_types": _STR_LIST, "continue_from": {"type": "string"},
    },
    "required": ["summary_sentence", "deliverables", "key_outputs", "validation_status",
                 "next_items", "resolves", "rejected_approaches", "corrections",
                 "coe_signal", "coe_topic"],
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
    """Output of the summarization pipeline.

    Designed around three questions:
    1. **What did we accomplish?** (deliverables — outcomes, not activities)
    2. **Where are the outputs?** (key_outputs — file paths, recipients)
    3. **What did we learn?** (lessons — highlights, lowlights, insights)

    Plus COE signaling for complex debugging sessions, and continuation
    context for the next session.

    Core fields (always populated by rule-based extraction):
    - topics, decisions, files_modified, open_questions

    Enriched fields (populated by LLM when session is substantial):
    - deliverables: concrete outcomes (not "what happened" but "what was delivered")
    - key_outputs: file paths + what they contain, or "sent to <person>"
    - lessons: insights, highlights, lowlights — things worth remembering
    - rejected_approaches: what was proposed and turned down, with reason
    - continue_from: specific next step for the next session
    - validation_status: what was tested vs untested
    - coe_signal: "" | "candidate" | "resolution" — for COE detection
    - coe_topic: the problem being investigated (for cross-session clustering)
    """

    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    # Enriched fields (LLM-powered, empty for short/rule-based sessions)
    deliverables: list[str] = field(default_factory=list)
    key_outputs: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    # Parallel type-label array for lessons (7-type schema: guideline/pitfall/
    # decision/model/process/principle/correction). LLM-emitted so the
    # DailyActivity distillation path can HONOR the author-time type instead of
    # keyword-guessing it (classify_entry_type is ~82% and pitfall-greedy).
    # INVARIANT: normalized to len(lessons) at parse time (short→pad "guideline",
    # long→truncate) so index i of lessons and lesson_types always correspond.
    # Metadata, NOT prose — deliberately excluded from word_count().
    lesson_types: list[str] = field(default_factory=list)
    rejected_approaches: list[str] = field(default_factory=list)
    continue_from: str = ""
    validation_status: str = ""
    # COE signal fields (LLM-detected)
    coe_signal: str = ""      # "" | "candidate" | "resolution"
    coe_topic: str = ""       # Problem topic for cross-session clustering
    # Corrections: user corrected agent behavior (auto-captured for EVOLUTION.md)
    corrections: list[str] = field(default_factory=list)
    # Git ground truth: actual commits made during the session timeframe.
    # Populated by DailyActivityExtractionHook from `git log --since=<session_start>`.
    # Gives distillation a verifiable signal for implementation claims.
    git_commits: list[str] = field(default_factory=list)
    # Process reflection: LLM-generated meta-analysis of HOW the session went,
    # not just WHAT was discussed. Captures iteration patterns, blind spots,
    # perspective gaps, and work-process lessons that the agent might not
    # explicitly acknowledge. Fed into DailyActivity for downstream evolution.
    process_reflection: str = ""
    # Signal-driven actions: when an external signal (news, release, announcement)
    # directly influenced a session decision. Tracks the causal link from
    # signal input → action output. Format: ["Signal: X → Action: Y", ...]
    signal_driven_actions: list[str] = field(default_factory=list)
    # Legacy fields (kept for backward compat, will be empty for new sessions)
    actions_taken: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    session_title: str = ""
    timestamp: str = ""  # HH:MM format
    # DailyActivity v2 fields. ``summary_sentence`` is generated as a complete
    # sentence, never sliced from another field. ``next_items`` is populated as
    # ``[{"next_id": ..., "text": ...}]`` once the session id is known.
    summary_sentence: str = ""
    next_items: list[dict[str, str]] = field(default_factory=list)
    resolves: list[str] = field(default_factory=list)

    def word_count(self) -> int:
        """Total word count across all text fields."""
        text = " ".join(
            self.topics + self.decisions + self.files_modified
            + self.open_questions + self.deliverables + self.key_outputs
            + self.lessons + self.rejected_approaches + self.corrections
            + self.actions_taken + self.reasoning  # legacy
        )
        if self.continue_from:
            text += " " + self.continue_from
        if self.validation_status:
            text += " " + self.validation_status
        return len(text.split())


_ENRICHMENT_PROMPT = """\
You are extracting a high-signal activity summary from a complete chat session. This is a
working-memory handoff, not a transcript and not a source of durable lessons.

## Conversation:
{conversation}

## Already extracted:
- Topics: {topics}
- Decisions: {decisions}
- Files modified: {files}

## Open Next items from the previous 7 days
{open_next_items}

Record the summary with the summary tool. Preserve every complete semantic item; never
truncate a sentence or add an ellipsis. Include empty arrays when a section has no content.
- summary_sentence: one complete sentence describing the session, 30–50 Chinese characters,
  or equivalent length in the session language. Write it directly at that length.
- deliverables: concrete outcomes.
- key_outputs: output locations and descriptions.
- validation_status: what was validated and what remains unvalidated.
- next_items: complete follow-up actions. Do not assign ids; the server assigns stable ids.
- resolves: only ids from the open list whose completion is explicit in this transcript.
  Do not infer completion from similarity or plans.
- coe_signal: "", "candidate", or "resolution".
- coe_topic: the investigated problem, or "". If it is the same problem as one of these
  open problems, copy that title exactly, character for character, so the earlier
  report and this one are recognised as one problem: {open_problems}
- corrections, rejected_approaches, process_reflection, and signal_driven_actions may be
  included for their existing downstream consumers. Do not emit a lessons section.

An example answer (the contents show the expected style):
{{
  "summary_sentence": "本次完成了会话工作记忆改造，并验证完整语义不会在生成和注入时被截断。",
  "deliverables": [],
  "key_outputs": [],
  "validation_status": "",
  "next_items": [],
  "resolves": [],
  "rejected_approaches": [],
  "corrections": [],
  "process_reflection": "",
  "signal_driven_actions": [],
  "coe_signal": "",
  "coe_topic": ""
}}"""


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
    """Converts conversation logs into structured summaries.

    Two-tier extraction:
    - Rule-based (all sessions): topics, decisions, files, open questions.
      Fast and deterministic.
    - LLM-enriched (sessions > LLM_ENRICHMENT_THRESHOLD messages): adds
      actions taken, reasoning, rejected approaches, continue-from, and
      validation status. Falls back to rule-based on LLM failure.

    Deduplication: topics and files are deduplicated by exact string match.
    Ordering: all lists preserve chronological order.
    """

    async def summarize(
        self,
        messages: list[dict],
        *,
        session_id: str = "",
        open_next_items: list[dict[str, str]] | None = None,
        open_problems: list[str] | None = None,
    ) -> StructuredSummary:
        """Full summarization for conversations with 3+ messages.

        If the session has more than LLM_ENRICHMENT_THRESHOLD messages,
        attempts LLM enrichment for handoff-level detail. Falls back to
        rule-based summary on LLM failure.

        ``open_problems``: titles of the COEs still open (core.coe_threads).
        The enrichment is asked to copy one exactly when this session reports or
        resolves the same problem, so the COE topic matches its thread by key.
        """
        topics = self._extract_topics(messages)
        decisions = self._extract_decisions(messages)
        files = self._extract_files_modified(messages)
        questions = self._extract_open_questions(messages)

        summary = StructuredSummary(
            topics=topics,
            decisions=decisions,
            files_modified=files,
            open_questions=questions,
            session_title=self._derive_title(messages),
            timestamp=datetime.now().strftime("%H:%M"),
        )

        # LLM enrichment for substantial sessions.
        # Only catches Exception — CancelledError (BaseException) must propagate
        # for proper asyncio task cancellation (Python 3.12+ re-cancels if
        # swallowed).  The tight LLM timeouts (5s connect + 15s read = 20s max)
        # ensure this fits within the 30s hook budget in normal operation.
        # On shutdown cancellation, the entry is lost — same as before this
        # change, just with a slightly larger window.
        if len(messages) > LLM_ENRICHMENT_THRESHOLD:
            try:
                summary = await self._enrich_with_llm(
                    summary, messages, open_next_items=open_next_items or [],
                    open_problems=open_problems or [],
                )
            except Exception as exc:
                logger.warning(
                    "LLM enrichment failed, using rule-based summary: %s", exc
                )

        if not self._summary_sentence_is_valid(summary.summary_sentence):
            summary.summary_sentence = self._derive_summary_sentence(summary)
        if not summary.continue_from:
            summary.continue_from = self._derive_continue_from(summary)
        if not summary.next_items and summary.continue_from:
            summary.next_items = [{"text": summary.continue_from}]
        self._assign_next_ids(summary, session_id)
        allowed_resolutions = {
            item.get("next_id", "") for item in (open_next_items or [])
        }
        summary.resolves = [item for item in summary.resolves if item in allowed_resolutions]
        return summary

    def minimal_summary(self, messages: list[dict], *, session_id: str = "") -> StructuredSummary:
        """Minimal summary for a short conversation without dropping semantic content."""
        topics = self._extract_topics(messages)
        summary = StructuredSummary(
            topics=topics,
            decisions=[],
            files_modified=[],
            open_questions=[],
            session_title=self._derive_title(messages),
            timestamp=datetime.now().strftime("%H:%M"),
        )
        summary.summary_sentence = self._derive_summary_sentence(summary)
        summary.continue_from = self._derive_continue_from(summary)
        if summary.continue_from:
            summary.next_items = [{"text": summary.continue_from}]
        self._assign_next_ids(summary, session_id)
        return summary

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
    def _derive_continue_from(summary: StructuredSummary) -> str:
        """Rule-based fallback for continue_from when LLM doesn't generate one.

        Priority chain:
        1. If there are open questions → "Resolve: <first question>"
        2. If validation_status mentions untested → "Verify: <validation>"
        3. If COE candidate → "Investigate: <coe_topic>"
        4. Fallback → "Ongoing: <session_title>"
        """
        if summary.open_questions:
            return f"Resolve: {summary.open_questions[0]}"
        if summary.validation_status and "untest" in summary.validation_status.lower():
            return f"Verify: {summary.validation_status}"
        if summary.coe_signal == "candidate" and summary.coe_topic:
            return f"Investigate: {summary.coe_topic}"
        if summary.session_title and summary.session_title != "Untitled session":
            return f"Ongoing: {summary.session_title}"
        return ""

    @staticmethod
    def _summary_sentence_is_valid(text: str) -> bool:
        """Validate a complete one-sentence summary without modifying it."""
        if not text or "\n" in text or text.rstrip().endswith(("...", "…")):
            return False
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        if cjk:
            return 30 <= len(text) <= 50
        words = text.split()
        return 8 <= len(words) <= 18

    @staticmethod
    def _derive_summary_sentence(summary: StructuredSummary) -> str:
        """Generate one complete language-matched fallback at the target length."""
        source = " ".join([*summary.deliverables, *summary.topics, summary.session_title])
        if re.search(r"[\u3400-\u9fff]", source):
            return "本次会话已记录主要工作，并完整保留交付结果、输出位置、验证状态与后续事项。"
        return "This session recorded its work, outputs, validation status, and remaining next steps."

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

    def _enforce_word_limit(self, summary: StructuredSummary) -> StructuredSummary:
        """Compatibility no-op: DailyActivity v2 never drops semantic entries."""
        return summary

    async def _enrich_with_llm(
        self,
        summary: StructuredSummary,
        messages: list[dict],
        *,
        open_next_items: list[dict[str, str]] | None = None,
        open_problems: list[str] | None = None,
    ) -> StructuredSummary:
        """Enrich a rule-based summary with LLM-extracted context.

        Calls Bedrock (jobs.bedrock.structured) to extract
        reasoning, rejected approaches, continue-from, and validation
        status. On failure, returns the original summary unchanged.
        """
        conversation = self._format_conversation_for_llm(messages)
        if not conversation.strip():
            return summary

        prompt = _ENRICHMENT_PROMPT.format(
            conversation=conversation,
            topics="; ".join(summary.topics) or "(none)",
            decisions="; ".join(summary.decisions) or "(none)",
            files="; ".join(summary.files_modified) or "(none)",
            open_next_items=json.dumps(open_next_items or [], ensure_ascii=False),
            open_problems="; ".join(f'"{x}"' for x in (open_problems or [])) or "(none)",
        )

        # Dedicated 'llm' pool (run_c8ad52f8), not the default ThreadPoolExecutor:
        # this enrichment fires in the post-session hook window — if the user sends
        # a message during it, a default-pool LLM call would compete with the
        # readiness sampler. The dedicated pool insulates it.
        from core import executors
        answer = await executors.run_in("llm", self._call_enrichment_llm, prompt)
        if not answer:
            return summary  # the call failed; _call_enrichment_llm logged why
        enriched = self._normalize_enrichment(answer)

        # Merge enriched fields into summary
        summary.deliverables = enriched.get("deliverables", [])
        summary.key_outputs = enriched.get("key_outputs", [])
        summary.lessons = enriched.get("lessons", [])
        # lesson_types is normalized to len(lessons) in _normalize_enrichment (bound
        # as pairs), so index alignment holds. Fallback: if absent (shouldn't be),
        # pad to guideline so downstream zip never misaligns.
        _lt = enriched.get("lesson_types", [])
        if len(_lt) != len(summary.lessons):
            _lt = (_lt + ["guideline"] * len(summary.lessons))[: len(summary.lessons)]
        summary.lesson_types = _lt
        summary.rejected_approaches = enriched.get("rejected_approaches", [])
        summary.corrections = enriched.get("corrections", [])
        summary.process_reflection = enriched.get("process_reflection", "")
        summary.signal_driven_actions = enriched.get("signal_driven_actions", [])
        summary.continue_from = enriched.get("continue_from", "")
        summary.validation_status = enriched.get("validation_status", "")
        summary.coe_signal = enriched.get("coe_signal", "")
        summary.coe_topic = enriched.get("coe_topic", "")
        summary.summary_sentence = enriched.get("summary_sentence", "")
        summary.next_items = [
            {"text": item} for item in enriched.get("next_items", [])
        ]
        summary.resolves = enriched.get("resolves", [])

        return summary

    @staticmethod
    def _format_conversation_for_llm(messages: list[dict]) -> str:
        """Format messages into a readable conversation for the LLM prompt.

        Truncates from the beginning if over _ENRICHMENT_MAX_CHARS
        (keeps recent messages which are most relevant for continue-from).
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

        Budget: runs inside DailyActivityExtractionHook, whose per-hook timeout is
        180 s (main.py), in the background after the session ends. Asked through
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
        """The fields of an enrichment answer the summary takes, normalized: empty
        entries dropped, lessons and lesson_types bound as pairs, an unknown type or
        coe_signal reset. (process_reflection and signal_driven_actions are asked for
        but not taken here; see the known issue in the design's implementation notes.)"""
        result: dict[str, Any] = {}
        for key in ("deliverables", "key_outputs",
                     "rejected_approaches", "corrections",
                     "actions_taken", "reasoning", "next_items", "resolves"):  # legacy
            entries = data.get(key, [])
            if isinstance(entries, list):
                result[key] = [e for e in entries if isinstance(e, str) and e.strip()]
            else:
                result[key] = []

        # lessons + lesson_types are BOUND: normalize lesson_types to len(lessons)
        # and filter empty-lesson entries as PAIRS, so index i always corresponds.
        # (Filtering lessons alone would shift indices vs a parallel type array —
        # the exact fragility Gate-1 flagged. Bind here, at the single parse point.)
        raw_lessons = data.get("lessons", [])
        raw_lessons = raw_lessons if isinstance(raw_lessons, list) else []
        raw_types = data.get("lesson_types", [])
        raw_types = raw_types if isinstance(raw_types, list) else []
        if len(raw_types) != len(raw_lessons) and raw_types:
            logger.warning(
                "enrichment lesson_types/lessons length mismatch: %d types vs %d lessons "
                "— normalizing (short→pad 'guideline', long→truncate)",
                len(raw_types), len(raw_lessons),
            )
        lessons_out: list[str] = []
        types_out: list[str] = []
        for i, les in enumerate(raw_lessons):
            if not (isinstance(les, str) and les.strip()):
                continue  # drop empty lesson AND its paired type together
            t = raw_types[i] if i < len(raw_types) else "guideline"
            t = t.lower() if isinstance(t, str) else "guideline"
            if t not in VALID_TYPE_SET:
                t = "guideline"
            lessons_out.append(les)
            types_out.append(t)
        result["lessons"] = lessons_out
        result["lesson_types"] = types_out

        for key in ("continue_from", "validation_status", "coe_signal", "coe_topic",
                    "summary_sentence"):
            val = data.get(key, "")
            result[key] = val if isinstance(val, str) else ""

        # Validate coe_signal
        if result.get("coe_signal") not in ("", "candidate", "resolution"):
            result["coe_signal"] = ""

        return result
