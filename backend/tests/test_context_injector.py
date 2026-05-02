"""Tests for context_injector — tool summarization, message formatting, token budget.

Covers:
- _compact_tool_args: truncation, key selection
- _summarize_tool_blocks: tool_use extraction
- _format_message: text-only, tool-only, mixed, empty
- _filter_tool_only_messages: filtering logic
- _apply_token_budget: O(n) truncation, edge cases
- _assemble_context: header, preamble, truncation note
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock

from core.context_injector import (
    _compact_tool_args,
    _compute_resume_budget,
    _summarize_tool_blocks,
    _format_message,
    _filter_tool_only_messages,
    _apply_token_budget,
    _assemble_context,
    _find_last_user_text,
    _extract_assistant_conclusions,
    _extract_user_directives,
    _extract_uncommitted_state,
    _merge_crash_checkpoint,
    _extract_key_tool_results,
    _build_checkpoint,
    _format_recent_turns,
    _trim_to_budget,
)


# ── _compute_resume_budget ────────────────────────────────────────


class TestComputeResumeBudget:
    def test_1m_model_gets_generous_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(1_000_000)
        assert budget == 150_000
        assert max_msgs == 500
        assert fetch == 1000

    def test_500k_model_gets_generous_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(500_000)
        assert budget == 150_000

    def test_200k_model_gets_medium_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(200_000)
        assert budget == 60_000
        assert max_msgs == 200
        assert fetch == 500

    def test_128k_model_gets_small_budget(self):
        budget, max_msgs, fetch = _compute_resume_budget(128_000)
        assert budget == 20_000
        assert max_msgs == 80
        assert fetch == 200

    def test_small_model_conservative(self):
        budget, max_msgs, fetch = _compute_resume_budget(32_000)
        assert budget == 20_000

    def test_channel_gets_fixed_budget_regardless_of_model(self):
        """Channel sessions use a fixed 32K/50 budget even on 1M models."""
        budget, max_msgs, fetch = _compute_resume_budget(1_000_000, is_channel=True)
        assert budget == 32_000
        assert max_msgs == 50
        assert fetch == 120

    def test_channel_overrides_all_tiers(self):
        """Channel budget is the same for all model sizes."""
        for window in (32_000, 200_000, 500_000, 1_000_000):
            budget, max_msgs, fetch = _compute_resume_budget(window, is_channel=True)
            assert budget == 32_000
            assert max_msgs == 50


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


# ── _find_last_user_text (expanded cap) ──────────────────────────────


class TestFindLastUserText:
    def test_returns_last_user_message(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "first"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "response"}]},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ]
        assert _find_last_user_text(msgs) == "second"

    def test_caps_at_4000_chars(self):
        long_text = "x" * 5000
        msgs = [{"role": "user", "content": [{"type": "text", "text": long_text}]}]
        result = _find_last_user_text(msgs)
        assert len(result) == 4000

    def test_returns_empty_on_no_user_messages(self):
        msgs = [{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
        assert _find_last_user_text(msgs) == ""


# ── _extract_assistant_conclusions ───────────────────────────────────


class TestExtractAssistantConclusions:
    def test_extracts_last_n_assistant_texts(self):
        msgs = []
        for i in range(8):
            msgs.append({"role": "user", "content": [{"type": "text", "text": f"q{i}"}]})
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"analysis {i}"}]})
        result = _extract_assistant_conclusions(msgs, max_blocks=5)
        assert len(result) == 5
        assert "analysis 7" in result[-1]

    def test_skips_tool_only_assistant_messages(self):
        msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": "real conclusion"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {}}]},
        ]
        result = _extract_assistant_conclusions(msgs, max_blocks=5)
        assert len(result) == 1
        assert "real conclusion" in result[0]

    def test_caps_each_at_1500_chars(self):
        long_text = "y" * 3000
        msgs = [{"role": "assistant", "content": [{"type": "text", "text": long_text}]}]
        result = _extract_assistant_conclusions(msgs)
        assert len(result[0]) == 1500

    def test_empty_messages(self):
        assert _extract_assistant_conclusions([]) == []


# ── _extract_user_directives ─────────────────────────────────────────


class TestExtractUserDirectives:
    def test_extracts_short_directive_messages(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "approve this approach"}]},
            {"role": "user", "content": [{"type": "text", "text": "A very long question about architecture that goes on and on " * 10}]},
            {"role": "user", "content": [{"type": "text", "text": "commit these changes"}]},
        ]
        result = _extract_user_directives(msgs)
        assert any("approve" in d for d in result)
        assert any("commit" in d for d in result)

    def test_skips_questions(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "what is this?"}]},
        ]
        result = _extract_user_directives(msgs)
        assert len(result) == 0

    def test_caps_at_max_directives(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": f"do thing {i}"}]}
            for i in range(20)
        ]
        result = _extract_user_directives(msgs, max_directives=10)
        assert len(result) <= 10


# ── _extract_uncommitted_state ───────────────────────────────────────


class TestExtractUncommittedState:
    def test_returns_git_status_output(self):
        mock_result = " M file1.py\n?? file2.py\n"
        with patch("core.context_injector._run_git_command", return_value=mock_result):
            result = asyncio.get_event_loop().run_until_complete(
                _extract_uncommitted_state("/some/dir")
            )
        assert "file1.py" in result
        assert "file2.py" in result

    def test_returns_empty_on_timeout(self):
        with patch("core.context_injector._run_git_command", return_value=""):
            result = asyncio.get_event_loop().run_until_complete(
                _extract_uncommitted_state("/some/dir")
            )
        assert result == ""

    def test_returns_empty_on_exception(self):
        with patch("core.context_injector._run_git_command", side_effect=Exception("boom")):
            result = asyncio.get_event_loop().run_until_complete(
                _extract_uncommitted_state("/some/dir")
            )
        assert result == ""


# ── _merge_crash_checkpoint ──────────────────────────────────────────


class TestMergeCrashCheckpoint:
    def test_returns_none_when_no_checkpoint(self, tmp_path):
        result = _merge_crash_checkpoint(tmp_path / "nonexistent.json")
        assert result is None

    def test_reads_checkpoint_data(self, tmp_path):
        cp = tmp_path / "session_checkpoint.json"
        cp.write_text(json.dumps({
            "session_id": "abc12345",
            "ts": time.time(),
            "tool_count": 42,
            "files_touched": ["hooks.py", "main.py"],
            "corrections_count": 3,
        }))
        result = _merge_crash_checkpoint(cp)
        assert result is not None
        assert "42 tool calls" in result
        assert "hooks.py" in result
        assert "3 correction" in result

    def test_handles_corrupt_json(self, tmp_path):
        cp = tmp_path / "session_checkpoint.json"
        cp.write_text("{corrupt json!!!")
        result = _merge_crash_checkpoint(cp)
        assert result is None

    def test_does_not_delete_file(self, tmp_path):
        cp = tmp_path / "session_checkpoint.json"
        cp.write_text(json.dumps({"session_id": "x", "ts": 1, "tool_count": 1}))
        _merge_crash_checkpoint(cp)
        assert cp.exists()  # Must not delete — that's recover_crash_checkpoint's job


# ── _extract_key_tool_results ────────────────────────────────────────


class TestExtractKeyToolResults:
    def test_extracts_read_results(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x.py"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "def hello():\n    return 42"},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert "hello" in result
        assert "42" in result

    def test_skips_trivial_results(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Edit", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert result == ""

    def test_caps_each_result(self):
        long_output = "line\n" * 1000
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": long_output},
            ]},
        ]
        result = _extract_key_tool_results(msgs, max_results=1)
        assert len(result) <= 2000  # 1500 chars + header overhead

    def test_respects_max_results(self):
        msgs = []
        for i in range(20):
            msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Grep", "input": {"pattern": f"pat{i}"}},
            ]})
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": f"result line {i}\nmore data"},
            ]})
        result = _extract_key_tool_results(msgs, max_results=5)
        # Should have at most 5 result blocks
        assert result.count("→") <= 5


# ── _format_recent_turns (expanded) ──────────────────────────────────


class TestFormatRecentTurnsExpanded:
    def test_30_turns_max(self):
        msgs = []
        for i in range(50):
            msgs.append({"role": "user", "content": [{"type": "text", "text": f"question {i}"}]})
            msgs.append({"role": "assistant", "content": [{"type": "text", "text": f"answer {i}"}]})
        result = _format_recent_turns(msgs, max_turns=30)
        assert "question 49" in result  # Most recent
        # Should not include very old turns
        assert "question 0" not in result

    def test_4k_char_cap_per_message(self):
        long_answer = "z" * 6000
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "ask"}]},
            {"role": "assistant", "content": [{"type": "text", "text": long_answer}]},
            {"role": "user", "content": [{"type": "text", "text": "follow up"}]},
        ]
        result = _format_recent_turns(msgs)
        # The long answer should be truncated
        assert "z" * 4000 not in result
        assert "..." in result


# ── _trim_to_budget ──────────────────────────────────────────────────


class TestTrimToBudget:
    def test_no_trimming_when_under_budget(self):
        sections = {
            "checkpoint": "short checkpoint",
            "conclusions": "short conclusions",
            "tool_results": "short results",
            "recent_turns": "short turns",
        }
        result = _trim_to_budget(sections, token_budget=100_000)
        assert result["tool_results"] == "short results"
        assert result["recent_turns"] == "short turns"

    def test_trims_tool_results_first(self):
        sections = {
            "checkpoint": "important " * 100,
            "conclusions": "critical " * 100,
            "tool_results": "expendable " * 5000,  # ~20K tokens
            "recent_turns": "conversation " * 5000,  # ~20K tokens
        }
        result = _trim_to_budget(sections, token_budget=5_000)
        # Tool results trimmed first (lowest priority)
        assert len(result.get("tool_results", "")) < len(sections["tool_results"])
        # Checkpoint never trimmed
        assert result["checkpoint"] == sections["checkpoint"]
