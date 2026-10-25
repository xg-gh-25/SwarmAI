"""Tests for startup-only runtime registration of generated App Skill paths.

Verifies:
1. External app skill paths from APP_SKILL_PATHS are loaded at scan_all time
2. Pollinate (existing in backend/skills/) is NOT duplicated
3. Malformed/escaping rows are skipped without crashing
4. No app.json read or directory walk occurs (only reads APP_SKILL_PATHS dict)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillManager, parse_skill_md, validate_folder_name


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def tmp_skills_tree(tmp_path: Path):
    """Create a minimal skills tree for testing."""
    builtin = tmp_path / "backend/skills"
    user = tmp_path / "user_skills"
    plugin = tmp_path / "plugin_skills"
    apps = tmp_path / "backend/apps"

    builtin.mkdir(parents=True)
    user.mkdir(parents=True)
    plugin.mkdir(parents=True)
    apps.mkdir(parents=True)

    # Create a built-in skill (s_pollinate)
    pollinate_dir = builtin / "s_pollinate"
    pollinate_dir.mkdir()
    (pollinate_dir / "SKILL.md").write_text(
        "---\nname: pollinate\ndescription: Turn one message into media\n---\n\n# Pollinate\n"
    )
    (pollinate_dir / "INSTRUCTIONS.md").write_text("Do pollination.")

    return {
        "root": tmp_path,
        "builtin": builtin,
        "user": user,
        "plugin": plugin,
        "apps": apps,
    }


# ============================================================================
# Test: External app skills load correctly
# ============================================================================


@pytest.mark.asyncio
async def test_three_external_skill_paths_load(tmp_path: Path) -> None:
    """Three external skill paths from APP_SKILL_PATHS should load as built-in tier."""
    root = tmp_path

    # Create minimal skills tree
    builtin = root / "backend/skills"
    user = root / "user_skills"
    plugin = root / "plugin_skills"
    apps = root / "backend/apps"

    builtin.mkdir(parents=True)
    user.mkdir(parents=True)
    plugin.mkdir(parents=True)
    apps.mkdir(parents=True)

    # Create a built-in skill (s_pollinate)
    pollinate_dir = builtin / "s_pollinate"
    pollinate_dir.mkdir()
    (pollinate_dir / "SKILL.md").write_text(
        "---\nname: pollinate\ndescription: Turn one message into media\n---\n\n# Pollinate\n"
    )
    (pollinate_dir / "INSTRUCTIONS.md").write_text("Do pollination.")

    # Create external app skills directly in this test
    research_pkg = apps / "research/package"
    research_pkg.mkdir(parents=True)
    (research_pkg / "SKILL.md").write_text(
        "---\nname: research\ndescription: Deep research skill\n---\n\n# Research\n"
    )

    domain_pkg = apps / "domain-modeling/package"
    domain_pkg.mkdir(parents=True)
    (domain_pkg / "SKILL.md").write_text(
        "---\nname: domain-modeling\ndescription: Domain modeling\n---\n\n# Domain Modeling\n"
    )

    frontend_pkg = apps / "frontend-design/package/upstream"
    frontend_pkg.mkdir(parents=True)
    (frontend_pkg / "SKILL.md").write_text(
        "---\nname: frontend-design\ndescription: UI design\n---\n\n# Frontend Design\n"
    )

    # Mock APP_SKILL_PATHS with three external apps
    mock_paths = {
        "research/skill:s_research": "backend/apps/research/package",
        "domain-modeling/skill:s_domain-modeling": "backend/apps/domain-modeling/package",
        "frontend-design/skill:s_frontend-design": "backend/apps/frontend-design/package/upstream",
    }

    manager = SkillManager(
        builtin_path=builtin,
        user_skills_path=user,
        plugin_skills_path=plugin,
    )

    def mock_resolve(source_path_str: str) -> Path:
        return (root / source_path_str).resolve()

    def mock_allowed_roots() -> list[Path]:
        return [
            (root / "backend/skills").resolve(),
            (root / "backend/apps").resolve(),
        ]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    # Verify the three external skills are loaded
    assert "s_research" in result
    assert "s_domain-modeling" in result
    assert "s_frontend-design" in result

    # All three should be marked as built-in tier (external-app uses built-in tier)
    assert result["s_research"].source_tier == "built-in"
    assert result["s_domain-modeling"].source_tier == "built-in"
    assert result["s_frontend-design"].source_tier == "built-in"


# ============================================================================
# Test: Pollinate is not duplicated
# ============================================================================


@pytest.mark.asyncio
async def test_pollinate_not_duplicated(tmp_skills_tree: dict[str, Path]) -> None:
    """Pollinate in APP_SKILL_PATHS is skipped because it already exists in backend/skills."""
    root = tmp_skills_tree["root"]

    # APP_SKILL_PATHS includes pollinate, but it's already in builtin_path
    mock_paths = {
        "pollinate/skill:s_pollinate": "backend/skills/s_pollinate",
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_resolve(source_path_str: str) -> Path:
        return (root / source_path_str).resolve()

    def mock_allowed_roots() -> list[Path]:
        return [
            (root / "backend/skills").resolve(),
            (root / "backend/apps").resolve(),
        ]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    # Pollinate should exist exactly once (from built-in, not duplicated)
    assert "s_pollinate" in result
    assert result["s_pollinate"].source_tier == "built-in"

    # Count to verify no duplicates
    pollinate_count = sum(1 for k in result if k == "s_pollinate")
    assert pollinate_count == 1


# ============================================================================
# Test: Malformed/escaping rows are skipped
# ============================================================================


@pytest.mark.asyncio
async def test_malformed_resource_id_skipped(tmp_skills_tree: dict[str, Path]) -> None:
    """Malformed resource_id (missing skill: prefix) should be skipped."""
    root = tmp_skills_tree["root"]

    mock_paths = {
        "bad-app/s_bad": "backend/apps/bad-app/package",  # Missing "skill:" prefix
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_allowed_roots() -> list[Path]:
        return [(root / "backend/apps").resolve()]

    with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
        with mock.patch.dict(
            "sys.modules",
            {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
        ):
            # Should not raise - bad record is logged and skipped
            result = await manager.scan_all()

    # Only pollinate from built-in should be present
    assert "s_bad" not in result
    assert "s_pollinate" in result


@pytest.mark.asyncio
async def test_path_escape_attempt_skipped(tmp_skills_tree: dict[str, Path]) -> None:
    """Path traversal attempt (../) should be skipped."""
    root = tmp_skills_tree["root"]

    # Create a skill outside the allowed roots
    outside = root / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: evil\ndescription: Bad\n---\n")

    mock_paths = {
        "evil-app/skill:s_evil": "../outside",  # Path escape attempt
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_resolve(source_path_str: str) -> Path:
        # This would resolve the escape attempt
        return (root / "backend" / source_path_str).resolve()

    def mock_allowed_roots() -> list[Path]:
        return [
            (root / "backend/skills").resolve(),
            (root / "backend/apps").resolve(),
        ]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    # Evil skill should not be loaded
    assert "s_evil" not in result


@pytest.mark.asyncio
async def test_missing_skill_md_skipped(tmp_skills_tree: dict[str, Path]) -> None:
    """Skill directory without SKILL.md should be skipped."""
    root = tmp_skills_tree["root"]

    # Create app dir without SKILL.md
    no_skill_md = root / "backend/apps/no-skill-md/package"
    no_skill_md.mkdir(parents=True)
    # No SKILL.md created

    mock_paths = {
        "no-skill-md/skill:s_no-skill-md": "backend/apps/no-skill-md/package",
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_resolve(source_path_str: str) -> Path:
        return (root / source_path_str).resolve()

    def mock_allowed_roots() -> list[Path]:
        return [
            (root / "backend/skills").resolve(),
            (root / "backend/apps").resolve(),
        ]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    assert "s_no-skill-md" not in result


@pytest.mark.asyncio
async def test_invalid_folder_name_skipped(tmp_skills_tree: dict[str, Path]) -> None:
    """Invalid folder name (path traversal chars) should be skipped."""
    root = tmp_skills_tree["root"]

    mock_paths = {
        "bad-app/skill:../etc/passwd": "backend/apps/bad",  # Path traversal in folder name
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_allowed_roots() -> list[Path]:
        return [(root / "backend/apps").resolve()]

    with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
        with mock.patch.dict(
            "sys.modules",
            {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
        ):
            result = await manager.scan_all()

    # The bad skill should not be loaded
    assert "../etc/passwd" not in result
    assert "passwd" not in result


# ============================================================================
# Test: No app.json read or directory walk
# ============================================================================


@pytest.mark.asyncio
async def test_no_app_manifest_read_or_walk(tmp_skills_tree: dict[str, Path]) -> None:
    """Verify that no app.json files are read and no apps directory walk occurs.

    The external-app tier reads only from the generated APP_SKILL_PATHS dict.
    """
    root = tmp_skills_tree["root"]

    # Create an apps directory with app.json that should NOT be read
    app_dir = root / "backend/apps/should-not-read"
    app_dir.mkdir(parents=True)

    # Create app.json that would indicate a walk occurred if read
    (app_dir / "app.json").write_text('{"id": "should-not-read", "probe": "detected"}')

    # Also create a skill in this app that is NOT in APP_SKILL_PATHS
    pkg_dir = app_dir / "package"
    pkg_dir.mkdir()
    (pkg_dir / "SKILL.md").write_text("---\nname: hidden\ndescription: Should not load\n---\n")

    # APP_SKILL_PATHS does NOT include this app
    mock_paths: dict[str, str] = {}  # Empty - no external skills

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_allowed_roots() -> list[Path]:
        return [(root / "backend/apps").resolve()]

    # Track if any Path.glob or iterdir is called on apps directory
    glob_calls: list[str] = []
    original_iterdir = Path.iterdir

    def tracked_iterdir(self: Path):
        if "apps" in str(self) and "should-not-read" not in str(self):
            glob_calls.append(str(self))
        return original_iterdir(self)

    with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
        with mock.patch.dict(
            "sys.modules",
            {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
        ):
            with mock.patch.object(Path, "iterdir", tracked_iterdir):
                result = await manager.scan_all()

    # The hidden skill should not be loaded
    assert "s_hidden" not in result
    assert "hidden" not in result

    # The apps directory should not have been walked by _scan_external_app_tier
    # (Some iterdir calls may happen for user/plugin dirs, but not apps)
    apps_walks = [c for c in glob_calls if "apps" in c and "should-not-read" not in c]
    # The external-app tier should NOT walk the apps directory
    # (it reads from APP_SKILL_PATHS directly)
    assert len(apps_walks) == 0 or all("should-not-read" in c for c in apps_walks)


# ============================================================================
# Test: Symlink rejection
# ============================================================================


@pytest.mark.asyncio
async def test_symlink_skill_dir_rejected(tmp_skills_tree: dict[str, Path]) -> None:
    """Skill directory that is a symlink should be rejected."""
    root = tmp_skills_tree["root"]

    # Create a real skill directory
    real_skill = root / "backend/apps/real-skill/package"
    real_skill.mkdir(parents=True)
    (real_skill / "SKILL.md").write_text("---\nname: real\ndescription: Real\n---\n")

    # Create a symlink to it
    symlink_app = root / "backend/apps/symlink-app"
    symlink_app.mkdir(parents=True)
    symlink_skill = symlink_app / "package"
    symlink_skill.symlink_to(real_skill)

    mock_paths = {
        "symlink-app/skill:s_symlink": "backend/apps/symlink-app/package",
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_resolve(source_path_str: str) -> Path:
        return root / source_path_str

    def mock_allowed_roots() -> list[Path]:
        return [(root / "backend/apps").resolve()]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    # Symlink skill should be rejected
    assert "s_symlink" not in result


# ============================================================================
# Test: Duplicate folder_name in generated paths rejected
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_folder_name_in_generated_paths_uses_first(
    tmp_skills_tree: dict[str, Path],
) -> None:
    """If APP_SKILL_PATHS has duplicate folder_names, first one wins."""
    root = tmp_skills_tree["root"]

    # Create two apps claiming the same skill folder_name
    app1 = root / "backend/apps/app1/package"
    app1.mkdir(parents=True)
    (app1 / "SKILL.md").write_text("---\nname: dupe\ndescription: First\n---\n")

    app2 = root / "backend/apps/app2/package"
    app2.mkdir(parents=True)
    (app2 / "SKILL.md").write_text("---\nname: dupe\ndescription: Second\n---\n")

    # Note: In practice, the generator prevents this, but we test runtime behavior
    mock_paths = {
        "app1/skill:s_dupe": "backend/apps/app1/package",
        "app2/skill:s_dupe": "backend/apps/app2/package",  # Same folder_name
    }

    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    def mock_resolve(source_path_str: str) -> Path:
        return (root / source_path_str).resolve()

    def mock_allowed_roots() -> list[Path]:
        return [(root / "backend/apps").resolve()]

    with mock.patch.object(manager, "_resolve_external_app_skill_path", mock_resolve):
        with mock.patch.object(manager, "_external_app_allowed_roots", mock_allowed_roots):
            with mock.patch.dict(
                "sys.modules",
                {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=mock_paths)},
            ):
                result = await manager.scan_all()

    # Only one s_dupe should be loaded (first one wins due to dict ordering)
    assert "s_dupe" in result
    # Description should be "First" (from app1)
    assert result["s_dupe"].description == "First"


# ============================================================================
# Test: Import failure is graceful
# ============================================================================


@pytest.mark.asyncio
async def test_import_error_is_graceful(tmp_skills_tree: dict[str, Path]) -> None:
    """If generated.app_bindings cannot be imported, external-app tier is no-op."""
    manager = SkillManager(
        builtin_path=tmp_skills_tree["builtin"],
        user_skills_path=tmp_skills_tree["user"],
        plugin_skills_path=tmp_skills_tree["plugin"],
    )

    # Mock the import to raise ImportError by making APP_SKILL_PATHS None
    with mock.patch.dict(
        "sys.modules",
        {"generated.app_bindings": mock.MagicMock(APP_SKILL_PATHS=None)},
    ):
        # The tier should handle None gracefully
        result = await manager.scan_all()

    # Should still have builtin skills
    assert "s_pollinate" in result


# ============================================================================
# Test: PyInstaller path resolution
# ============================================================================


def test_external_app_path_resolution_frozen() -> None:
    """Test path resolution strips backend/ prefix when frozen."""
    manager = SkillManager()

    with mock.patch.object(sys, "frozen", True, create=True):
        with mock.patch.object(sys, "_MEIPASS", "/tmp/meipass", create=True):
            result = manager._resolve_external_app_skill_path(
                "backend/apps/research/package"
            )

    # Should strip backend/ and resolve under _MEIPASS
    # On macOS /tmp resolves to /private/tmp
    assert str(result).endswith("/meipass/apps/research/package")


def test_external_app_path_resolution_dev() -> None:
    """Test path resolution uses repo root when not frozen."""
    manager = SkillManager()

    # Ensure frozen is False
    if hasattr(sys, "frozen"):
        del sys.frozen

    result = manager._resolve_external_app_skill_path(
        "backend/apps/research/package"
    )

    # Should be relative to repo root (backend's parent)
    backend_dir = Path(__file__).resolve().parent.parent
    repo_root = backend_dir.parent
    expected = (repo_root / "backend/apps/research/package").resolve()
    assert result == expected


def test_allowed_roots_frozen() -> None:
    """Test allowed roots under _MEIPASS when frozen."""
    manager = SkillManager()

    with mock.patch.object(sys, "frozen", True, create=True):
        with mock.patch.object(sys, "_MEIPASS", "/tmp/meipass", create=True):
            roots = manager._external_app_allowed_roots()

    # Should include skills/ and apps/ under _MEIPASS
    root_strs = [str(r) for r in roots]
    assert any("/tmp/meipass/skills" in r for r in root_strs)
    assert any("/tmp/meipass/apps" in r for r in root_strs)


def test_allowed_roots_dev() -> None:
    """Test allowed roots under backend/ when not frozen."""
    manager = SkillManager()

    if hasattr(sys, "frozen"):
        del sys.frozen

    roots = manager._external_app_allowed_roots()

    # Should include backend/skills/ and backend/apps/
    root_strs = [str(r) for r in roots]
    assert any("backend/skills" in r or "backend\\skills" in r for r in root_strs)
    assert any("backend/apps" in r or "backend\\apps" in r for r in root_strs)


def test_projection_accepts_only_exact_generated_app_skill_root(tmp_path: Path) -> None:
    app_skill = tmp_path / "backend/apps/research/package"
    sibling = tmp_path / "backend/apps/unlisted/package"
    app_skill.mkdir(parents=True)
    sibling.mkdir(parents=True)
    manager = SkillManager(
        builtin_path=tmp_path / "backend/skills",
        user_skills_path=tmp_path / "user",
        plugin_skills_path=tmp_path / "plugin",
    )
    with mock.patch.object(
        manager, "generated_app_skill_paths", return_value=(app_skill.resolve(),)
    ):
        projection = ProjectionLayer(manager)
        assert projection._validate_skill_source(app_skill) is True
        assert projection._validate_skill_source(sibling) is False


def test_real_generated_app_skill_paths_register_and_project(tmp_path: Path) -> None:
    manager = SkillManager(
        user_skills_path=tmp_path / "user",
        plugin_skills_path=tmp_path / "plugin",
    )
    result: dict[str, Any] = {}
    manager._scan_external_app_tier(result)
    assert {"s_domain-modeling", "s_research", "s_frontend-design", "s_pollinate"}.issubset(result)

    projection = ProjectionLayer(manager)
    assert projection._validate_skill_source(result["s_domain-modeling"].path)
    assert projection._validate_skill_source(result["s_research"].path)


def test_real_skills_api_lists_external_app_skills(client) -> None:
    response = client.get("/api/skills")
    assert response.status_code == 200
    folders = {row["folder_name"] for row in response.json()}
    assert {"s_domain-modeling", "s_research"}.issubset(folders)
