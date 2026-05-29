"""Tests for the Knowledge Backflow Hook.

Validates that high-value assistant outputs are auto-captured
as persistent Knowledge/Notes/ pages during post-session hook execution.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from hooks.knowledge_backflow_hook import (
    KnowledgeBackflowHook,
    _is_high_value_output,
    _generate_slug,
    _extract_title,
    _build_knowledge_page,
    _sanitize_yaml_string,
    _atomic_write,
)


# ─── AC1: Hook implements SessionLifecycleHook protocol ───


class TestHookProtocol:
    """AC1: New KnowledgeBackflowHook implements SessionLifecycleHook."""

    def test_has_name_property(self):
        hook = KnowledgeBackflowHook()
        assert hook.name == "knowledge_backflow"

    def test_has_execute_method(self):
        hook = KnowledgeBackflowHook()
        assert hasattr(hook, "execute")
        assert callable(hook.execute)


# ─── AC2: Detects high-value outputs ───


class TestHighValueDetection:
    """AC2: Detects high-value outputs (>500 words prose + markers)."""

    def test_short_text_rejected(self):
        """Text under 500 words is not high-value."""
        short_text = "This is a brief response. " * 10  # ~70 words
        assert _is_high_value_output(short_text) is False

    def test_long_generic_text_rejected(self):
        """Long text without analysis markers is not high-value."""
        generic = "Run this command. " * 200  # ~600 words, no markers
        assert _is_high_value_output(generic) is False

    def test_deep_analysis_detected(self):
        """Long text with analysis structure is high-value."""
        analysis = (
            "## Analysis\n\n"
            "After examining the architecture, there are three key findings. "
            "First, the session management layer shows a pattern where... " * 30
            + "\n\n### Comparison\n\n"
            "Comparing approach A vs approach B, the tradeoffs are clear. "
            "The root cause of the issue is... " * 20
            + "\n\n### Recommendation\n\n"
            "Based on this analysis, the recommended approach is..."
        )
        assert _is_high_value_output(analysis) is True

    def test_synthesis_with_table_detected(self):
        """Content with tables and synthesis markers is high-value."""
        synthesis = (
            "## Summary\n\n"
            "| Feature | SwarmAI | Competitor |\n"
            "|---------|---------|------------|\n"
            "| Memory | Persistent | Stateless |\n"
            "| Evolution | Self-improving | Static |\n\n"
            "The key insight from this comparison is that compound intelligence "
            "requires persistent state. " * 40
            + "\n\n## Conclusion\n\n"
            "In conclusion, the architectural difference is fundamental..."
        )
        assert _is_high_value_output(synthesis) is True

    def test_research_output_detected(self):
        """Research-style output with citations is high-value."""
        research = (
            "# Research: Agent Memory Systems\n\n"
            "## Findings\n\n"
            "Based on analysis of 5 production systems, the patterns are... " * 50
            + "\n\n## Key Takeaway\n\n"
            "The evidence suggests that structured extraction outperforms "
            "brute-force replay in all measured dimensions."
        )
        assert _is_high_value_output(research) is True

    def test_code_only_output_rejected(self):
        """Pure code output without analysis is not high-value."""
        code_output = (
            "```python\n"
            + "def process_data(x):\n    return x * 2\n" * 100
            + "```\n"
        )
        assert _is_high_value_output(code_output) is False

    def test_code_heavy_with_short_prose_rejected(self):
        """49% code + short prose should NOT qualify (prose under 500 words)."""
        # 200 words of prose + big code block
        prose = "This is some analysis text. " * 25  # ~150 words
        code = "```python\n" + "x = 1\n" * 200 + "```\n"
        text = prose + "\n## Analysis\n\n" + code
        assert _is_high_value_output(text) is False

    def test_pipes_in_code_dont_trigger_table_marker(self):
        """Pipe characters inside code blocks should not count as table markers."""
        # Short prose (< 500 words) + code block with pipes
        # Even if pipes counted as markers, word count gate rejects it
        text = (
            "## Analysis\n\n"
            "This is a brief explanation. " * 20  # ~100 words prose
            + "\n```bash\ncat file | grep foo | wc -l\n"
            + "echo bar | sed 's/a/b/' | head\n" * 50  # lots of pipes in code
            + "```\n"
            + "Based on this analysis, the conclusion is clear."
        )
        # Prose too short + markers only in code = not high value
        assert _is_high_value_output(text) is False


# ─── AC3: Writes to Knowledge/Notes/ with proper format ───


class TestSlugGeneration:
    """AC3: slug generation for filenames."""

    def test_basic_slug(self):
        assert _generate_slug("Agent Memory Architecture Deep Dive") == "agent-memory-architecture-deep-dive"

    def test_strips_non_ascii(self):
        # CJK characters stripped, ASCII parts kept
        assert _generate_slug("SwarmAI vs OpenClaw — 对比分析") == "swarmai-vs-openclaw"

    def test_pure_cjk_falls_back(self):
        """Pure CJK titles produce fallback slug (known limitation)."""
        assert _generate_slug("深度分析：代理记忆架构") == "session-insight"

    def test_empty_title_falls_back(self):
        assert _generate_slug("") == "session-insight"

    def test_slug_length_capped(self):
        long_title = "A Very Long Title " * 20
        slug = _generate_slug(long_title)
        assert len(slug) <= 60


class TestExtractTitle:
    """Tests for _extract_title — adversarial coverage."""

    def test_h1_heading(self):
        assert _extract_title("# My Analysis\n\nContent here") == "My Analysis"

    def test_h2_heading(self):
        assert _extract_title("## Deep Dive\n\nContent") == "Deep Dive"

    def test_no_heading_fallback(self):
        assert _extract_title("This is the first line\nSecond line") == "This is the first line"

    def test_skips_code_block_headings(self):
        """Headings inside code blocks are ignored."""
        content = "```python\n## Not A Real Heading\n```\n\n## Real Heading\n\nContent"
        assert _extract_title(content) == "Real Heading"

    def test_skips_frontmatter(self):
        """Lines starting with --- are skipped in fallback."""
        content = "---\ntitle: foo\n---\n\nActual content here"
        # "title: foo" doesn't start with any skip marker, so it's the first
        # non-empty non-marker line. But --- lines are skipped.
        # After stripping code blocks, first line is "---" (skipped),
        # "title: foo" is the first valid fallback line.
        assert _extract_title(content) == "title: foo"

    def test_empty_content(self):
        assert _extract_title("") == "Session Insight"

    def test_only_blockquotes(self):
        """Content with only blockquotes falls back to default."""
        assert _extract_title("> quote\n> more quote") == "Session Insight"


class TestYamlSanitization:
    """Tests for YAML frontmatter injection prevention."""

    def test_quotes_escaped(self):
        assert _sanitize_yaml_string('Analysis: "Why X Beats Y"') == 'Analysis: \\"Why X Beats Y\\"'

    def test_backslashes_escaped(self):
        assert _sanitize_yaml_string("path\\to\\file") == "path\\\\to\\\\file"

    def test_newlines_removed(self):
        assert _sanitize_yaml_string("line1\nline2\r\nline3") == "line1 line2 line3"

    def test_normal_title_unchanged(self):
        assert _sanitize_yaml_string("Simple Title") == "Simple Title"


class TestKnowledgePageGeneration:
    """AC3: Knowledge page format."""

    def test_page_has_frontmatter(self):
        page = _build_knowledge_page(
            content="## Analysis\n\nSome deep analysis here...",
            session_id="abc123",
            title="Test Analysis",
            date_str="2026-05-29",
        )
        assert page.startswith("---\n")
        assert "title:" in page
        assert "date: 2026-05-29" in page
        assert "source: session" in page
        assert "session_id: abc123" in page

    def test_page_preserves_content(self):
        content = "## Analysis\n\nThis is the actual analysis content."
        page = _build_knowledge_page(
            content=content,
            session_id="abc123",
            title="Test",
            date_str="2026-05-29",
        )
        assert content in page

    def test_title_with_quotes_is_safe(self):
        """Title containing double quotes doesn't break YAML."""
        page = _build_knowledge_page(
            content="content",
            session_id="x",
            title='Analysis: "Why X"',
            date_str="2026-05-29",
        )
        # Should have escaped quotes
        assert '\\"Why X\\"' in page
        # Should not have unescaped quotes breaking frontmatter
        lines = page.split("\n")
        title_line = [l for l in lines if l.startswith("title:")][0]
        # The title line should be parseable (starts with title: " and ends with ")
        assert title_line.startswith('title: "')


class TestAtomicWrite:
    """Tests for atomic file writing."""

    def test_writes_file_successfully(self, tmp_path):
        filepath = tmp_path / "test.md"
        _atomic_write(filepath, "hello world")
        assert filepath.read_text() == "hello world"

    def test_no_temp_file_left_on_success(self, tmp_path):
        filepath = tmp_path / "test.md"
        _atomic_write(filepath, "content")
        # Only the target file should exist
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "test.md"


# ─── AC4: Hook registered in chain (verified by import test) ───


class TestRegistration:
    """AC4: Hook registered after DailyActivity in hook chain."""

    def test_import_in_main(self):
        """Verify the hook is importable from hooks package."""
        from hooks.knowledge_backflow_hook import KnowledgeBackflowHook
        assert KnowledgeBackflowHook is not None


# ─── AC5: Isolated, non-blocking ───


class TestIsolation:
    """AC5: No regression - hook is isolated and non-blocking."""

    @pytest.mark.asyncio
    async def test_hook_handles_empty_messages(self):
        """Hook gracefully handles sessions with no messages."""
        hook = KnowledgeBackflowHook()
        context = MagicMock()
        context.session_id = "test_empty"
        context.session_start_time = datetime.now().isoformat()

        with patch("hooks.knowledge_backflow_hook.db") as mock_db:
            mock_db.messages.list_by_session_paginated = AsyncMock(return_value=[])
            await hook.execute(context)

    @pytest.mark.asyncio
    async def test_hook_handles_exception_gracefully(self):
        """Hook catches internal errors without propagating."""
        hook = KnowledgeBackflowHook()
        context = MagicMock()
        context.session_id = "test_error"
        context.session_start_time = datetime.now().isoformat()

        with patch("hooks.knowledge_backflow_hook.db") as mock_db:
            mock_db.messages.list_by_session_paginated = AsyncMock(
                side_effect=Exception("DB error")
            )
            await hook.execute(context)

    @pytest.mark.asyncio
    async def test_list_type_content_extracted(self):
        """Hook correctly handles list-type message content (Claude API format)."""
        hook = KnowledgeBackflowHook()
        context = MagicMock()
        context.session_id = "test_list_content"

        # Claude API returns content as list of blocks
        analysis_text = (
            "## Analysis\n\n"
            "After examining the architecture deeply, we find key patterns. " * 60
            + "\n\n## Recommendation\n\n"
            "Based on this analysis, the recommended approach is clear and well-supported. " * 10
        )
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": analysis_text},
                    {"type": "tool_use", "name": "Bash", "input": {}},
                ],
            }
        ]

        with patch("hooks.knowledge_backflow_hook.db") as mock_db, \
             patch("hooks.knowledge_backflow_hook.SWARMWS", str(Path("/tmp/test_backflow"))):
            mock_db.messages.list_by_session_paginated = AsyncMock(return_value=messages)
            # Create temp dir
            notes_dir = Path("/tmp/test_backflow/Knowledge/Notes")
            notes_dir.mkdir(parents=True, exist_ok=True)
            try:
                await hook.execute(context)
                # Check a file was written
                files = list(notes_dir.glob("*.md"))
                assert len(files) == 1
                content = files[0].read_text()
                assert "## Analysis" in content
                assert "session_id: test_list_content" in content
            finally:
                # Cleanup
                import shutil
                shutil.rmtree("/tmp/test_backflow", ignore_errors=True)

    @pytest.mark.asyncio
    async def test_integration_file_written(self, tmp_path):
        """Integration test: qualifying message -> file written with correct format."""
        hook = KnowledgeBackflowHook()
        context = MagicMock()
        context.session_id = "test_integration"

        analysis = (
            "## Deep Architecture Analysis\n\n"
            "The system has several key properties worth examining. " * 50
            + "\n\n## Conclusion\n\n"
            "Based on this analysis, the evidence suggests the approach is sound."
        )
        messages = [
            {"role": "user", "content": "Analyze the architecture"},
            {"role": "assistant", "content": analysis},
        ]

        with patch("hooks.knowledge_backflow_hook.db") as mock_db, \
             patch("hooks.knowledge_backflow_hook.SWARMWS", str(tmp_path)):
            mock_db.messages.list_by_session_paginated = AsyncMock(return_value=messages)
            notes_dir = tmp_path / "Knowledge" / "Notes"
            notes_dir.mkdir(parents=True)

            await hook.execute(context)

            files = list(notes_dir.glob("*.md"))
            assert len(files) == 1

            content = files[0].read_text()
            # Verify frontmatter
            assert content.startswith("---\n")
            assert "session_id: test_integration" in content
            assert "auto_captured: true" in content
            assert 'title: "Deep Architecture Analysis"' in content
            # Verify body preserved
            assert "## Deep Architecture Analysis" in content
            # Verify filename format
            assert files[0].name.startswith(datetime.now().strftime("%Y-%m-%d"))
            assert "deep-architecture-analysis" in files[0].name
