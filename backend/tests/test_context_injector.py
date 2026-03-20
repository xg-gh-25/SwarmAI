"""Tests for context_injector — tool summarization, message formatting, token budget.

Covers:
- _compact_tool_args: truncation, key selection
- _summarize_tool_blocks: tool_use extraction
- _format_message: text-only, tool-only, mixed, empty
- _filter_tool_only_messages: filtering logic
- _apply_token_budget: O(n) truncation, edge cases
- _assemble_context: header, preamble, truncation note
"""

from core.context_injector import (
    _compact_tool_args,
    _compute_resume_budget,
    _summarize_tool_blocks,
    _format_message,
    _filter_tool_only_messages,
    _apply_token_budget,
    _assemble_context,
)


# ── _compute_resume_budget ────────────────────────────────────────


class TestComputeResumeBudget:
    def test_1m_model_gets_full_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(1_000_000)
        assert budget == 200_000
        assert max_msgs == 500
        assert fetch == 1000

    def test_500k_model_gets_full_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(500_000)
        assert budget == 200_000

    def test_200k_model_gets_medium_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(200_000)
        assert budget == 40_000
        assert max_msgs == 100
        assert fetch == 250

    def test_128k_model_gets_small_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(128_000)
        assert budget == 12_000
        assert max_msgs == 40
        assert fetch == 100

    def test_small_model_conservative(self):
        budget, max_msgs, fetch = _compute_resume_budget(32_000)
        assert budget == 12_000


# ── _compact_tool_args ─────────────────────────────────────────────


class TestCompactToolArgs:
    def test_file_path(self):
        result = _compact_tool_args({"file_path": "agent_manager.py"})
        assert result == "file_path=agent_manager.py"

    def test_truncates_long_values(self):
        long_val = "x" * 200
        result = _compact_tool_args({"command": long_val})
        assert len(result) < 100
        assert result.endswith("...")

    def test_max_two_keys(self):
        result = _compact_tool_args({
            "file_path": "a.py",
            "command": "git status",
            "pattern": "foo",
        })
        # Should contain at most 2 key=value pairs
        assert result.count("=") <= 2

    def test_empty_dict(self):
        assert _compact_tool_args({}) == ""

    def test_irrelevant_keys_ignored(self):
        assert _compact_tool_args({"timeout": 30, "verbose": True}) == ""


# ── _summarize_tool_blocks ─────────────────────────────────────────


class TestSummarizeToolBlocks:
    def test_single_tool_use(self):
        content = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "main.py"}},
        ]
        result = _summarize_tool_blocks(content)
        assert len(result) == 1
        assert "Read" in result[0]
        assert "main.py" in result[0]

    def test_multiple_tool_uses(self):
        content = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "b.py"}},
        ]
        result = _summarize_tool_blocks(content)
        assert len(result) == 2

    def test_no_tool_use_blocks(self):
        content = [
            {"type": "text", "text": "hello"},
        ]
        assert _summarize_tool_blocks(content) == []

    def test_tool_result_ignored(self):
        content = [
            {"type": "tool_result", "content": "ok"},
        ]
        assert _summarize_tool_blocks(content) == []

    def test_non_dict_blocks_skipped(self):
        content = ["not a dict", None, 42]
        assert _summarize_tool_blocks(content) == []

    def test_missing_input(self):
        content = [{"type": "tool_use", "name": "Bash"}]
        result = _summarize_tool_blocks(content)
        assert len(result) == 1
        assert "Bash" in result[0]


# ── _format_message ────────────────────────────────────────────────


class TestFormatMessage:
    def test_text_only_user(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        result = _format_message(msg)
        assert result == "User: hello"

    def test_text_only_assistant(self):
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}
        result = _format_message(msg)
        assert result == "Assistant: hi"

    def test_tool_only_message_shows_summary(self):
        """When message has NO text blocks, tool summaries should appear."""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}},
            ],
        }
        result = _format_message(msg)
        assert result is not None
        assert "[Tools used:]" in result
        assert "Read" in result

    def test_mixed_text_and_tool_no_summary(self):
        """When message has text blocks, tool summaries should NOT appear."""
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I read the file and found a bug"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}},
            ],
        }
        result = _format_message(msg)
        assert result is not None
        assert "[Tools used:]" not in result
        assert "I read the file" in result

    def test_image_placeholder(self):
        msg = {"role": "user", "content": [{"type": "image"}]}
        result = _format_message(msg)
        assert "[image attachment]" in result

    def test_document_placeholder(self):
        msg = {"role": "user", "content": [{"type": "document"}]}
        result = _format_message(msg)
        assert "[document attachment]" in result

    def test_empty_content_returns_none(self):
        msg = {"role": "user", "content": []}
        assert _format_message(msg) is None

    def test_non_list_content_returns_none(self):
        msg = {"role": "user", "content": "just a string"}
        assert _format_message(msg) is None

    def test_only_tool_result_blocks_returns_none(self):
        """tool_result blocks are not summarized — message should be None."""
        msg = {
            "role": "assistant",
            "content": [{"type": "tool_result", "content": "ok"}],
        }
        assert _format_message(msg) is None

    def test_empty_text_blocks_skipped(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": ""},
                {"type": "text", "text": "real content"},
            ],
        }
        result = _format_message(msg)
        assert result == "User: real content"


# ── _filter_tool_only_messages ─────────────────────────────────────


class TestFilterToolOnlyMessages:
    def test_keeps_text_messages(self):
        messages = [
            {"content": [{"type": "text", "text": "hello"}]},
        ]
        assert len(_filter_tool_only_messages(messages)) == 1

    def test_removes_tool_only(self):
        messages = [
            {"content": [{"type": "tool_use", "name": "Read", "input": {}}]},
        ]
        assert len(_filter_tool_only_messages(messages)) == 0

    def test_keeps_mixed(self):
        messages = [
            {"content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "name": "Read", "input": {}},
            ]},
        ]
        assert len(_filter_tool_only_messages(messages)) == 1

    def test_removes_empty_content(self):
        messages = [{"content": []}]
        assert len(_filter_tool_only_messages(messages)) == 0

    def test_removes_non_list_content(self):
        messages = [{"content": "string"}]
        assert len(_filter_tool_only_messages(messages)) == 0


# ── _apply_token_budget ────────────────────────────────────────────


class TestApplyTokenBudget:
    def test_within_budget_no_truncation(self):
        messages = ["short msg"]
        result, truncated = _apply_token_budget(messages, 10000)
        assert result == ["short msg"]
        assert truncated is False

    def test_over_budget_truncates_oldest(self):
        # Each message ~10 tokens. Budget allows ~2.
        messages = ["a " * 20, "b " * 20, "c " * 20]
        result, truncated = _apply_token_budget(messages, 30)
        assert truncated is True
        # Newest messages survive
        assert len(result) < len(messages)
        if result:
            assert result[-1] == messages[-1]

    def test_empty_input(self):
        result, truncated = _apply_token_budget([], 1000)
        assert result == []
        assert truncated is False

    def test_zero_budget_truncates_all(self):
        messages = ["hello world"]
        result, truncated = _apply_token_budget(messages, 0)
        assert result == []
        assert truncated is True

    def test_does_not_mutate_input(self):
        original = ["msg1", "msg2", "msg3"]
        copy = list(original)
        _apply_token_budget(original, 1)
        # Original list passed in should not be mutated
        assert original == copy


# ── _assemble_context ──────────────────────────────────────────────


class TestAssembleContext:
    def test_empty_messages_returns_empty(self):
        assert _assemble_context([], False) == ""

    def test_includes_header_and_preamble(self):
        result = _assemble_context(["User: hello"], False)
        assert "## Previous Conversation Context" in result
        assert "READ-ONLY history" in result
        assert "User: hello" in result

    def test_truncation_note_when_truncated(self):
        result = _assemble_context(["User: hello"], True)
        assert "truncated" in result.lower()

    def test_no_truncation_note_when_not_truncated(self):
        result = _assemble_context(["User: hello"], False)
        assert "truncated to fit" not in result
