"""Tests for ManifestLoader — skill manifest parsing and caching.

Acceptance criteria tested:
- AC1: manifest_loader.py parses manifest.yaml with Pydantic models
- AC4: complex skills have valid manifest.yaml matching directory contents
"""
from __future__ import annotations

import pytest
from pathlib import Path

# These imports will FAIL until we create the module (RED phase)
from core.manifest_loader import ManifestLoader, SkillManifest, ScriptEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def skill_with_manifest(tmp_path: Path) -> Path:
    """Create a skill directory with manifest.yaml and scripts."""
    skill_dir = tmp_path / "s_weekly-report"
    skill_dir.mkdir()
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "generator.py").write_text("# generator")
    (scripts_dir / "data.py").write_text("# data fetcher")
    (skill_dir / "SKILL.md").write_text("---\nname: weekly-report\ntier: always\n---\n# Weekly Report")
    (skill_dir / "manifest.yaml").write_text(
        "name: weekly-report\n"
        "version: '4.0.0'\n"
        "tier: always\n"
        "scripts:\n"
        "  - path: scripts/generator.py\n"
        "    description: Generate HTML report\n"
        "    entry: true\n"
        "    args: '--scope {scope}'\n"
        "  - path: scripts/data.py\n"
        "    description: Fetch revenue data\n"
        "dependencies:\n"
        "  python:\n"
        "    - cairosvg\n"
        "    - openpyxl\n"
        "timeout: 300\n"
    )
    return skill_dir


@pytest.fixture
def skill_without_manifest(tmp_path: Path) -> Path:
    """Create a simple skill with no manifest."""
    skill_dir = tmp_path / "s_weather"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: weather\ntier: lazy\n---\n# Weather")
    return skill_dir


@pytest.fixture
def skill_with_bad_manifest(tmp_path: Path) -> Path:
    """Create a skill with invalid manifest.yaml."""
    skill_dir = tmp_path / "s_broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: broken\n---\n# Broken")
    (skill_dir / "manifest.yaml").write_text("this is not valid yaml: [[[")
    return skill_dir


# ---------------------------------------------------------------------------
# AC1: ManifestLoader parses manifest.yaml
# ---------------------------------------------------------------------------

def test_load_manifest_success(skill_with_manifest: Path):
    """Loads and parses a valid manifest.yaml into SkillManifest."""
    ManifestLoader.invalidate()
    manifest = ManifestLoader.load(skill_with_manifest)
    assert manifest is not None
    assert isinstance(manifest, SkillManifest)
    assert manifest.name == "weekly-report"
    assert manifest.version == "4.0.0"
    assert manifest.tier == "always"
    assert manifest.timeout == 300


def test_load_manifest_scripts(skill_with_manifest: Path):
    """Parses script entries with path, description, entry, args."""
    ManifestLoader.invalidate()
    manifest = ManifestLoader.load(skill_with_manifest)
    assert len(manifest.scripts) == 2
    entry = manifest.get_entry_script()
    assert entry is not None
    assert entry.path == "scripts/generator.py"
    assert entry.entry is True
    assert entry.args == "--scope {scope}"


def test_load_manifest_dependencies(skill_with_manifest: Path):
    """Parses dependency declarations."""
    ManifestLoader.invalidate()
    manifest = ManifestLoader.load(skill_with_manifest)
    assert "python" in manifest.dependencies
    assert "cairosvg" in manifest.dependencies["python"]
    assert "openpyxl" in manifest.dependencies["python"]


def test_load_no_manifest(skill_without_manifest: Path):
    """Returns None when no manifest.yaml exists."""
    ManifestLoader.invalidate()
    result = ManifestLoader.load(skill_without_manifest)
    assert result is None


def test_load_bad_manifest(skill_with_bad_manifest: Path):
    """Returns None and logs warning for invalid YAML."""
    ManifestLoader.invalidate()
    result = ManifestLoader.load(skill_with_bad_manifest)
    assert result is None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_manifest_cache_hit(skill_with_manifest: Path):
    """Second load returns cached instance."""
    ManifestLoader.invalidate()
    m1 = ManifestLoader.load(skill_with_manifest)
    m2 = ManifestLoader.load(skill_with_manifest)
    assert m1 is m2  # Same object from cache


def test_manifest_cache_invalidate(skill_with_manifest: Path):
    """Invalidate clears cache, next load re-reads."""
    ManifestLoader.invalidate()
    m1 = ManifestLoader.load(skill_with_manifest)
    ManifestLoader.invalidate(skill_with_manifest)
    m2 = ManifestLoader.load(skill_with_manifest)
    assert m1 is not m2  # Different objects after invalidation
    assert m1.name == m2.name  # But same content


# ---------------------------------------------------------------------------
# Script index generation
# ---------------------------------------------------------------------------

def test_generate_script_index(skill_with_manifest: Path):
    """Generates human-readable script index."""
    ManifestLoader.invalidate()
    manifest = ManifestLoader.load(skill_with_manifest)
    index = manifest.generate_script_index(skill_with_manifest)
    assert "scripts/generator.py" in index
    assert "[ENTRY]" in index
    assert "scripts/data.py" in index
    assert "exists" in index  # Files actually exist in fixture


def test_script_index_missing_file(skill_with_manifest: Path):
    """Flags MISSING for scripts not on disk."""
    ManifestLoader.invalidate()
    manifest = ManifestLoader.load(skill_with_manifest)
    # Remove a script file
    (skill_with_manifest / "scripts" / "data.py").unlink()
    index = manifest.generate_script_index(skill_with_manifest)
    assert "MISSING" in index


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_manifest_defaults():
    """SkillManifest has sensible defaults."""
    m = SkillManifest(name="test")
    assert m.version == "1.0.0"
    assert m.tier == "lazy"
    assert m.scripts == []
    assert m.timeout == 120
    assert m.get_entry_script() is None


def test_entry_script_fallback():
    """get_entry_script returns first script if none marked entry."""
    m = SkillManifest(
        name="test",
        scripts=[
            ScriptEntry(path="a.py", description="first"),
            ScriptEntry(path="b.py", description="second"),
        ],
    )
    entry = m.get_entry_script()
    assert entry.path == "a.py"
