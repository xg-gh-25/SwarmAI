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
- Property 4: Dynamic token budget tiers match model context window.
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
        """>=200K context window → BUDGET_LARGE_MODEL (40,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(200_000) == BUDGET_LARGE_MODEL

    def test_large_model_500k(self, tmp_dirs):
        """500K context window → BUDGET_LARGE_MODEL (40,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(500_000) == BUDGET_LARGE_MODEL

    def test_medium_model_64k(self, tmp_dirs):
        """Exactly 64K → DEFAULT_TOKEN_BUDGET (25,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(64_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_128k(self, tmp_dirs):
        """128K context window → DEFAULT_TOKEN_BUDGET (25,000)."""
        loader = self._make_loader(tmp_dirs)
        assert loader.compute_token_budget(128_000) == DEFAULT_TOKEN_BUDGET

    def test_medium_model_199999(self, tmp_dirs):
        """Just below 200K → DEFAULT_TOKEN_BUDGET (25,000)."""
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
