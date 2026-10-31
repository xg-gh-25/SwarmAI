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

The model call (_call_enrichment_llm) is replaced in every test that reaches it.
"""

from __future__ import annotations

import pytest

from core.summarization import SummarizationPipeline, StructuredSummary
from core.extraction_patterns import (
    DECISION_PATTERNS_BROAD,
    DECISION_PATTERNS_STRICT,
    AGENT_MONOLOGUE,
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


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """No test here reaches Bedrock; a test that needs an answer sets its own."""
    monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                        staticmethod(lambda prompt: {}))


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

    def test_long_message_is_complete(self, pipeline: SummarizationPipeline):
        text = "A" * 200 + " some important context"
        topics = pipeline._extract_topics([_user(text)])
        assert topics == [text]

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

    def test_all_decisions_are_preserved(self, pipeline: SummarizationPipeline):
        sentences = ". ".join(
            [f"We decided to use approach {i} for component {i}" for i in range(5)]
        )
        decisions = pipeline._extract_decisions([_assistant(sentences)])
        assert len(decisions) == 5

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

    def test_keeps_complete_title(self, pipeline: SummarizationPipeline):
        text = "A" * 100 + " trailing words"
        assert pipeline._derive_title([_user(text)]) == text

    def test_no_user_messages(self, pipeline: SummarizationPipeline):
        msgs = [_assistant("Hello")]
        title = pipeline._derive_title(msgs)
        assert title == "Untitled session"


# ---------------------------------------------------------------------------
# The model writes the record (design 2026-10-31-daily-activity-useful D2-D6)
# ---------------------------------------------------------------------------

class TestModelWrittenRecord:
    @pytest.mark.asyncio
    async def test_every_exchange_is_sent_and_one_message_is_not(self, monkeypatch, pipeline):
        prompts: list[str] = []
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: prompts.append(prompt) or {}))
        await pipeline.summarize([_user("What is the status of the build?")])
        assert prompts == []
        await pipeline.summarize([_user("What is the status of the build?"),
                                  _assistant("It is green.")])
        assert len(prompts) == 1

    @pytest.mark.asyncio
    async def test_enrich_false_never_calls_the_model(self, monkeypatch, pipeline):
        called: list[str] = []
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: called.append(prompt) or {}))
        msgs = [_user("Fix the bug in the parser now"), _assistant("Done."),
                _user("And the tests?"), _assistant("Green.")]
        summary = await pipeline.summarize(msgs, enrich=False)
        assert called == [] and summary.enriched is False

    @pytest.mark.asyncio
    async def test_the_answer_fills_the_record(self, monkeypatch, pipeline):
        answer = {"title": "解析器修复", "summary_sentence": "修好了解析器的空行崩溃，测试全绿。",
                  "deliverables": ["commit abc: parser skips blank lines"], "key_outputs": ["core/parser.py"],
                  "validation_status": "unit tests green", "next_items": ["发布 2.4"],
                  "resolves": ["aaaaaaaaaaaa", "not-offered"], "coe_signal": "bogus", "coe_topic": ""}
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: answer))
        msgs = [_user("修一下解析器空行崩溃的问题"), _assistant("好的，已修复。")]
        summary = await pipeline.summarize(
            msgs, session_id="s1", open_next_items=[{"next_id": "aaaaaaaaaaaa", "text": "x"}])
        assert summary.enriched is True
        assert summary.session_title == "解析器修复"
        assert summary.summary_sentence == "修好了解析器的空行崩溃，测试全绿。"
        assert summary.deliverables == ["commit abc: parser skips blank lines"]
        assert [i["text"] for i in summary.next_items] == ["发布 2.4"]
        assert summary.resolves == ["aaaaaaaaaaaa"]   # only ids that were offered
        assert summary.coe_signal == ""                # unknown value reset

    @pytest.mark.asyncio
    async def test_without_an_answer_nothing_the_user_said_becomes_the_record(self, monkeypatch, pipeline):
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: {}))
        msgs = [_user("Does it handle concurrent streams? What about the edge case?"),
                _assistant("Yes.")]
        summary = await pipeline.summarize(msgs, session_id="s1")
        assert summary.enriched is False
        assert summary.summary_sentence == ""
        assert summary.next_items == [] and summary.deliverables == []

    @pytest.mark.asyncio
    async def test_the_prompt_carries_the_earlier_day_and_asks_for_the_outcome(self, monkeypatch, pipeline):
        prompts: list[str] = []
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: prompts.append(prompt) or {}))
        await pipeline.summarize(
            [_user("continue the migration please"), _assistant("Migrated the rest.")],
            earlier={"day": "2026-10-30", "title": "DB migration", "sentence": "Half the tables moved."},
            day="2026-10-31")
        prompt = prompts[0]
        assert "Already recorded on 2026-10-30: DB migration: Half the tables moved." in prompt
        assert "## Conversation on 2026-10-31:" in prompt
        assert "where it ended" in prompt and "at most three" in prompt

    @pytest.mark.asyncio
    async def test_a_long_day_is_summarized_in_parts_each_message_whole(self, monkeypatch, pipeline):
        import core.summarization as sm
        monkeypatch.setattr(sm, "_CHUNK_TOKENS", 60)
        prompts: list[str] = []
        answers = iter([{**{k: "" for k in ("title", "summary_sentence", "validation_status",
                                            "coe_signal", "coe_topic")},
                         "deliverables": [f"part {i}"], "key_outputs": [], "next_items": [],
                         "resolves": []} for i in (1, 2, 3, 4, 5)])
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: prompts.append(prompt) or next(answers)))
        long_text = "x" * 400                       # one message larger than a part
        msgs = [_user("first question about the parser"), _assistant(long_text),
                _user("second question about the docs"), _assistant("answered")]
        summary = await pipeline.summarize(msgs)
        assert len(prompts) >= 3
        assert "(part 1 of" in prompts[0] and "record so far" not in prompts[0]
        assert '"part 1"' in prompts[1]               # the next part carries the record so far
        assert any(long_text in p for p in prompts)   # the long message reached the model whole
        assert summary.deliverables == [f"part {len(prompts)}"] and summary.enriched

    @pytest.mark.asyncio
    async def test_a_failed_part_keeps_the_fallback(self, monkeypatch, pipeline):
        import core.summarization as sm
        monkeypatch.setattr(sm, "_CHUNK_TOKENS", 20)
        replies = iter([{"title": "t", "summary_sentence": "s", "deliverables": [], "key_outputs": [],
                         "validation_status": "", "next_items": [], "resolves": [],
                         "coe_signal": "", "coe_topic": ""}, {}])
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: next(replies)))
        msgs = [_user("a" * 80), _assistant("b" * 80)]
        summary = await pipeline.summarize(msgs)
        assert summary.enriched is False and summary.summary_sentence == ""

    @pytest.mark.asyncio
    async def test_a_continued_record_keeps_what_it_resolved_and_answers_are_one_line(
            self, monkeypatch, pipeline):
        answer = {"title": "Parser\nfix", "summary_sentence": "a\nb", "deliverables": ["x\ny"],
                  "key_outputs": [], "validation_status": "", "next_items": ["n\nm"],
                  "resolves": ["old000000001"], "coe_signal": "", "coe_topic": ""}
        prompts: list[str] = []
        monkeypatch.setattr(SummarizationPipeline, "_call_enrichment_llm",
                            staticmethod(lambda prompt: prompts.append(prompt) or answer))
        so_far = {"title": "Parser", "summary_sentence": "s", "resolves": ["old000000001"]}
        summary = await pipeline.summarize([_user("more on the parser please")], session_id="s",
                                           open_next_items=[], so_far=so_far)
        assert summary.resolves == ["old000000001"]   # no longer open, still resolved
        assert summary.session_title == "Parser fix" and summary.deliverables == ["x y"]
        assert [i["text"] for i in summary.next_items] == ["n m"]
        assert "record so far" in prompts[0]           # one message is enough when continuing

    def test_the_schema_asks_only_for_fields_with_a_reader(self):
        from core.summarization import _ENRICHMENT_SCHEMA
        assert set(_ENRICHMENT_SCHEMA["properties"]) == {
            "title", "summary_sentence", "deliverables", "key_outputs", "validation_status",
            "next_items", "resolves", "coe_signal", "coe_topic"}
        assert set(_ENRICHMENT_SCHEMA["required"]) == set(_ENRICHMENT_SCHEMA["properties"])

    def test_the_timestamp_is_the_last_message(self, pipeline):
        msgs = [{**_user("hello there friend"), "created_at": "2026-10-30T23:50:01"},
                {**_assistant("hi"), "created_at": "2026-10-30T23:58:59"}]
        assert pipeline._last_time(msgs) == "23:58"


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
        summary = await pipeline.summarize(msgs, enrich=False)

        assert isinstance(summary, StructuredSummary)
        assert len(summary.topics) >= 1
        assert len(summary.decisions) >= 1
        assert len(summary.files_modified) >= 1
        assert summary.session_title
        assert summary.timestamp
        assert summary.summary_sentence == ""  # no model answer: no user words as the record

    @pytest.mark.asyncio
    async def test_minimal_conversation(self, pipeline: SummarizationPipeline):
        """A short conversation keeps empty rule-based fields."""
        msgs = [
            _user("What's the status?"),
            _assistant("Everything is running."),
        ]
        summary = await pipeline.summarize(msgs, enrich=False)
        assert isinstance(summary, StructuredSummary)
        assert summary.decisions == []
        assert summary.files_modified == []


class TestFilesModifiedAreRealPaths:
    """run_1ec5ea9d AC4: names come from the SSOT bash write-target parser."""

    def test_dev_null_sed_and_dirs_never_become_files(self, pipeline):
        msgs = [
            _tool_use("Bash", "Running: grep -c x a.py 2>/dev/null > /tmp/x/out.txt", "bash"),
            _tool_use("Bash", "Running: sed -n 's/a/b/' f.py > /tmp/y.md", "bash"),
            _tool_use("Bash", "Running: cp a.py /tmp/", "bash"),
        ]
        files = pipeline._extract_files_modified(msgs)
        assert "/tmp/x/out.txt" in files and "/tmp/y.md" in files
        assert not any(f.endswith("null") or f.endswith("/") for f in files)

    def test_target_at_a_cut_summary_is_dropped(self, pipeline):
        from core.tool_summarizer import MAX_SUMMARY_LENGTH
        cmd = "echo " + "x" * (MAX_SUMMARY_LENGTH - 40) + " > /tmp/run_abc/evidence/pro"
        summary = ("Running: " + cmd)[:MAX_SUMMARY_LENGTH]
        assert pipeline._extract_files_modified([_tool_use("Bash", summary, "bash")]) == []
