"""Tests for the summarization pipeline (core/summarization.py).

Covers:
- Topic extraction from user messages (noise filtering, merging, dedup)
- Decision extraction from assistant messages (pattern matching, monologue filtering)
- File modification extraction (summarized and raw format)
- Open question extraction
- Session title derivation
- Continue-from fallback logic
- Word limit enforcement
- Structured summary dataclass
- Shared extraction patterns (via extraction_patterns.py)

Does NOT test LLM enrichment (_enrich_with_llm) — that requires Bedrock.
"""

from __future__ import annotations

import pytest

from core.summarization import SummarizationPipeline, StructuredSummary
from core.extraction_patterns import (
    DECISION_PATTERNS_BROAD,
    DECISION_PATTERNS_STRICT,
    AGENT_MONOLOGUE,
    NOISE_PATTERNS,
    LESSON_PATTERNS,
    is_noise_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(text: str) -> dict:
    """Create a user message dict."""
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    """Create an assistant message dict."""
    return {"role": "assistant", "content": text}


def _tool_use(name: str, summary: str = "", category: str = "") -> dict:
    """Create an assistant message with a tool_use block (DB-summarized format)."""
    block = {"type": "tool_use", "name": name, "summary": summary, "category": category}
    return {"role": "assistant", "content": [block]}


def _tool_use_raw(name: str, input_dict: dict) -> dict:
    """Create an assistant message with a tool_use block (raw SDK format)."""
    block = {"type": "tool_use", "name": name, "input": input_dict}
    return {"role": "assistant", "content": [block]}


@pytest.fixture
def pipeline() -> SummarizationPipeline:
    return SummarizationPipeline()


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------

class TestExtractTopics:
    def test_basic_topic_extraction(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("Fix the streaming bug in the chat component"),
            _assistant("I'll look into the streaming code now."),
        ]
        topics = pipeline._extract_topics(msgs)
        assert len(topics) == 1
        assert "streaming bug" in topics[0].lower()

    def test_noise_filtered(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("ok"),
            _user("yes"),
            _user("thanks"),
            _user("Fix the actual bug in the system"),
        ]
        topics = pipeline._extract_topics(msgs)
        # Noise messages should be filtered, only the real one survives
        assert any("bug" in t.lower() for t in topics)
        assert not any(t.strip().lower() in ("ok", "yes", "thanks") for t in topics)

    def test_short_messages_merged(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("check the logs"),
            _assistant("Looking at logs."),
            _user("try again"),
            _assistant("Retrying."),
            _user("now fix it"),
            _assistant("Fixed."),
        ]
        topics = pipeline._extract_topics(msgs)
        # Short messages should be merged or individually included
        assert len(topics) >= 1

    def test_dedup(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("Fix the streaming bug in the chat component"),
            _assistant("Working on it."),
            _user("Fix the streaming bug in the chat component"),
        ]
        topics = pipeline._extract_topics(msgs)
        assert len(topics) == 1

    def test_very_short_skipped(self, pipeline: SummarizationPipeline):
        msgs = [_user("hi"), _user("yo")]
        topics = pipeline._extract_topics(msgs)
        assert len(topics) == 0

    def test_long_message_truncated(self, pipeline: SummarizationPipeline):
        msgs = [_user("A" * 200 + " some important context")]
        topics = pipeline._extract_topics(msgs)
        assert all(len(t) <= 125 for t in topics)  # 120 + "..."

    def test_content_list_format(self, pipeline: SummarizationPipeline):
        """Messages with content as list of blocks (API format)."""
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Investigate the memory leak in the worker"}],
            }
        ]
        topics = pipeline._extract_topics(msgs)
        assert len(topics) == 1
        assert "memory leak" in topics[0].lower()


# ---------------------------------------------------------------------------
# Decision extraction
# ---------------------------------------------------------------------------

class TestExtractDecisions:
    def test_basic_decision(self, pipeline: SummarizationPipeline):
        msgs = [
            _assistant("After analysis, I decided to use CDP instead of WebSocket for browser sessions."),
        ]
        decisions = pipeline._extract_decisions(msgs)
        assert len(decisions) >= 1
        assert any("CDP" in d for d in decisions)

    def test_confirmed_pattern(self, pipeline: SummarizationPipeline):
        msgs = [
            _assistant("We confirmed that the fix is working in production."),
        ]
        decisions = pipeline._extract_decisions(msgs)
        assert len(decisions) >= 1

    def test_agent_monologue_filtered(self, pipeline: SummarizationPipeline):
        msgs = [
            _assistant("Let me check if the confirmed fix works. I'll look at the logs now."),
        ]
        decisions = pipeline._extract_decisions(msgs)
        # "Let me..." should be filtered by AGENT_MONOLOGUE
        assert not any(d.startswith("Let me") for d in decisions)

    def test_user_messages_ignored(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("I decided to use React for the frontend"),
        ]
        decisions = pipeline._extract_decisions(msgs)
        assert len(decisions) == 0

    def test_per_message_cap(self, pipeline: SummarizationPipeline):
        # 5 decision sentences in one message — should be capped at 3
        sentences = ". ".join(
            [f"We decided to use approach {i} for component {i}" for i in range(5)]
        )
        msgs = [_assistant(sentences)]
        decisions = pipeline._extract_decisions(msgs)
        assert len(decisions) <= 3

    def test_dedup_decisions(self, pipeline: SummarizationPipeline):
        msgs = [
            _assistant("We decided to use CDP for connections."),
            _assistant("We decided to use CDP for connections."),
        ]
        decisions = pipeline._extract_decisions(msgs)
        assert len(decisions) == 1


# ---------------------------------------------------------------------------
# File extraction
# ---------------------------------------------------------------------------

class TestExtractFilesModified:
    def test_summarized_write_format(self, pipeline: SummarizationPipeline):
        msgs = [
            _tool_use("Write", summary="Writing to src/hooks/useChat.ts", category="write"),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert "src/hooks/useChat.ts" in files

    def test_summarized_bash_with_redirect(self, pipeline: SummarizationPipeline):
        msgs = [
            _tool_use("Bash", summary="Running: echo test > /tmp/out.txt", category="bash"),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert any("/tmp/out.txt" in f for f in files)

    def test_raw_write_format(self, pipeline: SummarizationPipeline):
        msgs = [
            _tool_use_raw("Write", {"file_path": "/home/user/src/main.py"}),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert "/home/user/src/main.py" in files

    def test_read_excluded(self, pipeline: SummarizationPipeline):
        msgs = [
            _tool_use_raw("Read", {"file_path": "/home/user/src/config.py"}),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert len(files) == 0

    def test_dedup_files(self, pipeline: SummarizationPipeline):
        msgs = [
            _tool_use("Write", summary="Writing to src/app.ts", category="write"),
            _tool_use("Edit", summary="Writing to src/app.ts", category="write"),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert files.count("src/app.ts") == 1


# ---------------------------------------------------------------------------
# Session title
# ---------------------------------------------------------------------------

class TestDeriveTitle:
    def test_picks_longest_user_message(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("hi"),
            _user("Fix the streaming bug in the chat component urgently"),
            _user("ok done"),
        ]
        title = pipeline._derive_title(msgs)
        assert "streaming" in title.lower()

    def test_caps_at_60_chars(self, pipeline: SummarizationPipeline):
        msgs = [_user("A" * 100 + " trailing words")]
        title = pipeline._derive_title(msgs)
        assert len(title) <= 63  # 60 + potential "..."

    def test_no_user_messages(self, pipeline: SummarizationPipeline):
        msgs = [_assistant("Hello")]
        title = pipeline._derive_title(msgs)
        assert title == "Untitled session"


# ---------------------------------------------------------------------------
# Continue-from fallback
# ---------------------------------------------------------------------------

class TestDeriveContinueFrom:
    def test_open_questions_first(self):
        summary = StructuredSummary(
            open_questions=["What about the edge case?"],
            coe_signal="candidate",
            coe_topic="streaming bug",
            session_title="Fix streaming",
        )
        result = SummarizationPipeline._derive_continue_from(summary)
        assert result.startswith("Resolve:")

    def test_untested_validation(self):
        summary = StructuredSummary(
            validation_status="Code changed but untested in production",
            session_title="Fix streaming",
        )
        result = SummarizationPipeline._derive_continue_from(summary)
        assert result.startswith("Verify:")

    def test_coe_candidate(self):
        summary = StructuredSummary(
            coe_signal="candidate",
            coe_topic="tab switch streaming loss",
        )
        result = SummarizationPipeline._derive_continue_from(summary)
        assert result.startswith("Investigate:")

    def test_fallback_to_title(self):
        summary = StructuredSummary(
            session_title="Refactor agent manager",
        )
        result = SummarizationPipeline._derive_continue_from(summary)
        assert result.startswith("Ongoing:")

    def test_empty_when_nothing(self):
        summary = StructuredSummary(session_title="Untitled session")
        result = SummarizationPipeline._derive_continue_from(summary)
        assert result == ""


# ---------------------------------------------------------------------------
# Word limit enforcement
# ---------------------------------------------------------------------------

class TestWordLimit:
    def test_trims_lowest_value_first(self, pipeline: SummarizationPipeline):
        summary = StructuredSummary(
            topics=["word " * 200],  # ~200 words in topics alone
            decisions=["important decision"],
            lessons=["critical lesson"],
        )
        trimmed = pipeline._enforce_word_limit(summary)
        # Topics (lowest value) should be trimmed before lessons
        assert trimmed.word_count() <= pipeline.MAX_WORDS_PER_ENTRY
        # Lessons should survive
        assert len(trimmed.lessons) >= 1 or len(trimmed.decisions) >= 1


# ---------------------------------------------------------------------------
# Minimal summary
# ---------------------------------------------------------------------------

class TestMinimalSummary:
    def test_short_conversation(self, pipeline: SummarizationPipeline):
        msgs = [
            _user("What time is it?"),
            _assistant("It's 3pm."),
        ]
        summary = pipeline.minimal_summary(msgs)
        assert isinstance(summary, StructuredSummary)
        assert summary.timestamp  # should have timestamp
        assert summary.session_title  # should have title
        assert summary.decisions == []
        assert summary.files_modified == []


# ---------------------------------------------------------------------------
# StructuredSummary
# ---------------------------------------------------------------------------

class TestStructuredSummary:
    def test_word_count(self):
        summary = StructuredSummary(
            topics=["one two three"],
            decisions=["four five"],
            continue_from="six seven eight",
        )
        assert summary.word_count() == 8

    def test_empty_word_count(self):
        summary = StructuredSummary()
        assert summary.word_count() == 0


# ---------------------------------------------------------------------------
# Shared extraction patterns (extraction_patterns.py)
# ---------------------------------------------------------------------------

class TestDecisionPatterns:
    """Test both STRICT and BROAD decision patterns."""

    @pytest.mark.parametrize("text", [
        "decided to use CDP",
        "chose to refactor the module",
        "going with React for the frontend",
        "switched to a new approach",
        "opted for the simpler design",
        "selected React as the framework",
    ])
    def test_strict_matches(self, text: str):
        assert DECISION_PATTERNS_STRICT.search(text)

    @pytest.mark.parametrize("text", [
        "confirmed that the fix works",
        "approved the design",
        "rejected the proposal",
        "using CDP instead of WebSocket",
    ])
    def test_broad_only_matches(self, text: str):
        """These match BROAD but not STRICT."""
        assert DECISION_PATTERNS_BROAD.search(text)

    @pytest.mark.parametrize("text", [
        "the weather is nice today",
        "reading the file now",
        "I can see the output",
    ])
    def test_non_decisions(self, text: str):
        assert not DECISION_PATTERNS_STRICT.search(text)
        assert not DECISION_PATTERNS_BROAD.search(text)


class TestAgentMonologue:
    @pytest.mark.parametrize("text", [
        "Let me check the logs",
        "I'll investigate this",
        "I need to read the file first",
        "Checking the configuration",
        "Looking at the error output",
        "Now let me verify",
        "Confirmed — state is garbage",
        "Verified — working now",
        "Item 5 confirmed: no file",
        "Found 3 matching files",
    ])
    def test_detected(self, text: str):
        assert AGENT_MONOLOGUE.match(text), f"Should detect: {text}"

    @pytest.mark.parametrize("text", [
        "We decided to use CDP",
        "The root cause was a race condition",
        "enableMCP = always true in frontend",
        "Sandbox network fix needed",
    ])
    def test_not_detected(self, text: str):
        assert not AGENT_MONOLOGUE.match(text), f"Should NOT detect: {text}"


class TestNoiseEntry:
    """Test the composite is_noise_entry() function."""

    @pytest.mark.parametrize("text", [
        "| col1 | col2 | col3 |",
        "Let me read the files",
        "I'll check the logs now",
        "Confirmed — state is garbage",
        "Verified — working now",
        "Item 5 confirmed: no file",
        "Found 3 matching files",
    ])
    def test_is_noise(self, text: str):
        assert is_noise_entry(text), f"Should be noise: {text}"

    @pytest.mark.parametrize("text", [
        "Decided to use CDP instead of WebSocket",
        "enableMCP = always true in frontend",
        "Sandbox network fix — added configurable hosts",
        "Root cause was a race condition in tab switching",
        "The approach is to batch writes per section",
    ])
    def test_is_not_noise(self, text: str):
        assert not is_noise_entry(text), f"Should NOT be noise: {text}"


class TestLessonPatterns:
    @pytest.mark.parametrize("text", [
        "lesson learned: always check the lock file",
        "learned that macOS GUI apps don't source .zshrc",
        "root cause was a missing PATH entry",
        "the bug was a race condition in tab switching",
        "should have tested the edge case first",
        "next time check the lock before committing",
        "fixed by adding a retry with backoff",
    ])
    def test_matches(self, text: str):
        assert LESSON_PATTERNS.search(text), f"Should match: {text}"

    @pytest.mark.parametrize("text", [
        "the weather is nice",
        "decided to use CDP",
        "I checked the configuration",
    ])
    def test_no_match(self, text: str):
        assert not LESSON_PATTERNS.search(text), f"Should NOT match: {text}"


# ---------------------------------------------------------------------------
# Full pipeline (rule-based only, no LLM)
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_substantial_conversation(self, pipeline: SummarizationPipeline):
        """A realistic multi-turn conversation produces all expected fields."""
        msgs = [
            _user("Fix the tab switching bug that loses streaming content"),
            _assistant("I'll investigate the streaming state management."),
            _user("Check useChatStreamingLifecycle.ts"),
            _assistant("Found the issue. The streaming state is not saved when switching tabs. "
                       "I decided to use a ref-based approach to persist content."),
            _tool_use("Write", summary="Writing to src/hooks/useChatStreamingLifecycle.ts", category="write"),
            _user("Does it handle concurrent streams?"),
            _assistant("Good question. The current approach is to isolate state per tab using a Map keyed by session ID. "
                       "I confirmed that this handles concurrent streams correctly."),
            _user("Great, what about the edge case when a stream finishes during tab switch?"),
            _assistant("I added a guard for that. Going with a three-layer approach: "
                       "1) save on unmount, 2) restore on mount, 3) reconcile on stream end."),
        ]
        summary = await pipeline.summarize(msgs)

        assert isinstance(summary, StructuredSummary)
        assert len(summary.topics) >= 1
        assert len(summary.decisions) >= 1
        assert len(summary.files_modified) >= 1
        assert summary.session_title
        assert summary.timestamp
        assert summary.continue_from  # should have a continue_from (rule-based fallback)

    @pytest.mark.asyncio
    async def test_minimal_conversation(self, pipeline: SummarizationPipeline):
        """Short conversations (< 3 msgs) get minimal summary."""
        msgs = [
            _user("What's the status?"),
            _assistant("Everything is running."),
        ]
        summary = pipeline.minimal_summary(msgs)
        assert isinstance(summary, StructuredSummary)
        assert summary.decisions == []
        assert summary.files_modified == []
