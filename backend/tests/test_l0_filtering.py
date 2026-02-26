"""Unit tests for L0 tag-based filtering methods in ContextAssembler.

Tests the three filtering methods introduced in Task 3.2:

- ``_extract_l0_tags``              — YAML frontmatter parsing for tags/active_domains
- ``_extract_live_context_keywords`` — Keyword extraction from Layer 2 content
- ``_is_l0_relevant``               — Tag intersection with legacy fallback

Testing methodology: unit tests covering core logic, edge cases, and
error handling (malformed YAML, empty inputs, template placeholders).

Validates: Requirement 16.2
"""
import pytest

from core.context_assembler import ContextAssembler


@pytest.fixture
def assembler(tmp_path):
    """Create a ContextAssembler with a temporary workspace path."""
    return ContextAssembler(workspace_path=str(tmp_path))


# ── _extract_l0_tags ───────────────────────────────────────────────────


class TestExtractL0Tags:
    """Tests for _extract_l0_tags YAML frontmatter parsing."""

    def test_extracts_tags_and_domains(self, assembler):
        content = (
            "---\n"
            "tags: [python, api-design, authentication]\n"
            "active_domains: [backend, security]\n"
            "---\n"
            "# Project Context Abstract\n"
        )
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "api-design", "authentication", "backend", "security"}

    def test_tags_only(self, assembler):
        content = "---\ntags: [python, fastapi]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "fastapi"}

    def test_domains_only(self, assembler):
        content = "---\nactive_domains: [backend, database]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"backend", "database"}

    def test_lowercases_tags(self, assembler):
        content = "---\ntags: [Python, API-Design]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "api-design"}

    def test_strips_whitespace_from_tags(self, assembler):
        content = "---\ntags: [ python , fastapi ]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "fastapi"}

    def test_empty_content(self, assembler):
        assert assembler._extract_l0_tags("") == set()
        assert assembler._extract_l0_tags("   ") == set()
        assert assembler._extract_l0_tags(None) == set()

    def test_no_frontmatter(self, assembler):
        content = "# Just a heading\nSome content without frontmatter."
        assert assembler._extract_l0_tags(content) == set()

    def test_malformed_yaml(self, assembler):
        content = "---\ntags: [unclosed bracket\n---\n# Abstract\n"
        # Should log warning and return empty set
        assert assembler._extract_l0_tags(content) == set()

    def test_frontmatter_not_a_dict(self, assembler):
        content = "---\n- just a list\n- not a dict\n---\n# Abstract\n"
        assert assembler._extract_l0_tags(content) == set()

    def test_tags_not_a_list(self, assembler):
        content = "---\ntags: not-a-list\n---\n# Abstract\n"
        assert assembler._extract_l0_tags(content) == set()

    def test_empty_tags_list(self, assembler):
        content = "---\ntags: []\nactive_domains: []\n---\n# Abstract\n"
        assert assembler._extract_l0_tags(content) == set()

    def test_skips_non_string_tags(self, assembler):
        content = "---\ntags: [python, 123, true, null, fastapi]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "fastapi"}

    def test_skips_empty_string_tags(self, assembler):
        content = "---\ntags: [python, '', '  ', fastapi]\n---\n# Abstract\n"
        result = assembler._extract_l0_tags(content)
        assert result == {"python", "fastapi"}


# ── _extract_live_context_keywords ─────────────────────────────────────


class TestExtractLiveContextKeywords:
    """Tests for keyword extraction from Layer 2 content."""

    def test_extracts_significant_words(self, assembler):
        content = "Working on Python authentication for the backend API"
        result = assembler._extract_live_context_keywords(content)
        assert "python" in result
        assert "authentication" in result
        assert "backend" in result
        assert "api" in result

    def test_filters_short_words(self, assembler):
        content = "I am on it ok go do"
        result = assembler._extract_live_context_keywords(content)
        # All words are <= 2 chars after lowercasing
        assert len(result) == 0

    def test_filters_stop_words(self, assembler):
        content = "the and for with this that from are was"
        result = assembler._extract_live_context_keywords(content)
        assert len(result) == 0

    def test_strips_punctuation(self, assembler):
        content = "**python**, (authentication) [backend] api-design."
        result = assembler._extract_live_context_keywords(content)
        assert "python" in result
        assert "authentication" in result
        assert "backend" in result
        assert "api-design" in result

    def test_lowercases_tokens(self, assembler):
        content = "Python FastAPI Backend"
        result = assembler._extract_live_context_keywords(content)
        assert "python" in result
        assert "fastapi" in result
        assert "backend" in result

    def test_empty_content(self, assembler):
        assert assembler._extract_live_context_keywords("") == set()
        assert assembler._extract_live_context_keywords("   ") == set()
        assert assembler._extract_live_context_keywords(None) == set()

    def test_markdown_content(self, assembler):
        content = "## Task: Implement authentication\n- Fix security bug\n- Update database schema"
        result = assembler._extract_live_context_keywords(content)
        assert "task" in result
        assert "implement" in result
        assert "authentication" in result
        assert "security" in result
        assert "database" in result


# ── _is_l0_relevant ───────────────────────────────────────────────────


class TestIsL0Relevant:
    """Tests for L0 relevance determination with tag intersection and legacy fallback."""

    def test_relevant_when_tags_overlap(self, assembler):
        l0 = "---\ntags: [python, authentication]\nactive_domains: [backend]\n---\n# Abstract\n"
        keywords = {"python", "database", "api"}
        assert assembler._is_l0_relevant(l0, keywords) is True

    def test_not_relevant_when_tags_disjoint(self, assembler):
        l0 = "---\ntags: [python, authentication]\nactive_domains: [backend]\n---\n# Abstract\n"
        keywords = {"javascript", "frontend", "react"}
        assert assembler._is_l0_relevant(l0, keywords) is False

    def test_relevant_via_domain_overlap(self, assembler):
        l0 = "---\ntags: [python]\nactive_domains: [backend, security]\n---\n# Abstract\n"
        keywords = {"security", "audit"}
        assert assembler._is_l0_relevant(l0, keywords) is True

    def test_legacy_fallback_non_empty_content(self, assembler):
        """No frontmatter but real content → relevant."""
        l0 = "# Project Context\nThis project handles authentication and user management."
        keywords = {"anything"}
        assert assembler._is_l0_relevant(l0, keywords) is True

    def test_legacy_fallback_empty_content(self, assembler):
        assert assembler._is_l0_relevant("", {"python"}) is False
        assert assembler._is_l0_relevant("   ", {"python"}) is False

    def test_legacy_fallback_template_placeholder(self, assembler):
        """Template-only content should not be considered relevant."""
        l0 = "# Context Abstract\n\nTODO: ..."
        assert assembler._is_l0_relevant(l0, {"python"}) is False

    def test_legacy_fallback_heading_with_todo(self, assembler):
        l0 = "# Context Abstract\nTODO: fill in later"
        assert assembler._is_l0_relevant(l0, {"python"}) is False

    def test_none_content(self, assembler):
        assert assembler._is_l0_relevant(None, {"python"}) is False

    def test_malformed_yaml_falls_back_to_legacy(self, assembler):
        """Malformed YAML → empty tags → legacy fallback on body content."""
        l0 = "---\ntags: [unclosed\n---\n# Real Content\nThis has actual project info about python."
        # Tags extraction fails, but body content is non-empty and non-template
        assert assembler._is_l0_relevant(l0, {"python"}) is True

    def test_empty_tags_falls_back_to_legacy(self, assembler):
        """Frontmatter with empty tags → legacy fallback."""
        l0 = "---\ntags: []\n---\n# Real Content\nActual project description here."
        assert assembler._is_l0_relevant(l0, {"python"}) is True

    def test_empty_keywords_with_tags(self, assembler):
        """Tags present but no keywords → no overlap → not relevant."""
        l0 = "---\ntags: [python, backend]\n---\n# Abstract\n"
        assert assembler._is_l0_relevant(l0, set()) is False
