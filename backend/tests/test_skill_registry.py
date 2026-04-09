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
    """Create a fake skills directory with some SKILL.md files."""
    for name in ["s_save-memory", "s_code-review", "s_deep-research",
                  "s_slack", "s_browser-agent", "s_unknown-cool-skill"]:
        skill_path = tmp_path / name
        skill_path.mkdir()
        (skill_path / "SKILL.md").write_text(f"# {name}\nA test skill.")
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
    """Output matches expected markdown format."""
    output = registry.generate_compact_registry()
    assert "## Available Skills" in output
    assert "### Memory" in output or "### Development" in output
    # Should contain some skill names
    assert "save-memory" in output
    assert "code-review" in output


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
