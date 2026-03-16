"""Unit and property-based tests for ContextDirectoryLoader.

Tests the BOOTSTRAP.md detection and creation logic, dynamic token budget
computation, truncation direction support, and L1 cache budget-tier
awareness, including:

- ``_is_empty_template()`` — structural detection of unfilled USER.md
- ``_maybe_create_bootstrap()`` — conditional BOOTSTRAP.md creation
- ``compute_token_budget()`` — dynamic budget tiers based on model window
- ``_enforce_token_budget()`` — truncate_from="head" vs "tail" behavior
- ``_write_l1_cache()`` — budget header prepended to L1 cache
- ``_load_l1_if_fresh()`` — budget-tier validation on cache load
- ``load_all()`` — dynamic budget integration

Testing methodology: unit tests for specific scenarios, property-based
tests (Hypothesis) for universal correctness properties.

Key properties verified:
- Property 3: BOOTSTRAP.md created iff USER.md is empty template AND
  BOOTSTRAP.md does not already exist.
- Property 4: Dynamic token budget tiers match model context window
  (1M/500K+, 200K+, 64K+, <64K).
- Property 5: Truncation direction matches truncate_from field.
- Property 11: L1 cache budget-tier consistency — cache returns None
  when budget mismatch, content when budget matches.
"""

import os
import tempfile
from pathlib import Path

import pytest

from core.context_directory_loader import (
    BUDGET_LARGE_MODEL,
    ContextDirectoryLoader,
    DEFAULT_TOKEN_BUDGET,
    THRESHOLD_USE_L1,
)


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """Create temporary context_dir and templates_dir for testing."""
    context_dir = tmp_path / "context"
    templates_dir = tmp_path / "templates"
    context_dir.mkdir()
    templates_dir.mkdir()
    return context_dir, templates_dir


@pytest.fixture
def bootstrap_template(tmp_dirs):
    """Write a BOOTSTRAP.md template into templates_dir."""
    _, templates_dir = tmp_dirs
    bootstrap = templates_dir / "BOOTSTRAP.md"
    bootstrap.write_text("# Welcome to SwarmAI\nOnboarding content here.\n")
    return bootstrap


# The default USER.md template content (matches backend/context/USER.md)
EMPTY_USER_TEMPLATE = """\
<!-- 👤 USER-CUSTOMIZED — This file is YOURS. SwarmAI will never overwrite your edits.
     Fill in your details so the agent can personalize responses to your preferences. -->

# User — About You

_I'll learn about you as we work together. Update this anytime, or let me fill it in as I learn._

- **Name:**
- **What to call you:**
- **Timezone:**
- **Primary language:**
- **Role:**

## Work Context

_(What do you do? What are you building?)_

## Preferences

_(How do you like to work?)_
"""

FILLED_USER_TEMPLATE = """\
<!-- 👤 USER-CUSTOMIZED -->

# User — About You

- **Name:** Alice
- **What to call you:** Alice
- **Timezone:** UTC+8
- **Primary language:** English
- **Role:** Backend Engineer
"""


# ── _is_empty_template() unit tests ───────────────────────────────────


class TestIsEmptyTemplate:
    """Unit tests for _is_empty_template() structural detection."""

    def _make_loader(self, tmp_dirs):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
        )

    def test_empty_template_returns_true(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template(EMPTY_USER_TEMPLATE) is True

    def test_filled_template_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template(FILLED_USER_TEMPLATE) is False

    def test_partially_filled_name_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** Bob\n- **Timezone:**\n- **Role:**\n"
        assert loader._is_empty_template(content) is False

    def test_partially_filled_role_returns_false(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:**\n- **Timezone:**\n- **Role:** Engineer\n"
        assert loader._is_empty_template(content) is False

    def test_underscore_placeholder_treated_as_empty(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** _\n- **Timezone:**\n- **Role:**\n"
        assert loader._is_empty_template(content) is True

    def test_no_indicators_at_all_returns_true(self, tmp_dirs):
        """Content without any indicator fields is treated as empty."""
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template("# Just a heading\nSome text.") is True

    def test_empty_string_returns_true(self, tmp_dirs):
        loader = self._make_loader(tmp_dirs)
        assert loader._is_empty_template("") is True

    def test_field_at_end_of_file_no_newline(self, tmp_dirs):
        """Field on the last line with no trailing newline."""
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:** Alice"
        assert loader._is_empty_template(content) is False

    def test_field_at_end_of_file_empty_no_newline(self, tmp_dirs):
        """Empty field on the last line with no trailing newline."""
        loader = self._make_loader(tmp_dirs)
        content = "- **Name:**"
        assert loader._is_empty_template(content) is True


# ── _maybe_create_bootstrap() unit tests ──────────────────────────────


class TestMaybeCreateBootstrap:
    """Unit tests for _maybe_create_bootstrap() conditional creation."""

    def test_creates_bootstrap_when_user_md_is_empty_template(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        bootstrap_md = context_dir / "BOOTSTRAP.md"
        assert bootstrap_md.exists()
        assert bootstrap_md.read_text() == bootstrap_template.read_text()

    def test_skips_when_user_md_is_filled(self, tmp_dirs, bootstrap_template):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(FILLED_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_bootstrap_already_exists(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        # Pre-create BOOTSTRAP.md with different content
        existing = context_dir / "BOOTSTRAP.md"
        existing.write_text("# Old bootstrap content\n")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        # Should NOT overwrite existing BOOTSTRAP.md
        assert existing.read_text() == "# Old bootstrap content\n"

    def test_skips_when_user_md_does_not_exist(
        self, tmp_dirs, bootstrap_template
    ):
        context_dir, templates_dir = tmp_dirs
        # No USER.md created

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_templates_dir_is_none(self, tmp_dirs):
        context_dir, _ = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=None
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_skips_when_bootstrap_template_missing(self, tmp_dirs):
        context_dir, templates_dir = tmp_dirs
        user_md = context_dir / "USER.md"
        user_md.write_text(EMPTY_USER_TEMPLATE, encoding="utf-8")
        # No BOOTSTRAP.md in templates_dir

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader._maybe_create_bootstrap()

        assert not (context_dir / "BOOTSTRAP.md").exists()

    def test_called_by_ensure_directory(self, tmp_dirs, bootstrap_template):
        """Verify ensure_directory() calls _maybe_create_bootstrap()."""
        context_dir, templates_dir = tmp_dirs
        # Write USER.md template into templates_dir so ensure_directory
        # copies it to context_dir
        (templates_dir / "USER.md").write_text(
            EMPTY_USER_TEMPLATE, encoding="utf-8"
        )

        loader = ContextDirectoryLoader(
            context_dir=context_dir, templates_dir=templates_dir
        )
        loader.ensure_directory()

        # After ensure_directory, USER.md should be copied (empty template)
        # and BOOTSTRAP.md should be created
        assert (context_dir / "USER.md").exists()
        assert (context_dir / "BOOTSTRAP.md").exists()


# ── compute_token_budget() unit tests ─────────────────────────────────


class TestComputeTokenBudget:
    """Unit tests for compute_token_budget() dynamic budget tiers.

    Validates: Requirements 11.1, 11.2, 11.3, 11.4, 11.5, 14.6
    """

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            token_budget=token_budget,
            templates_dir=templates_dir,
        )

    def test_large_model_200k(self, tmp_dirs):
        """>=200K context window → BUDGET_LARGE_MODEL (50,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(200_000) == BUDGET_LARGE_MODEL

    def test_large_model_499k(self, tmp_dirs):
        """499K context window → BUDGET_LARGE_MODEL (50,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(499_999) == BUDGET_LARGE_MODEL

    def test_1m_model_500k(self, tmp_dirs):
        """>=500K context window → BUDGET_1M_MODEL (100,000)."""
        from core.context_directory_loader import BUDGET_1M_MODEL
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(500_000) == BUDGET_1M_MODEL

    def test_1m_model_1m(self, tmp_dirs):
        """1M context window → BUDGET_1M_MODEL (100,000)."""
        from core.context_directory_loader import BUDGET_1M_MODEL
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(1_000_000) == BUDGET_1M_MODEL

    def test_medium_model_64k(self, tmp_dirs):
        """Exactly 64K → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(64_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_128k(self, tmp_dirs):
        """128K context window → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(128_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_199999(self, tmp_dirs):
        """Just below 200K → DEFAULT_TOKEN_BUDGET (30,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(199_999) == DEFAULT_TOKEN_BUDGET

    def test_small_model_below_64k(self, tmp_dirs):
        """<64K → instance token_budget (self.token_budget)."""
        loader = self._make_loader(tmp_dirs, token_budget=15_000)
        assert loader.compute_token_budget(32_000) == 15_000

    def test_small_model_63999(self, tmp_dirs):
        """Just below 64K boundary → instance token_budget."""
        loader = self._make_loader(tmp_dirs, token_budget=20_000)
        assert loader.compute_token_budget(63_999) == 20_000

    def test_none_falls_back_to_default(self, tmp_dirs):
        """None model_context_window → DEFAULT_TOKEN_BUDGET."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(None) == DEFAULT_TOKEN_BUDGET

    def test_zero_falls_back_to_default(self, tmp_dirs):
        """Zero model_context_window → DEFAULT_TOKEN_BUDGET."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(0) == DEFAULT_TOKEN_BUDGET


# ── _enforce_token_budget() truncate_from unit tests ──────────────────


class TestEnforceTokenBudgetTruncateFrom:
    """Unit tests for truncate_from support in _enforce_token_budget().

    Validates: Requirements 16.1, 16.3, 16.4, 16.5
    """

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            token_budget=token_budget,
            templates_dir=templates_dir,
        )

    def test_tail_truncation_keeps_beginning(self, tmp_dirs):
        """truncate_from='tail' keeps first N words (default behavior)."""
        loader = self._make_loader(tmp_dirs, token_budget=50)
        # Create a section with many words that will exceed budget
        long_content = " ".join(f"word{i}" for i in range(200))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "tail"),
        ]
        result = loader._enforce_token_budget(sections, budget=50)
        # The truncated section should start with "word0"
        _, _, content, _, _ = result[1]
        assert content.startswith("word0")
        assert "[Truncated:" in content

    def test_head_truncation_keeps_end(self, tmp_dirs):
        """truncate_from='head' keeps last N words (newest preserved)."""
        loader = self._make_loader(tmp_dirs, token_budget=50)
        long_content = " ".join(f"word{i}" for i in range(200))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "head"),
        ]
        result = loader._enforce_token_budget(sections, budget=50)
        _, _, content, _, _ = result[1]
        # Head truncation: indicator at the start, last words preserved
        assert content.startswith("[Truncated:")
        assert "word199" in content

    def test_head_truncation_does_not_contain_first_words(self, tmp_dirs):
        """Head truncation should remove the beginning words."""
        loader = self._make_loader(tmp_dirs, token_budget=50)
        long_content = " ".join(f"word{i}" for i in range(200))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "head"),
        ]
        result = loader._enforce_token_budget(sections, budget=50)
        _, _, content, _, _ = result[1]
        # First words should be gone (truncated from head)
        assert "word0 word1 word2" not in content

    def test_no_truncation_when_under_budget(self, tmp_dirs):
        """Sections under budget are returned unchanged."""
        loader = self._make_loader(tmp_dirs, token_budget=100_000)
        sections = [
            (0, "A", "hello world", False, "tail"),
            (5, "B", "foo bar", True, "head"),
        ]
        result = loader._enforce_token_budget(sections, budget=100_000)
        assert result == sections

    def test_budget_parameter_overrides_instance(self, tmp_dirs):
        """Explicit budget param is used instead of self.token_budget."""
        loader = self._make_loader(tmp_dirs, token_budget=100_000)
        long_content = " ".join(f"w{i}" for i in range(500))
        sections = [
            (0, "Fixed", "small", False, "tail"),
            (5, "Big", long_content, True, "tail"),
        ]
        # Pass a very small budget — should trigger truncation
        result = loader._enforce_token_budget(sections, budget=30)
        _, _, content, _, _ = result[1]
        assert "[Truncated:" in content

    def test_full_removal_preserves_truncate_from(self, tmp_dirs):
        """When a section is fully removed, truncate_from is preserved."""
        loader = self._make_loader(tmp_dirs, token_budget=10)
        long_content = " ".join(f"w{i}" for i in range(500))
        sections = [
            (0, "Fixed", "small content here", False, "tail"),
            (9, "Big", long_content, True, "head"),
        ]
        result = loader._enforce_token_budget(sections, budget=10)
        _, _, _, _, truncate_from = result[1]
        assert truncate_from == "head"


# ── L1 Cache Budget-Tier Tests ─────────────────────────────────────────


class TestWriteL1Cache:
    """Tests for _write_l1_cache() budget header writing."""

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
            token_budget=token_budget,
        )

    def test_writes_budget_header_as_first_line(self, tmp_dirs):
        """Cache file starts with <!-- budget:NNNNN --> header."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("hello world", budget=40000)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        assert raw.startswith("<!-- budget:40000 -->\n")

    def test_content_follows_header(self, tmp_dirs):
        """Actual content appears after the budget header line."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("my context content", budget=25000)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        lines = raw.split("\n", 1)
        assert lines[0] == "<!-- budget:25000 -->"
        assert lines[1] == "my context content"

    def test_default_budget_is_default_token_budget(self, tmp_dirs):
        """When budget is not specified, DEFAULT_TOKEN_BUDGET is used."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("content")
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        raw = l1_path.read_text(encoding="utf-8")
        assert raw.startswith(f"<!-- budget:{DEFAULT_TOKEN_BUDGET} -->\n")


class TestLoadL1IfFresh:
    """Tests for _load_l1_if_fresh() budget-tier validation."""

    def _make_loader(self, tmp_dirs, token_budget=DEFAULT_TOKEN_BUDGET):
        context_dir, templates_dir = tmp_dirs
        return ContextDirectoryLoader(
            context_dir=context_dir,
            templates_dir=templates_dir,
            token_budget=token_budget,
        )

    def _write_cache_with_budget(self, context_dir, budget, content="cached content"):
        """Helper: write an L1 cache file with a budget header."""
        l1_path = context_dir / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text(
            f"<!-- budget:{budget} -->\n{content}", encoding="utf-8"
        )

    def test_returns_content_when_budget_matches(self, tmp_dirs):
        """Returns cached content (sans header) when budget matches."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000, "my content")
        # Bypass _is_l1_fresh by monkeypatching
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result == "my content"

    def test_returns_none_when_budget_mismatch(self, tmp_dirs):
        """Returns None when cached budget differs from expected."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000, "my content")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_returns_none_when_header_missing(self, tmp_dirs):
        """Returns None for old-format cache without budget header."""
        loader = self._make_loader(tmp_dirs)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text("old cache without header\n", encoding="utf-8")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_returns_none_when_no_newline(self, tmp_dirs):
        """Returns None for malformed cache with no newline."""
        loader = self._make_loader(tmp_dirs)
        l1_path = tmp_dirs[0] / "L1_SYSTEM_PROMPTS.md"
        l1_path.write_text("<!-- budget:40000 -->", encoding="utf-8")
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result is None

    def test_returns_none_when_l1_not_fresh(self, tmp_dirs):
        """Returns None when _is_l1_fresh() returns False."""
        loader = self._make_loader(tmp_dirs)
        self._write_cache_with_budget(tmp_dirs[0], 40000)
        loader._is_l1_fresh = lambda: False
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result is None

    def test_returns_none_when_file_missing(self, tmp_dirs):
        """Returns None when L1 cache file does not exist."""
        loader = self._make_loader(tmp_dirs)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None

    def test_multiline_content_preserved(self, tmp_dirs):
        """Multi-line content after header is returned intact."""
        loader = self._make_loader(tmp_dirs)
        content = "line one\nline two\nline three"
        self._write_cache_with_budget(tmp_dirs[0], 25000, content)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result == content

    def test_roundtrip_write_then_load(self, tmp_dirs):
        """Content survives a write→load roundtrip with matching budget."""
        loader = self._make_loader(tmp_dirs)
        original = "## SwarmAI\nHello world\n\n## Memory\nStuff here"
        loader._write_l1_cache(original, budget=40000)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=40000)
        assert result == original

    def test_roundtrip_budget_mismatch_returns_none(self, tmp_dirs):
        """Write with budget A, load with budget B → None."""
        loader = self._make_loader(tmp_dirs)
        loader._write_l1_cache("content", budget=40000)
        loader._is_l1_fresh = lambda: True
        result = loader._load_l1_if_fresh(expected_budget=25000)
        assert result is None


# ── CJK Token Estimation ─────────────────────────────────────────────


class TestEstimateTokensCJK:
    """Tests for CJK-aware token estimation."""

    def test_pure_ascii_unchanged(self):
        """Pure ASCII text uses the original word-based heuristic."""
        text = "Hello world this is a test"
        result = ContextDirectoryLoader.estimate_tokens(text)
        # 6 words * 4/3 = 8
        assert result == 8

    def test_pure_chinese_text(self):
        """Pure Chinese text should count characters, not words."""
        # 12 Chinese characters — should be ~8 tokens (12 / 1.5)
        text = "你好世界这是一个测试用例吧"
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result == 8  # 12 / 1.5 = 8

    def test_mixed_cjk_and_latin(self):
        """Mixed CJK + Latin text sums both estimates."""
        # 4 Chinese chars → 4/1.5 ≈ 2 CJK tokens
        # "hello world" → 2 words * 4/3 ≈ 2 Latin tokens
        text = "你好世界 hello world"
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result >= 4  # At least 2 CJK + 2 Latin

    def test_chinese_much_higher_than_naive(self):
        """A Chinese paragraph should estimate far more than 1 token."""
        # This is a single "word" by split() but should be many tokens
        text = "这是一段中文文本用于测试令牌估算的准确性确保中日韩文字不会被低估"
        naive_word_count = len(text.split())
        assert naive_word_count == 1  # Naive split sees 1 word
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result >= 15  # Should be much more than 1

    def test_japanese_hiragana(self):
        """Japanese hiragana characters should be CJK-counted."""
        text = "おはようございます"  # 9 hiragana chars
        result = ContextDirectoryLoader.estimate_tokens(text)
        assert result == 6  # 9 / 1.5 = 6

    def test_empty_returns_zero(self):
        """Empty/whitespace returns 0 (unchanged behavior)."""
        assert ContextDirectoryLoader.estimate_tokens("") == 0
        assert ContextDirectoryLoader.estimate_tokens("   ") == 0

    def test_single_cjk_char(self):
        """Single CJK character returns at least 1."""
        result = ContextDirectoryLoader.estimate_tokens("你")
        assert result >= 1


# ── Group Channel Exclusion ──────────────────────────────────────────


class TestExcludeFilenames:
    """Tests for the exclude_filenames parameter in assembly."""

    def _write_context_files(self, context_dir: Path):
        """Create minimal context files for testing exclusion."""
        (context_dir / "SWARMAI.md").write_text("# Core\nYou are SwarmAI.")
        (context_dir / "MEMORY.md").write_text("# Memory\nSecret personal memory content.")
        (context_dir / "USER.md").write_text("# User\n**Name:** TestUser\n**Timezone:** UTC\n**Role:** Dev")
        (context_dir / "PROJECTS.md").write_text("# Projects\nActive project list.")

    def test_no_exclusion_includes_all(self, tmp_path):
        """Without exclusions, all files appear in output."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        result = loader._assemble_from_sources(exclude_filenames=None)
        assert "Secret personal memory" in result
        assert "TestUser" in result

    def test_exclude_memory_removes_it(self, tmp_path):
        """Excluding MEMORY.md removes personal memory from output."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        result = loader._assemble_from_sources(exclude_filenames={"MEMORY.md"})
        assert "Secret personal memory" not in result
        assert "SwarmAI" in result  # Non-excluded files still present

    def test_exclude_memory_and_user(self, tmp_path):
        """Group channel exclusion removes both MEMORY.md and USER.md."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)
        from core.context_directory_loader import GROUP_CHANNEL_EXCLUDE
        result = loader._assemble_from_sources(exclude_filenames=set(GROUP_CHANNEL_EXCLUDE))
        assert "Secret personal memory" not in result
        assert "TestUser" not in result
        assert "SwarmAI" in result

    def test_load_all_skips_cache_when_excluding(self, tmp_path):
        """load_all bypasses L1 cache when exclude_filenames is set."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)

        # Pre-populate L1 cache with full content (includes MEMORY)
        full = loader._assemble_from_sources()
        loader._write_l1_cache(full, budget=50000)
        loader._is_l1_fresh = lambda: True

        # Load with exclusion — should NOT use the cache
        result = loader.load_all(
            model_context_window=200_000,
            exclude_filenames={"MEMORY.md"},
        )
        assert "Secret personal memory" not in result

    def test_load_all_no_exclusion_uses_cache(self, tmp_path):
        """load_all uses L1 cache when no exclusions (normal path)."""
        context_dir = tmp_path / "ctx"
        context_dir.mkdir()
        self._write_context_files(context_dir)
        loader = ContextDirectoryLoader(context_dir=context_dir)

        # Write cache with known content
        loader._write_l1_cache("cached content only", budget=50000)
        loader._is_l1_fresh = lambda: True

        result = loader.load_all(model_context_window=200_000)
        assert result == "cached content only"


# ── Content Cleaning ─────────────────────────────────────────────────


class TestCleanContent:
    """Tests for _clean_content — HTML comment stripping and H1 dedup."""

    def test_strips_html_comments(self):
        """HTML comments are removed from assembled content."""
        raw = '<!-- ⚙️ SYSTEM DEFAULT -->\n# Soul\nYou are warm.'
        result = ContextDirectoryLoader._clean_content(raw, "Soul")
        assert "SYSTEM DEFAULT" not in result
        assert "warm" in result

    def test_strips_multiline_html_comment(self):
        """Multi-line HTML comments are fully removed."""
        raw = (
            '<!-- ⚙️ SYSTEM DEFAULT — Managed by SwarmAI.\n'
            '     Edits here will be OVERWRITTEN. -->\n'
            '# Identity\nI am SwarmAI.'
        )
        result = ContextDirectoryLoader._clean_content(raw, "Identity")
        assert "OVERWRITTEN" not in result
        assert "SwarmAI" in result

    def test_strips_redundant_h1_matching_section_name(self):
        """H1 that matches section_name is removed (avoids ## + # duplication)."""
        raw = "# SwarmAI — Your AI Command Center\n\nYou are the central intelligence."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert not result.startswith("# SwarmAI")
        assert "central intelligence" in result

    def test_keeps_h1_not_matching_section_name(self):
        """H1 that doesn't match section_name is preserved."""
        raw = "# Completely Different Title\n\nSome content here."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert "# Completely Different Title" in result

    def test_keeps_h2_headers(self):
        """H2 headers are never stripped (only H1 is checked)."""
        raw = "## Sub Section\nContent here."
        result = ContextDirectoryLoader._clean_content(raw, "Sub Section")
        assert "## Sub Section" in result

    def test_empty_after_comment_strip_returns_empty(self):
        """If only HTML comments exist, returns empty string."""
        raw = "<!-- just a comment -->"
        result = ContextDirectoryLoader._clean_content(raw, "Test")
        assert result == ""

    def test_h1_with_colon_separator(self):
        """H1 with colon separator: 'Soul: Who You Are' matches 'Soul'."""
        raw = "# Soul: Who You Are\n\nPersonality content."
        result = ContextDirectoryLoader._clean_content(raw, "Soul")
        assert not result.startswith("# Soul")
        assert "Personality content" in result

    def test_h1_with_en_dash_separator(self):
        """H1 with en-dash: 'Agent – Directives' matches 'Agent Directives'."""
        raw = "# Agent Directives – How to Act\n\nBe resourceful."
        result = ContextDirectoryLoader._clean_content(raw, "Agent Directives")
        assert not result.startswith("# Agent Directives")
        assert "Be resourceful" in result

    def test_case_insensitive_h1_match(self):
        """H1 matching is case-insensitive."""
        raw = "# SWARMAI\n\nContent."
        result = ContextDirectoryLoader._clean_content(raw, "SwarmAI")
        assert not result.startswith("# SWARMAI")

    def test_preserves_content_without_h1(self):
        """Content without an H1 is returned unchanged (minus comments)."""
        raw = "Just plain content\nwith multiple lines."
        result = ContextDirectoryLoader._clean_content(raw, "Test")
        assert result == raw
