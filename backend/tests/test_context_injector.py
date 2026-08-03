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
from unittest.mock import patch, AsyncMock

from core.context_injector import (
    _compact_tool_args,
    _compute_resume_budget,
    _compute_layer_caps,
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


# ── _compute_layer_caps (elastic budget-driven caps) ──────────────────


class TestComputeLayerCaps:
    """Per-layer CHAR budgets derived from token_budget (run_6d5f60dd).

    The fix for resume under-fill: a generous 150K budget filled only ~4%
    because extraction used FIXED item-counts. These caps make each layer's
    size a pure function of the budget — complex sessions fill more, simple
    stay lean, channel stays small.
    """

    def test_returns_all_layer_keys(self):
        caps = _compute_layer_caps(150_000)
        for key in ("recent_chars", "tool_results_chars",
                    "conclusions_chars", "directives_chars", "tool_item_trunc"):
            assert key in caps, f"missing layer cap: {key}"

    def test_monotonic_in_budget(self):
        """AC3: every layer cap must grow with the budget (1M > 200K > 32K)."""
        big = _compute_layer_caps(150_000)      # 1M model budget
        mid = _compute_layer_caps(60_000)       # 200K model budget
        small = _compute_layer_caps(32_000)     # channel budget
        for key in ("recent_chars", "tool_results_chars",
                    "conclusions_chars", "directives_chars"):
            assert big[key] > mid[key] > small[key], (
                f"{key} not monotonic: {big[key]} / {mid[key]} / {small[key]}"
            )

    def test_shares_leave_checkpoint_headroom(self):
        """Trimmable layer shares must sum to < 100% of budget so the
        untrimmable checkpoint + uncommitted have room (Gate-1 finding 2)."""
        caps = _compute_layer_caps(150_000)
        budget_chars = 150_000 * 4
        trimmable = (caps["recent_chars"] + caps["tool_results_chars"]
                     + caps["conclusions_chars"] + caps["directives_chars"])
        assert trimmable < budget_chars, "no headroom for checkpoint"
        # headroom should be a meaningful fraction (≥10%), not a sliver
        assert (budget_chars - trimmable) >= 0.10 * budget_chars

    def test_tool_item_trunc_has_floor(self):
        """Per-item truncation floor = 300 (today's value) — never regress
        below it even on a tiny budget (Gate-1 finding 3: per-item floor)."""
        assert _compute_layer_caps(32_000)["tool_item_trunc"] >= 300
        assert _compute_layer_caps(150_000)["tool_item_trunc"] >= 300

    def test_tool_item_trunc_scales_up_with_budget(self):
        """A large budget allows keeping more than 300 chars per tool summary."""
        assert (_compute_layer_caps(150_000)["tool_item_trunc"]
                > _compute_layer_caps(32_000)["tool_item_trunc"])


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

    def test_char_budget_keeps_newest_directives(self):
        """Finding 1 (adversarial): when char_budget binds, keep the MOST
        RECENT directives, not the oldest — a later decision supersedes an
        earlier one. Mirrors _extract_assistant_conclusions' direction.
        (Would be RED with forward iteration: it kept 'approve step 0'.)"""
        msgs = [
            {"role": "user",
             "content": [{"type": "text", "text": f"approve step {i}"}]}
            for i in range(30)
        ]
        # ~14 chars each; budget 50 → ~3-4 directives collected.
        result = _extract_user_directives(msgs, char_budget=50)
        assert result, "expected some directives"
        assert any("step 29" in d for d in result), "newest directive dropped"
        assert not any("step 0" in d and "step 0" == d.replace("approve ", "")
                       for d in result), "oldest directive should be dropped"
        # chronological order preserved (ascending step numbers)
        steps = [int(d.rsplit(" ", 1)[1]) for d in result]
        assert steps == sorted(steps), f"not chronological: {steps}"


# ── _extract_uncommitted_state ───────────────────────────────────────


class TestExtractUncommittedState:
    def test_returns_git_status_output(self):
        mock_result = " M file1.py\n?? file2.py\n"
        with patch("core.context_injector._run_git_command", return_value=mock_result):
            result = asyncio.run(
                _extract_uncommitted_state("/some/dir")
            )
        assert "file1.py" in result
        assert "file2.py" in result

    def test_returns_empty_on_timeout(self):
        with patch("core.context_injector._run_git_command", return_value=""):
            result = asyncio.run(
                _extract_uncommitted_state("/some/dir")
            )
        assert result == ""

    def test_returns_empty_on_exception(self):
        with patch("core.context_injector._run_git_command", side_effect=Exception("boom")):
            result = asyncio.run(
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
    """Tests for _extract_key_tool_results — now reads tool_use.summary (DB path).

    The Claude Agent SDK does NOT persist tool_result blocks to DB.
    Only tool_use blocks with a 'summary' field survive. E2E audit
    confirmed: 0 tool_result rows, 15693 tool_use rows in production DB.
    """

    def test_extracts_from_summary_field(self):
        """DB path: summary contains human-readable action description."""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {},
                 "summary": "Reading $SWARMAI_ROOT/backend/core/context_injector.py"},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert "Read" in result
        assert "context_injector.py" in result

    def test_falls_back_to_input_when_no_summary(self):
        """Live path: input dict has data but no summary."""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "git status --short"}},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert "Bash" in result
        assert "git status" in result

    def test_skips_trivial_summaries(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Edit", "input": {},
                 "summary": "ok"},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert result == ""

    def test_skips_non_high_value_tools(self):
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Skill", "input": {},
                 "summary": "Running skill s_autonomous-pipeline with long description"},
            ]},
        ]
        result = _extract_key_tool_results(msgs)
        assert result == ""

    def test_respects_max_results(self):
        msgs = []
        for i in range(20):
            msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Grep", "input": {},
                 "summary": f"Searching for pattern_{i} in backend/core/"},
            ]})
        result = _extract_key_tool_results(msgs, max_results=5)
        assert result.count("→") <= 5

    def test_caps_long_summaries(self):
        long_summary = "Reading " + "x" * 500
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {},
                 "summary": long_summary},
            ]},
        ]
        result = _extract_key_tool_results(msgs, max_results=1)
        assert "..." in result
        assert len(result) < 500  # 300 char cap + header


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

    def test_trims_uncommitted_before_conclusions(self):
        """Gate-1 finding 2: uncommitted is in the trim view and goes
        before conclusions (more disposable), after recent_turns."""
        sections = {
            "checkpoint": "x " * 50,
            "conclusions": "keep " * 2000,       # 10000 chars
            "tool_results": "drop " * 2000,      # 10000 chars
            "recent_turns": "turns " * 2000,     # 12000 chars
            "uncommitted": "diff " * 2000,       # 10000 chars
        }
        # Budget tight enough that trimming tool_results + recent_turns is NOT
        # enough — the trimmer must reach into uncommitted (4th in order) while
        # leaving conclusions (5th, last) untouched.
        result = _trim_to_budget(sections, token_budget=4_000)  # 16000 chars
        # uncommitted shrinks (reached) before conclusions is spent
        assert len(result.get("uncommitted", "")) < len(sections["uncommitted"])
        # conclusions is the last trimmable to go — survives more than uncommitted
        assert len(result.get("conclusions", "")) >= len(result.get("uncommitted", ""))


# ── build_resume_context — END-TO-END elasticity (the seam) ──────────
#
# These are the load-bearing proof of run_6d5f60dd: that the elastic caps
# actually change build_resume_context's OUTPUT (PIT08 — the bug lives
# BETWEEN _compute_layer_caps and the extraction fns, not inside either).
# We drive the REAL function, mocking only the DB + git boundaries.


def _mk_session(n_turns: int, answer_chars: int = 2000) -> list[dict]:
    """Build a synthetic session of n_turns user/assistant pairs."""
    msgs: list[dict] = []
    for i in range(n_turns):
        msgs.append({
            "role": "user",
            "content": [{"type": "text", "text": f"please do task {i} now"}],
        })
        msgs.append({
            "role": "assistant",
            "content": [{"type": "text", "text": f"turn{i} " + ("z" * answer_chars)}],
        })
    return msgs


class _FakeMessages:
    def __init__(self, msgs):
        self._msgs = msgs

    async def count_by_session(self, _sid):
        return len(self._msgs)

    async def list_by_session_paginated(self, _sid, limit=None):
        return self._msgs[-limit:] if limit else self._msgs


class _FakeDB:
    def __init__(self, msgs):
        self.messages = _FakeMessages(msgs)


def _run_resume(msgs, **kwargs):
    """Invoke build_resume_context with DB + git mocked, fresh cache."""
    import core.context_injector as ci
    ci._resume_cache.clear()
    fake_db = _FakeDB(msgs)
    with patch.dict("sys.modules", {"database": type("M", (), {"db": fake_db})}), \
         patch.object(ci, "_extract_uncommitted_state",
                      new=AsyncMock(return_value="")):
        return asyncio.run(ci.build_resume_context("sess-x", **kwargs))


class TestBuildResumeContextElastic:
    """AC1/AC2/AC4: elastic caps change the assembled output end-to-end."""

    def test_large_session_fills_more_than_small_budget(self):
        """AC1: same big session → 1M-model budget yields MORE context than
        a 128K-model budget (the caps are budget-driven, not fixed counts)."""
        big_session = _mk_session(60, answer_chars=2000)
        out_1m = _run_resume(big_session, model_context_window=1_000_000)
        out_128k = _run_resume(big_session, model_context_window=128_000)
        assert len(out_1m) > len(out_128k), (
            f"1M budget ({len(out_1m)}) should exceed 128K ({len(out_128k)})"
        )

    def test_short_session_stays_lean(self):
        """AC2: a tiny session does NOT balloon to the budget — output is
        bounded by available content, not by the generous cap."""
        short = _mk_session(2, answer_chars=200)
        out = _run_resume(short, model_context_window=1_000_000)
        # 150K-token budget = 600K chars; a 2-turn session must stay far under.
        assert len(out) < 20_000, f"short session ballooned to {len(out)} chars"

    def test_never_exceeds_hard_clamp(self):
        """AC4: output never exceeds token_budget*4 chars even when the
        untrimmable checkpoint + content are huge (final clamp backstop)."""
        huge = _mk_session(80, answer_chars=4000)
        # Force a tiny budget so the clamp must engage.
        out = _run_resume(huge, model_context_window=128_000)
        budget, _, _ = _compute_resume_budget(128_000)
        assert len(out) <= budget * 4, (
            f"output {len(out)} exceeds hard clamp {budget * 4}"
        )


class TestHardClamp:
    """Direct unit tests of the _hard_clamp backstop. The full-pipeline test
    above can't reliably ENGAGE the clamp (every layer is now budget-bounded),
    so the exact-boundary guarantee — INCLUSIVE of the marker (Gate-2 finding
    7) — must be pinned here, where the clamp can be forced."""

    def test_noop_when_within_budget(self):
        from core.context_injector import _hard_clamp
        text = "short text"
        out, did = _hard_clamp(text, 1000)
        assert did is False
        assert out == text  # byte-identical, untouched

    def test_clamps_and_stays_within_bound_inclusive_of_marker(self):
        # The exact-bound regression Gate-2 caught: result must be <= max_chars
        # INCLUDING the appended marker, not max_chars + len(marker).
        from core.context_injector import _hard_clamp, _CLAMP_MARKER
        text = "line\n" * 1000  # 5000 chars, many newlines
        max_chars = 500
        out, did = _hard_clamp(text, max_chars)
        assert did is True
        assert len(out) <= max_chars, (
            f"clamp overshot: {len(out)} > {max_chars}"
        )
        assert out.endswith(_CLAMP_MARKER)

    def test_single_giant_line_no_newline(self):
        # No newline before body_limit → hard cut fallback, still in bound.
        from core.context_injector import _hard_clamp, _CLAMP_MARKER
        text = "x" * 5000  # one giant line, zero newlines
        max_chars = 500
        out, did = _hard_clamp(text, max_chars)
        assert did is True
        assert len(out) <= max_chars
        assert out.endswith(_CLAMP_MARKER)

    def test_cut_on_newline_boundary(self):
        from core.context_injector import _hard_clamp
        # Body limit lands inside "CCC..."; cut should retreat to the newline
        # after the BBB block, so no partial line leaks past the boundary.
        text = "A" * 100 + "\n" + "B" * 100 + "\n" + "C" * 1000
        max_chars = 260  # body_limit = 260-35 = 225 → last \n at idx 201
        out, did = _hard_clamp(text, max_chars)
        assert did is True
        assert len(out) <= max_chars
        # The retained body ends at a newline boundary (no partial "C" line).
        body = out[:-len("\n[resume context clamped to budget]")]
        assert not body.endswith("C")

    def test_empty_session_returns_empty(self):
        """Fallback preserved: no messages → empty string, no crash."""
        out = _run_resume([], model_context_window=1_000_000)
        assert out == ""

    def test_recent_turns_keeps_newest(self):
        """The newest-first collection fix: the most recent turn must appear,
        an ancient one must not (when budget forces selection)."""
        session = _mk_session(40, answer_chars=2000)
        out = _run_resume(session, model_context_window=128_000)
        assert "task 39" in out          # newest user directive present
        assert "turn0 " not in out       # oldest assistant turn dropped
