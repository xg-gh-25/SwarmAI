"""Tests for G3 shadow recall — validates recall quality logging without prompt injection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.session_router import _shadow_recall, _shadowed_sessions


@pytest.fixture(autouse=True)
def clear_shadow_state():
    """Reset module-level shadow state between tests."""
    _shadowed_sessions.clear()
    yield
    _shadowed_sessions.clear()


class TestShadowRecallTriggering:
    """AC1: First user message triggers shadow recall in non-channel sessions."""

    @pytest.mark.asyncio
    async def test_first_message_triggers_shadow(self, tmp_path):
        """First message in a session should run shadow recall."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        await _shadow_recall(
            session_id="sess_001",
            user_message="评估下 evolution pipeline 的产出",
            working_directory=str(tmp_path),
            is_channel=False,
        )

        log_path = ctx_dir / "recall_shadow.jsonl"
        assert log_path.exists(), "Shadow recall should create JSONL log file"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["session_id"] == "sess_001"
        assert "fts5" in entry
        assert entry["injected"] is False  # Shadow mode — never inject

    @pytest.mark.asyncio
    async def test_second_message_skipped(self, tmp_path):
        """Second message in same session should be skipped (already shadowed)."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        await _shadow_recall(
            session_id="sess_002",
            user_message="first message",
            working_directory=str(tmp_path),
            is_channel=False,
        )
        await _shadow_recall(
            session_id="sess_002",
            user_message="second message",
            working_directory=str(tmp_path),
            is_channel=False,
        )

        log_path = ctx_dir / "recall_shadow.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1, "Second call should be skipped"


class TestChannelExclusion:
    """AC5: Channel sessions (Slack) are excluded."""

    @pytest.mark.asyncio
    async def test_channel_session_skipped(self, tmp_path):
        """Channel sessions should not trigger shadow recall."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        await _shadow_recall(
            session_id="slack_001",
            user_message="hello from slack",
            working_directory=str(tmp_path),
            is_channel=True,
        )

        log_path = ctx_dir / "recall_shadow.jsonl"
        assert not log_path.exists(), "Channel session should not create log"


class TestDualPathTiming:
    """AC2: Both FTS5-only and embedding paths attempted with independent timing."""

    @pytest.mark.asyncio
    async def test_log_entry_has_dual_timing(self, tmp_path):
        """Log entry should have separate fts5 and embedding timing fields."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        await _shadow_recall(
            session_id="sess_timing",
            user_message="test query for timing",
            working_directory=str(tmp_path),
            is_channel=False,
        )

        log_path = ctx_dir / "recall_shadow.jsonl"
        entry = json.loads(log_path.read_text().strip())
        # FTS5 should always have timing (even if 0 results)
        assert "fts5" in entry
        assert "ms" in entry["fts5"]
        assert "hits" in entry["fts5"]
        # Embedding path should exist (may have timing=None if no embed available)
        assert "embedding" in entry
        assert "ms" in entry["embedding"]


class TestGracefulDegradation:
    """AC6: Errors never block message processing."""

    @pytest.mark.asyncio
    async def test_db_missing_no_crash(self, tmp_path, monkeypatch):
        """Missing knowledge DB should not crash — just skip."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        # Prevent fallback to the real global DB
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")

        await _shadow_recall(
            session_id="sess_nodb",
            user_message="test with no db",
            working_directory=str(tmp_path),
            is_channel=False,
        )

        # Should create a log with no_db error or 0 hits
        # Key: no exception raised
        log_path = ctx_dir / "recall_shadow.jsonl"
        if log_path.exists():
            entry = json.loads(log_path.read_text().strip())
            assert entry["fts5"]["hits"] == 0 or "no_db" in entry["fts5"].get("error", "")

    @pytest.mark.asyncio
    async def test_corrupt_db_no_crash(self, tmp_path):
        """Corrupt DB should not crash."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        # Create a fake corrupt DB
        db_path = tmp_path / "data.db"
        db_path.write_text("not a database")

        await _shadow_recall(
            session_id="sess_corrupt",
            user_message="test with corrupt db",
            working_directory=str(tmp_path),
            is_channel=False,
        )
        # Key: no exception raised


class TestNoPromptInjection:
    """AC4: Shadow mode never injects into system prompt."""

    @pytest.mark.asyncio
    async def test_return_value_is_none(self, tmp_path):
        """_shadow_recall should return None (fire-and-forget, no injection)."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()

        result = await _shadow_recall(
            session_id="sess_noinject",
            user_message="test no injection",
            working_directory=str(tmp_path),
            is_channel=False,
        )
        assert result is None, "Shadow mode must return None — no prompt injection"
