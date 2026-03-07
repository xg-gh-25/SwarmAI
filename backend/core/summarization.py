"""Rule-based conversation summarization pipeline.

Converts conversation logs into structured summaries for DailyActivity
files.  Extraction is entirely rule-based (no LLM call) for speed and
determinism.

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
    r"the approach is|selected)\b",
    re.IGNORECASE,
)

# Tool names whose input contains file paths
_FILE_TOOL_NAMES = {"Write", "Edit", "Read", "Bash"}


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

    Extraction rules:
    - Topics: first sentence (up to first period or 80 chars) of each
      user message.
    - Decisions: assistant message sentences matching decision-indicator
      regex patterns.
    - Files: file paths parsed from tool_use content blocks where tool
      name matches Write/Edit/Read/Bash.
    - Open questions: question text from ask_user_question tool_use blocks.

    Deduplication: topics and files are deduplicated by exact string match.
    Ordering: all lists preserve chronological order.
    """

    MAX_WORDS_PER_ENTRY = 500

    async def summarize(self, messages: list[dict]) -> StructuredSummary:
        """Full summarization for conversations with 3+ messages."""
        topics = self._extract_topics(messages)
        decisions = self._extract_decisions(messages)
        files = self._extract_files(messages)
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
        """Extract first sentence of each user message, deduplicated."""
        seen: set[str] = set()
        topics: list[str] = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = self._get_text_content(msg)
            if not text:
                continue
            # First sentence: up to first period or 80 chars
            sentence = text.split(".")[0][:80].strip()
            if sentence and sentence not in seen:
                seen.add(sentence)
                topics.append(sentence)
        return topics

    def _extract_decisions(self, messages: list[dict]) -> list[str]:
        """Extract sentences with decision-indicator patterns from assistant msgs."""
        decisions: list[str] = []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            text = self._get_text_content(msg)
            if not text:
                continue
            for sentence in re.split(r"[.!?\n]", text):
                sentence = sentence.strip()
                if sentence and _DECISION_PATTERNS.search(sentence):
                    if len(sentence) > 120:
                        sentence = sentence[:120] + "..."
                    decisions.append(sentence)
        return decisions

    def _extract_files(self, messages: list[dict]) -> list[str]:
        """Extract file paths from tool_use events (Write/Edit/Read/Bash)."""
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
                if tool_name not in _FILE_TOOL_NAMES:
                    continue
                inp = block.get("input", {})
                # Try common field names for file paths
                for key in ("file_path", "path", "filename", "command"):
                    val = inp.get(key, "")
                    if isinstance(val, str) and val and "/" in val:
                        if val not in seen:
                            seen.add(val)
                            files.append(val)
        return files

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
        """Derive a session title from the first user message."""
        for msg in messages:
            if msg.get("role") == "user":
                text = SummarizationPipeline._get_text_content(msg)
                if text:
                    return text[:60].strip()
        return "Untitled session"

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
