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

Used by both the automatic ``DailyActivityExtractionHook`` and the
on-demand ``s_save-activity`` skill to ensure consistent extraction.
"""

from __future__ import annotations

import asyncio
import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Threshold: sessions with more messages than this get LLM enrichment
LLM_ENRICHMENT_THRESHOLD = 8

# Decision-indicator patterns (case-insensitive)
_DECISION_PATTERNS = re.compile(
    r"\b(?:decided to|chose|will use|going with|recommend|"
    r"the approach is|selected|switched to|adopted|confirmed|"
    r"approved|rejected|using .+ instead of|opted for)\b",
    re.IGNORECASE,
)

# Filler/noise patterns to filter from topics
_NOISE_PATTERNS = re.compile(
    r"^(?:ok|yes|no|sure|thanks|thank you|got it|right|"
    r"hmm|ah|oh|please|hi|hello|hey)\b",
    re.IGNORECASE,
)

# Tool names whose input contains file paths
_FILE_TOOL_NAMES = {"Write", "Edit", "Read", "Bash"}

# Write-only tools (for Files Modified, exclude Read)
_WRITE_TOOL_NAMES = {"Write", "Edit"}


@dataclass
class StructuredSummary:
    """Output of the summarization pipeline.

    Core fields (always populated by rule-based extraction):
    - topics, decisions, files_modified, open_questions

    Enriched fields (populated by LLM when session is substantial):
    - actions_taken: what the agent actually did (not just files)
    - reasoning: why key decisions were made
    - rejected_approaches: what was proposed and turned down, with reason
    - continue_from: specific next step for the next session
    - validation_status: what was tested vs untested
    """

    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    # Enriched fields (LLM-powered, empty for short/rule-based sessions)
    actions_taken: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    rejected_approaches: list[str] = field(default_factory=list)
    continue_from: str = ""
    validation_status: str = ""
    session_title: str = ""
    timestamp: str = ""  # HH:MM format

    def word_count(self) -> int:
        """Total word count across all text fields."""
        text = " ".join(
            self.topics + self.decisions + self.files_modified
            + self.open_questions + self.actions_taken
            + self.reasoning + self.rejected_approaches
        )
        if self.continue_from:
            text += " " + self.continue_from
        if self.validation_status:
            text += " " + self.validation_status
        return len(text.split())


_ENRICHMENT_PROMPT = """\
You are summarizing a chat session for a DailyActivity log. The log helps \
the next agent session pick up where this one left off.

## Conversation:
{conversation}

## Already extracted (rule-based):
- Topics: {topics}
- Decisions: {decisions}
- Files modified: {files}

## Your task:
Enrich the summary with context that rule-based extraction misses. \
Be concise — each entry max 120 chars. Only include sections with real content.

## Output (valid JSON only, no markdown fences):
{{
  "actions_taken": ["what the agent did beyond file edits — e.g. diagnosed X, proposed Y, ran tests"],
  "reasoning": ["why key decisions were made — e.g. chose X over Y because Z"],
  "rejected_approaches": ["what was proposed and turned down — e.g. user rejected X because Y"],
  "continue_from": "specific next step for the next session (one sentence)",
  "validation_status": "what was tested vs untested (one sentence)"
}}"""

# Max chars of conversation to send to LLM for enrichment
_ENRICHMENT_MAX_CHARS = 25_000


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

    MAX_WORDS_PER_ENTRY = 700  # Increased from 500 to accommodate enriched fields

    async def summarize(self, messages: list[dict]) -> StructuredSummary:
        """Full summarization for conversations with 3+ messages.

        If the session has more than LLM_ENRICHMENT_THRESHOLD messages,
        attempts LLM enrichment for handoff-level detail. Falls back to
        rule-based summary on LLM failure.
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
                summary = await self._enrich_with_llm(summary, messages)
            except Exception as exc:
                logger.warning(
                    "LLM enrichment failed, using rule-based summary: %s", exc
                )

        return self._enforce_word_limit(summary)

    def minimal_summary(self, messages: list[dict]) -> StructuredSummary:
        """Minimal summary (topics only) for short conversations (<3 msgs)."""
        topics = self._extract_topics(messages)
        return StructuredSummary(
            topics=topics,
            decisions=[],
            files_modified=[],
            open_questions=[],
            session_title=self._derive_title(messages),
            timestamp=datetime.now().strftime("%H:%M"),
        )

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
                    merged = "; ".join(pending_short)[:150]
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
                    merged = "; ".join(pending_short)[:150]
                    if merged not in seen:
                        seen.add(merged)
                        topics.append(merged)
                    pending_short = []
            else:
                # Flush any pending shorts first
                if pending_short:
                    merged = "; ".join(pending_short)[:150]
                    if merged not in seen:
                        seen.add(merged)
                        topics.append(merged)
                    pending_short = []

                if sentence not in seen:
                    seen.add(sentence)
                    topics.append(sentence)

        # Flush remaining
        if pending_short:
            merged = "; ".join(pending_short)[:150]
            if merged not in seen:
                topics.append(merged)

        return topics

    @staticmethod
    def _extract_first_sentence(text: str) -> str:
        """Extract the first meaningful sentence from text.

        Uses sentence boundaries (period, question mark, exclamation,
        or newline) and caps at 120 chars.
        """
        # Split on sentence boundaries
        parts = re.split(r"[.!?\n]", text, maxsplit=1)
        sentence = parts[0].strip()

        # Cap length
        if len(sentence) > 120:
            # Try to break at a word boundary
            truncated = sentence[:120]
            last_space = truncated.rfind(" ")
            if last_space > 80:
                sentence = truncated[:last_space] + "..."
            else:
                sentence = truncated + "..."

        return sentence

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
            msg_decisions = 0
            for sentence in re.split(r"[.!?\n]", text):
                sentence = sentence.strip()
                if not sentence or len(sentence) < 20:
                    continue
                if _DECISION_PATTERNS.search(sentence):
                    # Normalize for dedup
                    normalized = sentence.lower().strip()
                    if normalized in seen_normalized:
                        continue
                    seen_normalized.add(normalized)

                    if len(sentence) > 150:
                        sentence = sentence[:150] + "..."
                    decisions.append(sentence)
                    msg_decisions += 1
                    if msg_decisions >= 3:  # Cap per message
                        break

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
                    # Summary format: "Running: command..."
                    cmd = summary.removeprefix("Running: ").strip()
                    if any(w in cmd for w in (">", "mv ", "cp ", "mkdir ", "tee ")):
                        for token in cmd.split():
                            if "/" in token and not token.startswith("-"):
                                clean = token.strip("'\">;|&")
                                if clean and clean not in seen and len(clean) > 3:
                                    seen.add(clean)
                                    files.append(clean)
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
                    cmd = inp.get("command", "")
                    if any(w in cmd for w in (">", "mv ", "cp ", "mkdir ", "tee ")):
                        for token in cmd.split():
                            if "/" in token and not token.startswith("-"):
                                clean = token.strip("'\">;|&")
                                if clean and clean not in seen and len(clean) > 3:
                                    seen.add(clean)
                                    files.append(clean)
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
                            questions.append(q[:200])
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
        # Cap at 60 chars for title
        if len(title) > 60:
            truncated = title[:60]
            last_space = truncated.rfind(" ")
            if last_space > 40:
                title = truncated[:last_space]
            else:
                title = truncated
        return title.rstrip(".")

    def _enforce_word_limit(self, summary: StructuredSummary) -> StructuredSummary:
        """Trim fields to stay within MAX_WORDS_PER_ENTRY."""
        while summary.word_count() > self.MAX_WORDS_PER_ENTRY:
            # Trim from longest list first (include enriched fields)
            lists = [
                ("topics", summary.topics),
                ("decisions", summary.decisions),
                ("open_questions", summary.open_questions),
                ("actions_taken", summary.actions_taken),
                ("reasoning", summary.reasoning),
                ("rejected_approaches", summary.rejected_approaches),
            ]
            longest = max(lists, key=lambda x: len(x[1]))
            if longest[1]:
                longest[1].pop()
            else:
                break
        return summary

    async def _enrich_with_llm(
        self, summary: StructuredSummary, messages: list[dict]
    ) -> StructuredSummary:
        """Enrich a rule-based summary with LLM-extracted context.

        Calls Bedrock (same pattern as memory_extractor) to extract
        reasoning, rejected approaches, continue-from, and validation
        status. On failure, returns the original summary unchanged.
        """
        conversation = self._format_conversation_for_llm(messages)
        if not conversation.strip():
            return summary

        prompt = _ENRICHMENT_PROMPT.format(
            conversation=conversation,
            topics="; ".join(summary.topics[:10]) or "(none)",
            decisions="; ".join(summary.decisions[:5]) or "(none)",
            files="; ".join(summary.files_modified[:10]) or "(none)",
        )

        raw = await asyncio.to_thread(self._call_enrichment_llm, prompt)
        if not raw or raw == "{}":
            return summary

        enriched = self._parse_enrichment(raw)
        if not enriched:
            return summary

        # Merge enriched fields into summary
        summary.actions_taken = enriched.get("actions_taken", [])
        summary.reasoning = enriched.get("reasoning", [])
        summary.rejected_approaches = enriched.get("rejected_approaches", [])
        summary.continue_from = enriched.get("continue_from", "")
        summary.validation_status = enriched.get("validation_status", "")

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

        result = "\n\n".join(lines)

        if len(result) > _ENRICHMENT_MAX_CHARS:
            result = (
                "...[truncated older messages]...\n\n"
                + result[-_ENRICHMENT_MAX_CHARS:]
            )

        return result

    @staticmethod
    def _call_enrichment_llm(prompt: str) -> str:
        """Make a Bedrock API call for enrichment extraction.

        Budget constraint: This runs inside DailyActivityExtractionHook
        which has a 30s timeout.  The DB query + rule-based extraction
        take ~1-2s, file write ~0.1s.  So the LLM call has ~20s budget.

        Timeouts are set conservatively:
        - connect_timeout=5s, read_timeout=15s → worst case 20s per attempt
        - Single attempt (no retry) — enrichment is best-effort, rule-based
          summary is the safety net
        - boto3 adaptive retry handles transient AWS errors internally

        Uses Sonnet for quality. Only runs for sessions >8 messages.
        """
        import boto3
        from botocore.config import Config as BotoConfig

        model_id = "us.anthropic.claude-sonnet-4-6"
        region = "us-east-1"

        boto_config = BotoConfig(
            retries={"max_attempts": 1, "mode": "adaptive"},
            connect_timeout=5,
            read_timeout=15,
        )

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
                    "max_tokens": 512,
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
            logger.warning("Enrichment LLM call failed: %s", e)
            return "{}"

    @staticmethod
    def _parse_enrichment(raw: str) -> dict[str, Any]:
        """Parse the LLM enrichment JSON response."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return {}

        try:
            data = json.loads(text[start:end])
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse enrichment JSON: %s", e)
            return {}

        result: dict[str, Any] = {}
        for key in ("actions_taken", "reasoning", "rejected_approaches"):
            entries = data.get(key, [])
            if isinstance(entries, list):
                result[key] = [e for e in entries if isinstance(e, str) and e.strip()]
            else:
                result[key] = []

        for key in ("continue_from", "validation_status"):
            val = data.get(key, "")
            result[key] = val if isinstance(val, str) else ""

        return result
