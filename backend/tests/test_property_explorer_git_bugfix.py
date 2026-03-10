"""Bug condition exploration tests for explorer git status & skill projection.

This module tests the fault conditions identified in the explorer-git-skills-diff-fix
bugfix spec.  The tests are written against UNFIXED code and are EXPECTED TO FAIL,
confirming that the bugs exist.  Once the fixes are applied, these same tests validate
the expected behavior.

Testing methodology: property-based (hypothesis) and unit tests.

Key properties / invariants being verified:

- **1a** — ``_build_tree()`` direct-match: directory nodes with flat-path git entries
  receive the correct git status (Bug 1 — currently fails because only prefix scan
  is used).
- **1b** — Symlink vs copytree: ``ProjectionLayer.project_skills()`` creates real
  directory copies, not symlinks (Bug 2 — currently fails because symlink_to is used).
- **1c** — Parent directory propagation: parent directories inherit git status from
  child directories that have flat-path entries (Bug 3 — transitive failure from Bug 1).

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3**
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass
from typing import Literal

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

# Import the function under test
from routers.workspace_api import _build_tree

# Import ProjectionLayer and SkillInfo for Bug 2 test
from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillInfo


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid directory path segments (no dots, slashes, or special chars)
_path_segment = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=12,
).filter(lambda s: s not in (".", "..") and not s.startswith("-"))

# Git status codes that can appear on directories
_git_status_code = st.sampled_from(["untracked", "modified", "added"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_dir_on_disk(root: Path, rel_path: str) -> None:
    """Create a directory at root/rel_path so _build_tree can walk it."""
    full = root / rel_path
    full.mkdir(parents=True, exist_ok=True)


def find_node_by_path(nodes: list[dict], target_path: str) -> dict | None:
    """Recursively search the tree for a node with the given path."""
    for node in nodes:
        if node["path"] == target_path:
            return node
        if node.get("children"):
            found = find_node_by_path(node["children"], target_path)
            if found is not None:
                return found
    return None


def create_fake_skill_manager(
    tmp_path: Path,
    skill_names: list[str],
    source_tier: str = "built-in",
) -> MagicMock:
    """Create a mock SkillManager with fake skills on disk.

    Returns the mock manager and the dict of skill_name -> SkillInfo.
    """
    builtin_dir = tmp_path / "builtin_skills"
    user_dir = tmp_path / "user_skills"
    plugin_dir = tmp_path / "plugin_skills"
    for d in (builtin_dir, user_dir, plugin_dir):
        d.mkdir(parents=True, exist_ok=True)

    cache = {}
    for name in skill_names:
        skill_dir = builtin_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Test skill\nversion: 1.0.0\n---\n\n# {name}\n"
        )
        cache[name] = SkillInfo(
            folder_name=name,
            name=name,
            description="Test skill",
            version="1.0.0",
            source_tier=source_tier,
            path=skill_dir,
        )

    manager = MagicMock(spec=["builtin_path", "user_skills_path",
                               "plugin_skills_path", "get_cache"])
    manager.builtin_path = builtin_dir
    manager.user_skills_path = user_dir
    manager.plugin_skills_path = plugin_dir
    manager.get_cache = AsyncMock(return_value=cache)
    return manager


# ---------------------------------------------------------------------------
# Test 1a: _build_tree() direct-match for directory flat-path git status
# ---------------------------------------------------------------------------


class TestBuildTreeDirectMatch:
    """Bug 1 exploration: _build_tree() misses flat-path directory git entries.

    **Validates: Requirements 1.1, 2.1**

    On unfixed code, ``_build_tree()`` only does a prefix scan
    (``rel_path + "/"``) for directory git status.  When git reports a
    directory as a flat path (e.g. ``"dir/subdir": "untracked"``), the
    prefix scan finds nothing and the directory gets ``git_status = None``.

    These tests MUST FAIL on unfixed code to confirm the bug exists.
    """

    def test_flat_path_directory_gets_status(self, tmp_path: Path):
        """A directory with a flat-path git entry should receive that status.

        **Validates: Requirements 2.1**

        Create dir/subdir on disk, set git_status = {"dir/subdir": "untracked"},
        call _build_tree(). The subdir node should have git_status == "untracked".
        On unfixed code, it gets None because only prefix scan is used.
        """
        # Create the directory structure on disk
        create_dir_on_disk(tmp_path, "dir/subdir")

        git_status = {"dir/subdir": "untracked"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, "dir/subdir")
        assert node is not None, "dir/subdir node should exist in tree"
        assert node.get("git_status") == "untracked", (
            f"Expected git_status='untracked' for flat-path directory entry, "
            f"got git_status={node.get('git_status')!r}. "
            f"Bug 1 confirmed: _build_tree() only does prefix scan, "
            f"missing the direct flat-path match."
        )

    @given(
        dir_name=st.tuples(_path_segment, _path_segment).map(
            lambda t: f"{t[0]}/{t[1]}"
        ),
        status_code=_git_status_code,
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_property_flat_path_directory_status(
        self, tmp_path: Path, dir_name: str, status_code: str,
    ):
        """Property: for all (dir_name, status_code), _build_tree assigns status.

        **Validates: Requirements 2.1**

        For any valid directory path that exists as a direct key in
        git_status (with NO child prefix entries), _build_tree() should
        assign that status to the directory node.
        """
        # Ensure unique tmp dir per hypothesis example
        import uuid
        example_dir = tmp_path / str(uuid.uuid4())
        example_dir.mkdir()

        create_dir_on_disk(example_dir, dir_name)
        git_status = {dir_name: status_code}

        tree = _build_tree(
            root=example_dir,
            workspace_root=example_dir,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, dir_name)
        assert node is not None, f"Node {dir_name} should exist in tree"
        assert node.get("git_status") == status_code, (
            f"Expected git_status={status_code!r} for directory {dir_name}, "
            f"got {node.get('git_status')!r}"
        )


# ---------------------------------------------------------------------------
# Test 1b: ProjectionLayer creates copies, not symlinks
# ---------------------------------------------------------------------------


class TestProjectionLayerCopytree:
    """Bug 2 exploration: ProjectionLayer uses symlinks instead of copies.

    **Validates: Requirements 1.2, 2.2**

    On unfixed code, ``project_skills()`` calls ``symlink_to()`` which
    creates symlinks (git mode 120000).  Git tracks the pointer, not the
    content, so content changes at the target are invisible.

    These tests MUST FAIL on unfixed code to confirm the bug exists.
    """

    @pytest.mark.asyncio
    async def test_projected_skills_are_not_symlinks(self, tmp_path: Path):
        """Projected skill entries should be real directories, not symlinks.

        **Validates: Requirements 2.2**

        Create a SkillManager mock with one built-in skill, call
        project_skills(), and assert the resulting entry under
        .claude/skills/ is NOT a symlink.
        On unfixed code, it IS a symlink (mode 120000).
        """
        workspace = tmp_path / "SwarmWS"
        workspace.mkdir()

        manager = create_fake_skill_manager(
            tmp_path, ["s_test_skill"], source_tier="built-in",
        )
        layer = ProjectionLayer(manager)

        await layer.project_skills(workspace, allow_all=True)

        skill_entry = workspace / ".claude" / "skills" / "s_test_skill"
        assert skill_entry.exists(), (
            "Skill entry should exist after projection"
        )
        assert not skill_entry.is_symlink(), (
            f"Skill entry {skill_entry} should NOT be a symlink. "
            f"Bug 2 confirmed: ProjectionLayer uses symlink_to() "
            f"instead of copytree(). Git tracks the pointer (mode 120000), "
            f"not the content."
        )
        assert skill_entry.is_dir(), (
            f"Skill entry {skill_entry} should be a real directory"
        )

    @pytest.mark.asyncio
    async def test_projected_skill_content_is_copied(self, tmp_path: Path):
        """Projected skill directory should contain real files, not be a symlink.

        **Validates: Requirements 2.2**

        After projection, the directory itself under .claude/skills/
        should NOT be a symlink.  On unfixed code, the directory IS a
        symlink, so even though files inside appear real (symlink is
        transparent), git tracks the directory as mode 120000.
        """
        workspace = tmp_path / "SwarmWS"
        workspace.mkdir()

        manager = create_fake_skill_manager(
            tmp_path, ["s_copy_test"], source_tier="built-in",
        )
        layer = ProjectionLayer(manager)

        await layer.project_skills(workspace, allow_all=True)

        skill_dir = workspace / ".claude" / "skills" / "s_copy_test"
        # The directory itself must not be a symlink
        assert not skill_dir.is_symlink(), (
            f"Skill directory {skill_dir} should NOT be a symlink. "
            f"Bug 2 confirmed: ProjectionLayer creates symlinks."
        )
        # And it should contain the SKILL.md as a real file
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists(), (
            "SKILL.md should exist inside projected skill directory"
        )


# ---------------------------------------------------------------------------
# Test 1c: Parent directory propagation from flat-path child entries
# ---------------------------------------------------------------------------


class TestParentDirectoryPropagation:
    """Bug 3 exploration: parent directory misses status from flat-path children.

    **Validates: Requirements 1.3, 2.3**

    Bug 3 is a transitive consequence of Bug 1.  When a directory has a
    flat-path git entry but no child prefix entries, the directory itself
    gets no status (Bug 1).  If that directory is a top-level entry
    (e.g. ``topdir``) with a flat-path entry ``"topdir": "untracked"``,
    the parent (workspace root) has no parent node to propagate to.

    However, for nested paths like ``.claude/skills/s_code-review``, the
    parent ``.claude/skills/`` actually DOES get status via prefix scan
    because the flat-path entry starts with the parent's prefix.  The
    real propagation failure occurs when the flat-path entry is at a
    level where no ancestor's prefix scan can match it.

    The key test here: the CHILD directory itself must get the specific
    status from its flat-path entry (not just "modified" from prefix
    scan).  On unfixed code, the child gets None.

    These tests MUST FAIL on unfixed code to confirm the bug exists.
    """

    def test_child_gets_specific_status_not_just_parent(
        self, tmp_path: Path,
    ):
        """Child directory with flat-path entry should get its specific status.

        **Validates: Requirements 2.1, 2.3**

        Create parent/child on disk.  Set git_status to
        {"parent/child": "untracked"}.  The child node should have
        git_status="untracked" (the specific status, not just "modified").
        The parent should get "modified" from prefix scan.
        On unfixed code, the child gets None (Bug 1), even though the
        parent correctly gets "modified" from prefix scan.
        """
        create_dir_on_disk(tmp_path, "parent/child")

        git_status = {"parent/child": "untracked"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        # The child should have the specific status from its flat-path entry
        child_node = find_node_by_path(tree, "parent/child")
        assert child_node is not None, (
            "parent/child node should exist in tree"
        )

        assert child_node.get("git_status") == "untracked", (
            f"Child directory with flat-path entry should get its specific "
            f"status 'untracked', got {child_node.get('git_status')!r}. "
            f"Bug 1/3 confirmed: child directory gets None because "
            f"_build_tree() only does prefix scan for directories."
        )

    def test_isolated_directory_flat_path_no_children(
        self, tmp_path: Path,
    ):
        """A directory with flat-path entry and no children on disk gets status.

        **Validates: Requirements 2.1, 2.3**

        Create an empty directory ``solo_dir`` on disk.  Set git_status
        to ``{"solo_dir": "added"}``.  The directory node should have
        git_status="added".  On unfixed code, it gets None.
        """
        create_dir_on_disk(tmp_path, "solo_dir")

        git_status = {"solo_dir": "added"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, "solo_dir")
        assert node is not None, "solo_dir node should exist"
        assert node.get("git_status") == "added", (
            f"Isolated directory with flat-path entry should get 'added', "
            f"got {node.get('git_status')!r}. "
            f"Bug 1 confirmed: prefix scan finds nothing for empty dirs."
        )
