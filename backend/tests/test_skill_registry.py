"""Tests for SkillRegistry — compact skill index for system prompt.

Key public symbols tested:
- ``SkillRegistry``    — Scanner + categorizer
- ``SKILL_CATEGORIES`` — Known category mapping
"""
from __future__ import annotations

import pytest
from pathlib import Path

from core.skill_registry import SkillRegistry, SKILL_CATEGORIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a fake skills directory with some SKILL.md files.

    Includes both always-tier and lazy-tier (default) skills for tiered
    registry testing.
    """
    always_skills = {
        "s_save-memory": "---\nname: save-memory\ndescription: >\n  Save to memory.\n  TRIGGER: remember.\ntier: always\n---\n# save-memory\nA test skill.",
        "s_slack": "---\nname: slack\ndescription: >\n  Slack integration.\n  TRIGGER: slack.\ntier: always\n---\n# slack\nA test skill.",
    }
    lazy_skills = {
        "s_code-review": "---\nname: code-review\ndescription: >\n  Review code quality.\n  TRIGGER: review code.\ntier: lazy\n---\n# code-review\nA test skill.",
        "s_deep-research": "---\nname: deep-research\ndescription: >\n  Deep research.\n  TRIGGER: research.\n---\n# deep-research\nA test skill.",
        "s_browser-agent": "---\nname: browser-agent\ndescription: >\n  Browser automation.\n  TRIGGER: browse.\n---\n# browser-agent\nA test skill.",
        "s_unknown-cool-skill": "# unknown-cool-skill\nA test skill.",
    }
    for name, content in {**always_skills, **lazy_skills}.items():
        skill_path = tmp_path / name
        skill_path.mkdir()
        (skill_path / "SKILL.md").write_text(content)
    return tmp_path


@pytest.fixture
def registry(skills_dir: Path) -> SkillRegistry:
    return SkillRegistry(skills_dir=skills_dir)


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    d = tmp_path / "empty_skills"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_skills(registry: SkillRegistry):
    """Finds skills in s_*/SKILL.md pattern."""
    skills = registry._discover_skills()
    assert "save-memory" in skills
    assert "code-review" in skills
    assert "deep-research" in skills
    assert len(skills) == 6


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

def test_categorize_known_skills(registry: SkillRegistry):
    """Known skills placed in correct category."""
    skills = ["save-memory", "code-review", "deep-research", "slack", "browser-agent"]
    categories = registry._categorize(skills)
    assert "save-memory" in categories.get("Memory", [])
    assert "code-review" in categories.get("Development", [])
    assert "deep-research" in categories.get("Research", [])
    assert "slack" in categories.get("Integrations", [])
    assert "browser-agent" in categories.get("Automation", [])


def test_uncategorized_in_other(registry: SkillRegistry):
    """Unknown skill name goes to 'Other'."""
    skills = ["unknown-cool-skill"]
    categories = registry._categorize(skills)
    assert "unknown-cool-skill" in categories.get("Other", [])


# ---------------------------------------------------------------------------
# Compact format
# ---------------------------------------------------------------------------

def test_compact_format(registry: SkillRegistry):
    """Output matches expected categorized markdown format."""
    output = registry.generate_compact_registry()
    assert "## Available Skills" in output
    # Skills categorized by known categories
    assert "### Memory" in output  # save-memory
    assert "save-memory" in output
    assert "code-review" in output


def test_tier_from_frontmatter(registry: SkillRegistry):
    """_read_tier reads tier field from SKILL.md frontmatter."""
    assert registry._read_tier("save-memory") == "always"
    assert registry._read_tier("slack") == "always"
    assert registry._read_tier("code-review") == "lazy"
    assert registry._read_tier("unknown-cool-skill") == "lazy"


def test_tier_missing_skill(registry: SkillRegistry):
    """_read_tier returns lazy for nonexistent skill."""
    assert registry._read_tier("nonexistent-skill") == "lazy"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_cache_hit(registry: SkillRegistry):
    """Second call returns cached result without re-scanning."""
    result1 = registry.generate_compact_registry()
    # Manually check that cache is populated
    assert registry._cache is not None
    result2 = registry.generate_compact_registry()
    assert result1 == result2


def test_cache_invalidation(registry: SkillRegistry, skills_dir: Path):
    """Adding new skill invalidates cache."""
    result1 = registry.generate_compact_registry()
    # Add a new skill
    new_skill = skills_dir / "s_new-skill"
    new_skill.mkdir()
    (new_skill / "SKILL.md").write_text("# New skill")
    result2 = registry.generate_compact_registry()
    assert "new-skill" in result2
    assert result1 != result2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_dir(empty_dir: Path):
    """Empty skills dir returns empty string."""
    registry = SkillRegistry(skills_dir=empty_dir)
    output = registry.generate_compact_registry()
    assert output == ""
