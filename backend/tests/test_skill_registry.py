"""Tests for SkillRegistry — compact skill index for system prompt.

Key public symbols tested:
- ``SkillRegistry``    — Scanner + categorizer
- ``SKILL_CATEGORIES`` — Known category mapping
"""
from __future__ import annotations

import pytest
from pathlib import Path

from core.skill_registry import SkillRegistry


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
        "s_persist": "---\nname: persist\ndescription: >\n  Save to memory.\n  TRIGGER: remember.\ntier: always\n---\n# persist\nA test skill.",
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
    assert "persist" in skills
    assert "code-review" in skills
    assert "deep-research" in skills
    assert len(skills) == 6


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

def test_categorize_known_skills(registry: SkillRegistry):
    """Known skills placed in correct category."""
    skills = ["persist", "code-review", "deep-research", "pptx", "browser-agent"]
    categories = registry._categorize(skills)
    assert "persist" in categories.get("Memory", [])
    assert "code-review" in categories.get("Development", [])
    assert "deep-research" in categories.get("Research", [])
    assert "pptx" in categories.get("Writing", [])
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
    assert "### Memory" in output  # persist
    assert "persist" in output
    assert "code-review" in output


def test_tier_from_frontmatter(registry: SkillRegistry):
    """_read_tier reads tier field from SKILL.md frontmatter."""
    assert registry._read_tier("persist") == "always"
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


# ---------------------------------------------------------------------------
# Visibility comes only from the SKILL.md frontmatter (F5, R5.1)
# ---------------------------------------------------------------------------

@pytest.fixture
def skills_api(tmp_path, monkeypatch):
    """routers.skills over a SkillManager whose tiers are all temp folders."""
    from core.skill_manager import SkillManager
    from routers import skills as api

    manager = SkillManager(builtin_path=tmp_path / "builtin", user_skills_path=tmp_path / "user",
                           plugin_skills_path=tmp_path / "plugin")
    for d in ("builtin", "user", "plugin"):
        (tmp_path / d).mkdir()
    monkeypatch.setattr(api, "skill_manager", manager)

    async def no_projection():
        return None
    monkeypatch.setattr(api, "_trigger_projection", no_projection)
    return api, manager, tmp_path


def _plant(root: Path, folder: str, frontmatter: str = "") -> None:
    (root / folder).mkdir()
    (root / folder / "SKILL.md").write_text(
        f"---\nname: {folder}\ndescription: \"A test skill.\"\n{frontmatter}---\n# {folder}\n",
        encoding="utf-8")


def test_a_name_never_makes_a_skill_internal():
    from core.skill_registry import derive_category, derive_visibility
    for folder in ("s_ivt-notes", "s_cmhk-report", "s_internal-tool", "s_meddpicc-card"):
        assert derive_visibility(folder) == "public"
        assert derive_category(folder) != "Internal"
    assert derive_visibility("s_anything", "internal") == "internal"
    assert derive_category("s_anything", None, "internal") == "Internal"
    assert derive_visibility("s_anything", "bogus") == "public"


def test_a_stranger_creates_s_ivt_notes_and_it_is_public(skills_api, monkeypatch):
    import asyncio
    from schemas.skill import SkillCreateRequest
    api, _manager, tmp = skills_api
    monkeypatch.setenv("SWARMAI_MODE", "hive")            # not the owner
    created = asyncio.run(api.create_skill(SkillCreateRequest(
        folder_name="s_ivt-notes", name="ivt-notes", description="Notes.", content="# Notes\n")))
    assert created.visibility == "public" and created.category != "Internal"
    assert (tmp / "user" / "s_ivt-notes" / "SKILL.md").is_file()
    listed = asyncio.run(api.list_skills())
    assert "s_ivt-notes" in [s.folder_name for s in listed]


def test_frontmatter_internal_stays_hidden_from_non_owners(skills_api, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    api, _manager, tmp = skills_api
    _plant(tmp / "builtin", "s_owner-only", "visibility: internal\n")
    _plant(tmp / "builtin", "s_open")

    monkeypatch.setenv("SWARMAI_MODE", "hive")
    names = [s.folder_name for s in asyncio.run(api.list_skills())]
    assert "s_open" in names and "s_owner-only" not in names
    assert "s_owner-only" not in [s.folder_name for s in asyncio.run(api.rescan_skills())]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_skill("s_owner-only"))
    assert exc.value.status_code == 404

    monkeypatch.setenv("SWARMAI_MODE", "daemon")          # the owner sees it, grouped Internal
    owner = {s.folder_name: s for s in asyncio.run(api.list_skills())}
    assert owner["s_owner-only"].visibility == "internal"
    assert owner["s_owner-only"].category == "Internal"


def test_an_existing_internal_name_gets_the_same_409_as_a_public_one(skills_api, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from schemas.skill import SkillCreateRequest
    api, _manager, tmp = skills_api
    _plant(tmp / "user", "s_secret", "visibility: internal\n")
    _plant(tmp / "user", "s_plain")
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    answers = []
    for folder in ("s_secret", "s_plain"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api.create_skill(SkillCreateRequest(
                folder_name=folder, name=folder, description="x", content="# x\n")))
        answers.append((exc.value.status_code, exc.value.detail.replace(folder, "<name>")))
    assert answers[0] == answers[1] and answers[0][0] == 409
