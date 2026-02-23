"""Tests for ContextManager.

Tests cover:
- get_context: reading context.md from workspace ContextFiles/
- update_context: writing context.md
- compress_context: generating compressed-context.md
- inject_context: token-budgeted context injection with freshness logic

Requirements: 14.1-14.9, 29.1-29.10
"""
import os
import time
from pathlib import Path

import pytest

from core.context_manager import (
    ContextManager,
    estimate_tokens,
    truncate_to_token_budget,
    CHARS_PER_TOKEN,
    DEFAULT_TOKEN_BUDGET,
)
from tests.helpers import create_workspace_with_path


# ---------------------------------------------------------------------------
# Unit helper tests
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    """Tests for the estimate_tokens helper."""

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_known_length(self):
        text = "a" * 400  # 400 chars → 100 tokens
        assert estimate_tokens(text) == 100

    def test_none_like_empty(self):
        assert estimate_tokens("") == 0


class TestTruncateToTokenBudget:
    """Tests for the truncate_to_token_budget helper."""

    def test_short_text_unchanged(self):
        text = "Hello world"
        result = truncate_to_token_budget(text, 1000)
        assert result == text

    def test_long_text_truncated(self):
        text = "a" * 10000  # 2500 tokens
        result = truncate_to_token_budget(text, 100)
        # 100 tokens * 4 chars = 400 chars max
        assert len(result) <= 500  # some slack for the truncation note
        assert "[Context truncated" in result

    def test_empty_text(self):
        assert truncate_to_token_budget("", 100) == ""

    def test_truncation_prefers_newline_break(self):
        # Build text with newlines so truncation can break cleanly
        lines = ["Line " + str(i) for i in range(200)]
        text = "\n".join(lines)
        result = truncate_to_token_budget(text, 50)
        assert "[Context truncated" in result


# ---------------------------------------------------------------------------
# ContextManager tests
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx_manager():
    return ContextManager()


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestGetContext:
    """Tests for ContextManager.get_context."""

    @pytest.mark.asyncio
    async def test_read_existing_context(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        # Create ContextFiles/context.md
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("# My Context\nHello", encoding="utf-8")

        result = await ctx_manager.get_context(ws["id"])
        assert result == "# My Context\nHello"

    @pytest.mark.asyncio
    async def test_missing_context_file_returns_empty(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        result = await ctx_manager.get_context(ws["id"])
        assert result == ""

    @pytest.mark.asyncio
    async def test_invalid_workspace_raises(self, ctx_manager):
        with pytest.raises(ValueError, match="not found"):
            await ctx_manager.get_context("nonexistent-id")


class TestUpdateContext:
    """Tests for ContextManager.update_context."""

    @pytest.mark.asyncio
    async def test_write_context(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        await ctx_manager.update_context(ws["id"], "# Updated\nNew content")

        ctx_file = Path(ws["file_path"]) / "ContextFiles" / "context.md"
        assert ctx_file.exists()
        assert ctx_file.read_text(encoding="utf-8") == "# Updated\nNew content"

    @pytest.mark.asyncio
    async def test_creates_directory_if_missing(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        # ContextFiles/ doesn't exist yet
        await ctx_manager.update_context(ws["id"], "content")
        ctx_file = Path(ws["file_path"]) / "ContextFiles" / "context.md"
        assert ctx_file.exists()

    @pytest.mark.asyncio
    async def test_invalid_workspace_raises(self, ctx_manager):
        with pytest.raises(ValueError, match="not found"):
            await ctx_manager.update_context("nonexistent-id", "content")


class TestCompressContext:
    """Tests for ContextManager.compress_context."""

    @pytest.mark.asyncio
    async def test_compress_creates_file(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        # Write a context.md first
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Short context", encoding="utf-8")

        result = await ctx_manager.compress_context(ws["id"])
        assert result == "Short context"

        compressed_file = ctx_dir / "compressed-context.md"
        assert compressed_file.exists()
        assert compressed_file.read_text(encoding="utf-8") == "Short context"

    @pytest.mark.asyncio
    async def test_compress_truncates_large_context(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        # Write a very large context (well over 4000 tokens)
        large_content = "x" * (DEFAULT_TOKEN_BUDGET * CHARS_PER_TOKEN * 2)
        (ctx_dir / "context.md").write_text(large_content, encoding="utf-8")

        result = await ctx_manager.compress_context(ws["id"])
        assert estimate_tokens(result) <= DEFAULT_TOKEN_BUDGET + 20  # small slack for note
        assert "[Context truncated" in result

    @pytest.mark.asyncio
    async def test_compress_empty_context(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        result = await ctx_manager.compress_context(ws["id"])
        assert result == ""

    @pytest.mark.asyncio
    async def test_invalid_workspace_raises(self, ctx_manager):
        with pytest.raises(ValueError, match="not found"):
            await ctx_manager.compress_context("nonexistent-id")


class TestInjectContext:
    """Tests for ContextManager.inject_context."""

    @pytest.mark.asyncio
    async def test_inject_with_fresh_compressed(self, ctx_manager, tmp_dir):
        """Requirement 14.3: Prefer compressed-context.md if fresh."""
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Full context here", encoding="utf-8")
        (ctx_dir / "compressed-context.md").write_text("Compressed version", encoding="utf-8")

        result = await ctx_manager.inject_context(ws["id"])
        assert "Current Workspace: TestWS" in result
        assert "Compressed version" in result
        # Should NOT contain the full context since compressed is fresh
        assert "Full context here" not in result

    @pytest.mark.asyncio
    async def test_inject_falls_back_to_full_context(self, ctx_manager, tmp_dir):
        """Requirement 14.4: Fallback to context.md if compressed is missing."""
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Full context here", encoding="utf-8")
        # No compressed-context.md

        result = await ctx_manager.inject_context(ws["id"])
        assert "Current Workspace: TestWS" in result
        assert "Full context here" in result

    @pytest.mark.asyncio
    async def test_inject_falls_back_when_compressed_empty(self, ctx_manager, tmp_dir):
        """Requirement 14.4: Fallback when compressed is empty."""
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Full context", encoding="utf-8")
        (ctx_dir / "compressed-context.md").write_text("", encoding="utf-8")

        result = await ctx_manager.inject_context(ws["id"])
        assert "Full context" in result

    @pytest.mark.asyncio
    async def test_inject_falls_back_when_compressed_stale(self, ctx_manager, tmp_dir):
        """Requirement 14.4: Fallback when compressed is stale (>24h)."""
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Full context", encoding="utf-8")
        compressed = ctx_dir / "compressed-context.md"
        compressed.write_text("Old compressed", encoding="utf-8")
        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(compressed, (old_time, old_time))

        result = await ctx_manager.inject_context(ws["id"])
        assert "Full context" in result
        assert "Old compressed" not in result

    @pytest.mark.asyncio
    async def test_inject_respects_token_budget(self, ctx_manager, tmp_dir):
        """Requirement 14.5: Token budget enforcement."""
        ws = await create_workspace_with_path(tmp_dir)
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        large = "x" * 50000  # ~12500 tokens
        (ctx_dir / "context.md").write_text(large, encoding="utf-8")

        result = await ctx_manager.inject_context(ws["id"], token_budget=500)
        assert estimate_tokens(result) <= 550  # small slack

    @pytest.mark.asyncio
    async def test_inject_includes_workspace_header(self, ctx_manager, tmp_dir):
        """Requirement 14.7: Prefixed with workspace name."""
        ws = await create_workspace_with_path(tmp_dir, name="ProjectAlpha")
        ctx_dir = Path(ws["file_path"]) / "ContextFiles"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        (ctx_dir / "context.md").write_text("Some context", encoding="utf-8")

        result = await ctx_manager.inject_context(ws["id"])
        assert result.startswith("Current Workspace: ProjectAlpha")

    @pytest.mark.asyncio
    async def test_inject_empty_context_returns_empty(self, ctx_manager, tmp_dir):
        ws = await create_workspace_with_path(tmp_dir)
        result = await ctx_manager.inject_context(ws["id"])
        assert result == ""

    @pytest.mark.asyncio
    async def test_inject_nonexistent_workspace_returns_empty(self, ctx_manager):
        result = await ctx_manager.inject_context("nonexistent-id")
        assert result == ""
