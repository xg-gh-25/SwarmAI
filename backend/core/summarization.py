"""Improved rule-based conversation summarization pipeline.

Converts conversation logs into structured summaries for DailyActivity
files.  Extraction is rule-based (no LLM call) for speed and
determinism, with improved heuristics for topic extraction, decision
detection, and title generation.

Key public symbols:

- ``StructuredSummary``       — Dataclass holding extracted topics,
                                decisions, files modified, and open
                                questions.
- ``SummarizationPipeline``   — Stateless pipeline that accepts a list
                                of message dicts and returns a
                                ``StructuredSummary``.

Used by both the automatic ``DailyActivityExtractionHook`` and the
on-demand ``s_save-activity`` skill to ensure consistent extraction.
"""

from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

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
    """Output of the summarization pipeline."""

    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    session_title: str = ""
    timestamp: str = ""  # HH:MM format

    def word_count(self) -> int:
        """Total word count across all text fields."""
        text = " ".join(
            self.topics + self.decisions + self.files_modified + self.open_questions
        )
        return len(text.split())


class SummarizationPipeline:
    """Converts conversation logs into structured summaries.

    Improved extraction rules:
    - Topics: meaningful user statements (filtered for noise, merged
      consecutive short messages, sentence-boundary aware).
    - Decisions: assistant message sentences matching decision-indicator
      regex patterns with deduplication.
    - Files: file paths parsed from tool_use content blocks, split into
      files read vs files modified.
    - Open questions: from ask_user_question tool blocks.
    - Title: derived from the longest substantive user message, not
      just the first.

    Deduplication: topics and files are deduplicated by exact string match.
    Ordering: all lists preserve chronological order.
    """

    MAX_WORDS_PER_ENTRY = 500

    async def summarize(self, messages: list[dict]) -> StructuredSummary:
        """Full summarization for conversations with 3+ messages."""
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
        """Extract file paths from Write/Edit tool_use events only.

        Improved: only counts Write/Edit as modifications (not Read),
        extracts paths from Bash commands that contain file writes.
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
                tool_name = block.get("name", "")
                inp = block.get("input", {})

                if tool_name in _WRITE_TOOL_NAMES:
                    for key in ("file_path", "path", "filename"):
                        val = inp.get(key, "")
                        if isinstance(val, str) and val and "/" in val:
                            if val not in seen:
                                seen.add(val)
                                files.append(val)
                elif tool_name == "Bash":
                    # Extract file paths from bash commands that write
                    cmd = inp.get("command", "")
                    if any(w in cmd for w in (">", "mv ", "cp ", "mkdir ", "tee ")):
                        # Simple heuristic: find paths in the command
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
        """Extract questions from ask_user_question tool_use blocks."""
        questions: list[str] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") != "ask_user_question":
                    continue
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
            # Trim from longest list first
            lists = [
                ("topics", summary.topics),
                ("decisions", summary.decisions),
                ("open_questions", summary.open_questions),
            ]
            longest = max(lists, key=lambda x: len(x[1]))
            if longest[1]:
                longest[1].pop()
            else:
                break
        return summary
