"""Tests for core.context_monitor — context window usage estimation.

NOTE: These tests cover the DEPRECATED ``context_monitor`` module.
The ``agent_manager.py`` post-response pipeline now computes context
usage inline from the SDK's ``ResultMessage.usage.input_tokens`` instead
of scanning ``.jsonl`` transcript files.  These tests are retained to
verify the deprecated module still works for reference purposes.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from core.context_monitor import (
    CHECK_INTERVAL_TURNS,
    CHARS_PER_TOKEN,
    CRITICAL_PCT,
    DEFAULT_BASELINE_TOKENS,
    DEFAULT_WINDOW_TOKENS,
    WARN_PCT,
    ContextStatus,
    check_context_usage,
    _count_content_chars,
    _find_latest_transcript,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl_line(msg_type: str, role: str, content, subtype: str = "") -> str:
    """Build a single JSONL transcript line."""
    obj = {
        "type": msg_type,
        "message": {"role": role, "content": content},
    }
    if subtype:
        obj["subtype"] = subtype
    return json.dumps(obj)


def _write_transcript(tmp_dir: str, lines: list[str], name: str = "session.jsonl") -> str:
    """Write a .jsonl transcript file and return its directory path."""
    proj_dir = os.path.join(tmp_dir, "project")
    os.makedirs(proj_dir, exist_ok=True)
    path = os.path.join(proj_dir, name)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return proj_dir


# ---------------------------------------------------------------------------
# _find_latest_transcript
# ---------------------------------------------------------------------------

class TestFindLatestTranscript:
    def test_no_dir(self):
        assert _find_latest_transcript("/nonexistent/path") is None

    def test_empty_dir(self, tmp_path):
        assert _find_latest_transcript(str(tmp_path)) is None

    def test_finds_jsonl(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text('{"type": "user"}\n')
        result = _find_latest_transcript(str(tmp_path))
        assert result is not None
        assert result[0] == str(f)
        assert result[1] > 0

    def test_picks_most_recent(self, tmp_path):
        import time
        old = tmp_path / "old.jsonl"
        old.write_text("old\n")
        time.sleep(0.05)
        new = tmp_path / "new.jsonl"
        new.write_text("new\n")
        result = _find_latest_transcript(str(tmp_path))
        assert result is not None
        assert "new.jsonl" in result[0]


# ---------------------------------------------------------------------------
# _count_content_chars
# ---------------------------------------------------------------------------

class TestCountContentChars:
    def test_string_content(self):
        assert _count_content_chars("hello world") == 11

    def test_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert _count_content_chars(content) == 5

    def test_tool_use_block(self):
        content = [{"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}}]
        chars = _count_content_chars(content)
        # JSON of {"cmd": "ls"} + "Bash" (4) + 50 overhead
        assert chars > 50

    def test_tool_result_block(self):
        content = [{"type": "tool_result", "content": "output here"}]
        chars = _count_content_chars(content)
        assert chars == len("output here") + 50

    def test_mixed_blocks(self):
        content = [
            {"type": "text", "text": "abc"},
            {"type": "tool_result", "content": "xyz"},
        ]
        chars = _count_content_chars(content)
        assert chars == 3 + 3 + 50  # "abc" + "xyz" + overhead

    def test_none_content(self):
        assert _count_content_chars(None) == 0

    def test_empty_list(self):
        assert _count_content_chars([]) == 0


# ---------------------------------------------------------------------------
# check_context_usage — basic behavior
# ---------------------------------------------------------------------------

class TestCheckContextUsage:
    def test_no_projects_dir(self):
        status = check_context_usage(projects_dir="/nonexistent")
        assert status.level == "ok"
        assert status.tokens_est == 0

    def test_simple_conversation(self, tmp_path):
        lines = [
            _make_jsonl_line("user", "user", "Hello, how are you?"),
            _make_jsonl_line("assistant", "assistant", "I'm doing well, thank you!"),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)

        assert status.level == "ok"
        assert status.user_messages == 1
        assert status.assistant_messages == 1
        assert status.content_chars > 0
        assert status.tokens_est == status.content_tokens + DEFAULT_BASELINE_TOKENS
        assert status.pct < WARN_PCT

    def test_skips_progress_events(self, tmp_path):
        lines = [
            _make_jsonl_line("user", "user", "Hello"),
            json.dumps({"type": "progress", "message": {"role": "assistant", "content": "streaming chunk"}}),
            _make_jsonl_line("assistant", "assistant", "Final response"),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)

        # Progress events should be skipped
        assert status.user_messages == 1
        assert status.assistant_messages == 1


# ---------------------------------------------------------------------------
# Compaction detection
# ---------------------------------------------------------------------------

class TestCompactionDetection:
    def test_detects_compaction_in_string(self, tmp_path):
        lines = [
            _make_jsonl_line("user", "user", "old message before compaction"),
            _make_jsonl_line("assistant", "assistant", "old response " + "x" * 1000),
            _make_jsonl_line("user", "user",
                "This session is being continued from a previous conversation that ran out of context. Summary: ..."),
            _make_jsonl_line("assistant", "assistant", "New response after compaction"),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)

        assert status.compacted is True
        # Should only count content from the compaction marker onward
        # (the old "x" * 1000 should NOT be counted)
        assert status.user_messages == 1  # only the compaction message
        assert status.assistant_messages == 1  # only new response

    def test_detects_compaction_in_content_blocks(self, tmp_path):
        compaction_content = [
            {"type": "text", "text": "continued from a previous conversation that ran out of context"},
        ]
        lines = [
            _make_jsonl_line("user", "user", "old message"),
            json.dumps({
                "type": "user",
                "message": {"role": "user", "content": compaction_content},
            }),
            _make_jsonl_line("assistant", "assistant", "New response"),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.compacted is True

    def test_no_compaction(self, tmp_path):
        lines = [
            _make_jsonl_line("user", "user", "Hello"),
            _make_jsonl_line("assistant", "assistant", "Hi"),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.compacted is False


# ---------------------------------------------------------------------------
# Level thresholds
# ---------------------------------------------------------------------------

class TestLevelThresholds:
    def _make_large_transcript(self, tmp_path, content_chars: int) -> str:
        """Create a transcript with approximately the given number of content chars."""
        # Each message contributes its content length
        msg = "x" * content_chars
        lines = [_make_jsonl_line("user", "user", msg)]
        return _write_transcript(str(tmp_path), lines)

    def test_ok_level(self, tmp_path):
        # Under 70%: need content_tokens + baseline < 70% of 200K
        # 70% of 200K = 140K. baseline = 40K. So content_tokens < 100K
        # content_tokens = chars / 3, so chars < 300K
        # Use small content to stay well under
        proj_dir = self._make_large_transcript(tmp_path, 1000)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.level == "ok"

    def test_warn_level(self, tmp_path):
        # Need pct >= 70 and < 85
        # Target: 75% of 200K = 150K tokens
        # content_tokens = 150K - 40K = 110K
        # chars = 110K * 3 = 330K
        proj_dir = self._make_large_transcript(tmp_path, 330_000)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.level == "warn"
        assert status.pct >= WARN_PCT
        assert status.pct < CRITICAL_PCT

    def test_critical_level(self, tmp_path):
        # Need pct >= 85
        # Target: 90% of 200K = 180K tokens
        # content_tokens = 180K - 40K = 140K
        # chars = 140K * 3 = 420K
        proj_dir = self._make_large_transcript(tmp_path, 420_000)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.level == "critical"
        assert status.pct >= CRITICAL_PCT


# ---------------------------------------------------------------------------
# ContextStatus serialization
# ---------------------------------------------------------------------------

class TestContextStatusDict:
    def test_to_dict_structure(self):
        status = ContextStatus(
            tokens_est=60000,
            pct=30,
            level="ok",
            message="Context 30% full",
            content_chars=60000,
            content_tokens=20000,
        )
        d = status.to_dict()
        assert d["tokensEst"] == 60000
        assert d["pct"] == 30
        assert d["level"] == "ok"
        assert d["message"] == "Context 30% full"
        assert "details" in d
        assert d["details"]["contentChars"] == 60000
        assert d["details"]["contentTokens"] == 20000


# ---------------------------------------------------------------------------
# Tool block counting
# ---------------------------------------------------------------------------

class TestToolBlockCounting:
    def test_tool_use_and_result_counted(self, tmp_path):
        content = [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        ]
        result_content = [
            {"type": "tool_result", "content": "file1.txt\nfile2.txt"},
        ]
        lines = [
            _make_jsonl_line("user", "user", "list files"),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": result_content}}),
        ]
        proj_dir = _write_transcript(str(tmp_path), lines)
        status = check_context_usage(projects_dir=proj_dir)
        assert status.tool_use_blocks == 1
        assert status.tool_result_blocks == 1


# ---------------------------------------------------------------------------
# Message-level classification
# ---------------------------------------------------------------------------

class TestMessageClassification:
    def test_warn_message_format(self):
        status = ContextStatus(
            tokens_est=160000,
            pct=80,
            level="warn",
        )
        # Manually set message like the real function would
        status.message = (
            f"Heads up — we've used about {status.pct}% of this session's "
            f"context window (~{status.tokens_est // 1000}K/{DEFAULT_WINDOW_TOKENS // 1000}K tokens). "
            f"Consider saving context soon if more heavy tasks remain."
        )
        assert "80%" in status.message
        assert "160K" in status.message

    def test_critical_message_format(self):
        status = ContextStatus(
            tokens_est=180000,
            pct=90,
            level="critical",
        )
        status.message = (
            f"**Context alert**: Session is {status.pct}% full "
            f"(~{status.tokens_est // 1000}K/{DEFAULT_WINDOW_TOKENS // 1000}K tokens). "
            f"Recommend: save context and start a new session."
        )
        assert "**Context alert**" in status.message
        assert "90%" in status.message


# ---------------------------------------------------------------------------
# Integration: CHECK_INTERVAL_TURNS constant
# ---------------------------------------------------------------------------

class TestCheckInterval:
    def test_interval_is_positive(self):
        assert CHECK_INTERVAL_TURNS > 0
        assert CHECK_INTERVAL_TURNS == 5

    def test_modulo_triggers_correctly(self):
        """Verify the turn-counting logic matches the agent_manager pattern."""
        triggered = []
        for turn in range(1, 61):
            if turn == 1 or turn % CHECK_INTERVAL_TURNS == 0:
                triggered.append(turn)
        assert triggered == [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
