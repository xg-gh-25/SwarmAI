"""Preservation property tests for explorer git status & skill projection.

This module tests behaviors that are CORRECT on the current (unfixed) code and
must remain correct after the bugfix is applied.  All tests here follow the
observation-first methodology: they encode existing working behavior so that
regressions are caught when the fix is implemented.

Testing methodology: property-based (hypothesis) and unit tests.

Key properties / invariants being verified:

- **2a** — Regular file git status: ``_build_tree()`` assigns the correct
  status to file nodes via the existing ``rel_path in git_status`` check.
- **2b** — Child-prefix directory status: ``_build_tree()`` assigns
  ``"modified"`` to directory nodes when child paths exist in ``git_status``
  with the ``prefix + "/"`` pattern.
- **2c** — Skill tier precedence: ``project_skills()`` projects built-in
  skills unconditionally and gates user/plugin skills correctly.
- **2d** — Git status code parsing: ``_get_git_status()`` maps all valid
  porcelain status codes to the correct status strings.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Literal

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from routers.workspace_api import _build_tree, _get_git_status
from core.projection_layer import ProjectionLayer
from core.skill_manager import SkillInfo

# Reuse helpers from the bug condition test module
from tests.test_property_explorer_git_bugfix import (
    create_dir_on_disk,
    find_node_by_path,
    create_fake_skill_manager,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid path segments for generating random file/directory names
_path_segment = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=12,
).filter(lambda s: s not in (".", "..") and not s.startswith("-"))

# Git status codes for files
_file_git_status = st.sampled_from([
    "untracked", "modified", "added", "deleted", "renamed",
])

# Git status codes that can appear on directories via prefix scan
_dir_git_status = st.sampled_from(["untracked", "modified", "added"])


# ---------------------------------------------------------------------------
# Test 2a: Regular file git status preservation
# ---------------------------------------------------------------------------


class TestRegularFileGitStatus:
    """Preservation: regular file git status is assigned correctly.

    **Validates: Requirements 3.1**

    ``_build_tree()`` uses ``rel_path in git_status`` for file nodes.
    This check is NOT being changed by the bugfix.  These tests confirm
    the existing behavior so regressions are caught.
    """

    def test_file_gets_correct_status(self, tmp_path: Path):
        """A file with a git status entry receives that status.

        **Validates: Requirements 3.1**
        """
        create_dir_on_disk(tmp_path, "src")
        (tmp_path / "src" / "main.py").write_text("print('hello')")

        git_status = {"src/main.py": "modified"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, "src/main.py")
        assert node is not None, "src/main.py node should exist"
        assert node.get("git_status") == "modified"

    def test_file_without_status_has_no_git_status_key(self, tmp_path: Path):
        """A file NOT in git_status should have no git_status key.

        **Validates: Requirements 3.1**
        """
        (tmp_path / "clean.txt").write_text("clean")

        git_status = {"other.txt": "modified"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, "clean.txt")
        assert node is not None, "clean.txt node should exist"
        assert "git_status" not in node, (
            "File not in git_status should have no git_status key"
        )

    @given(
        file_name=_path_segment.map(lambda s: s + ".py"),
        status_code=_file_git_status,
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_file_status_assigned(
        self, tmp_path: Path, file_name: str, status_code: str,
    ):
        """Property: for all (file_name, status), file node gets that status.

        **Validates: Requirements 3.1**
        """
        import uuid
        example_dir = tmp_path / str(uuid.uuid4())
        example_dir.mkdir()

        (example_dir / file_name).write_text("content")
        git_status = {file_name: status_code}

        tree = _build_tree(
            root=example_dir,
            workspace_root=example_dir,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, file_name)
        assert node is not None, f"Node {file_name} should exist"
        assert node.get("git_status") == status_code, (
            f"Expected git_status={status_code!r}, "
            f"got {node.get('git_status')!r}"
        )


# ---------------------------------------------------------------------------
# Test 2b: Child-prefix directory status preservation
# ---------------------------------------------------------------------------


class TestChildPrefixDirectoryStatus:
    """Preservation: directories get "modified" from child prefix matches.

    **Validates: Requirements 3.2**

    ``_build_tree()`` scans ``git_status`` for paths starting with
    ``rel_path + "/"``.  If any match, the directory gets
    ``git_status = "modified"``.  This prefix scan is NOT being changed.
    These tests confirm the existing behavior.
    """

    def test_directory_gets_modified_from_child_file(self, tmp_path: Path):
        """Directory with a child file in git_status gets "modified".

        **Validates: Requirements 3.2**
        """
        create_dir_on_disk(tmp_path, "src")
        (tmp_path / "src" / "app.py").write_text("code")

        git_status = {"src/app.py": "modified"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        dir_node = find_node_by_path(tree, "src")
        assert dir_node is not None, "src directory should exist"
        assert dir_node.get("git_status") == "modified", (
            "Directory with modified child should get 'modified'"
        )

    def test_nested_directory_gets_modified_from_deep_child(
        self, tmp_path: Path,
    ):
        """Nested directory gets "modified" from a deeply nested child.

        **Validates: Requirements 3.2**
        """
        create_dir_on_disk(tmp_path, "a/b/c")
        (tmp_path / "a" / "b" / "c" / "file.txt").write_text("data")

        git_status = {"a/b/c/file.txt": "added"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        # All ancestor directories should get "modified"
        for dir_path in ("a", "a/b", "a/b/c"):
            node = find_node_by_path(tree, dir_path)
            assert node is not None, f"{dir_path} should exist"
            assert node.get("git_status") == "modified", (
                f"Directory {dir_path} should get 'modified' from "
                f"child prefix match"
            )

    def test_directory_without_child_status_has_no_status(
        self, tmp_path: Path,
    ):
        """Directory with no children in git_status has no git_status key.

        **Validates: Requirements 3.2**
        """
        create_dir_on_disk(tmp_path, "clean_dir")
        (tmp_path / "clean_dir" / "file.txt").write_text("clean")

        git_status = {"other/file.txt": "modified"}

        tree = _build_tree(
            root=tmp_path,
            workspace_root=tmp_path,
            depth=5,
            git_status=git_status,
        )

        node = find_node_by_path(tree, "clean_dir")
        assert node is not None, "clean_dir should exist"
        assert "git_status" not in node, (
            "Directory with no child status should have no git_status"
        )

    @given(
        dir_name=_path_segment,
        child_name=_path_segment.map(lambda s: s + ".txt"),
        child_status=_dir_git_status,
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_child_prefix_gives_modified(
        self,
        tmp_path: Path,
        dir_name: str,
        child_name: str,
        child_status: str,
    ):
        """Property: for all dirs with child-prefix entries, dir gets "modified".

        **Validates: Requirements 3.2**

        Generate random trees with ONLY child-prefix entries (no flat-path
        directory entries) and verify the directory gets "modified".
        """
        import uuid
        example_dir = tmp_path / str(uuid.uuid4())
        example_dir.mkdir()

        create_dir_on_disk(example_dir, dir_name)
        (example_dir / dir_name / child_name).write_text("content")

        # Only child-prefix entries — no flat-path directory entry
        child_path = f"{dir_name}/{child_name}"
        git_status = {child_path: child_status}

        tree = _build_tree(
            root=example_dir,
            workspace_root=example_dir,
            depth=5,
            git_status=git_status,
        )

        dir_node = find_node_by_path(tree, dir_name)
        assert dir_node is not None, f"Directory {dir_name} should exist"
        assert dir_node.get("git_status") == "modified", (
            f"Directory {dir_name} with child-prefix entry "
            f"{child_path}={child_status} should get 'modified', "
            f"got {dir_node.get('git_status')!r}"
        )


# ---------------------------------------------------------------------------
# Test 2c: Skill tier precedence preservation
# ---------------------------------------------------------------------------


class TestSkillTierPrecedence:
    """Preservation: project_skills() respects tier precedence.

    **Validates: Requirements 3.4**

    Built-in skills are ALWAYS projected unconditionally.  User and
    plugin skills are gated by ``allowed_skills`` / ``allow_all``.
    This tier logic is NOT being changed by the bugfix (only the
    symlink→copytree mechanism changes).
    """

    @pytest.mark.asyncio
    async def test_builtin_always_projected(self, tmp_path: Path):
        """Built-in skills are projected regardless of allowed_skills.

        **Validates: Requirements 3.4**
        """
        workspace = tmp_path / "SwarmWS"
        workspace.mkdir()

        manager = create_fake_skill_manager(
            tmp_path, ["s_builtin_a", "s_builtin_b"],
            source_tier="built-in",
        )
        layer = ProjectionLayer(manager)

        # Empty allowed_skills, allow_all=False — built-ins still projected
        await layer.project_skills(
            workspace, allowed_skills=[], allow_all=False,
        )

        skills_dir = workspace / ".claude" / "skills"
        projected = {e.name for e in skills_dir.iterdir()}
        assert "s_builtin_a" in projected
        assert "s_builtin_b" in projected

    @pytest.mark.asyncio
    async def test_user_skill_gated_by_allowed_skills(self, tmp_path: Path):
        """User skills only projected when in allowed_skills list.

        **Validates: Requirements 3.4**
        """
        workspace = tmp_path / "SwarmWS"
        workspace.mkdir()

        # Create a mixed cache: one built-in, two user skills
        builtin_dir = tmp_path / "builtin_skills"
        user_dir = tmp_path / "user_skills"
        plugin_dir = tmp_path / "plugin_skills"
        for d in (builtin_dir, user_dir, plugin_dir):
            d.mkdir(parents=True, exist_ok=True)

        cache = {}
        # Built-in skill
        bi_path = builtin_dir / "s_builtin"
        bi_path.mkdir()
        (bi_path / "SKILL.md").write_text("---\nname: s_builtin\n---\n")
        cache["s_builtin"] = SkillInfo(
            folder_name="s_builtin", name="s_builtin",
            description="test", version="1.0.0",
            source_tier="built-in", path=bi_path,
        )
        # User skills
        for name in ("s_user_a", "s_user_b"):
            u_path = user_dir / name
            u_path.mkdir()
            (u_path / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            cache[name] = SkillInfo(
                folder_name=name, name=name,
                description="test", version="1.0.0",
                source_tier="user", path=u_path,
            )

        mgr = MagicMock()
        mgr.builtin_path = builtin_dir
        mgr.user_skills_path = user_dir
        mgr.plugin_skills_path = plugin_dir
        mgr.get_cache = AsyncMock(return_value=cache)

        layer = ProjectionLayer(mgr)

        # Only allow s_user_a
        await layer.project_skills(
            workspace, allowed_skills=["s_user_a"], allow_all=False,
        )

        skills_dir = workspace / ".claude" / "skills"
        projected = {e.name for e in skills_dir.iterdir()}
        assert "s_builtin" in projected, "Built-in always projected"
        assert "s_user_a" in projected, "Allowed user skill projected"
        assert "s_user_b" not in projected, (
            "Non-allowed user skill should NOT be projected"
        )

    @pytest.mark.asyncio
    async def test_allow_all_projects_everything(self, tmp_path: Path):
        """allow_all=True projects all skills from every tier.

        **Validates: Requirements 3.4**
        """
        workspace = tmp_path / "SwarmWS"
        workspace.mkdir()

        builtin_dir = tmp_path / "builtin_skills"
        user_dir = tmp_path / "user_skills"
        plugin_dir = tmp_path / "plugin_skills"
        for d in (builtin_dir, user_dir, plugin_dir):
            d.mkdir(parents=True, exist_ok=True)

        cache = {}
        tiers = [
            ("s_bi", "built-in", builtin_dir),
            ("s_usr", "user", user_dir),
            ("s_plg", "plugin", plugin_dir),
        ]
        for name, tier, parent in tiers:
            p = parent / name
            p.mkdir()
            (p / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            cache[name] = SkillInfo(
                folder_name=name, name=name,
                description="test", version="1.0.0",
                source_tier=tier, path=p,
            )

        mgr = MagicMock()
        mgr.builtin_path = builtin_dir
        mgr.user_skills_path = user_dir
        mgr.plugin_skills_path = plugin_dir
        mgr.get_cache = AsyncMock(return_value=cache)

        layer = ProjectionLayer(mgr)
        await layer.project_skills(workspace, allow_all=True)

        skills_dir = workspace / ".claude" / "skills"
        projected = {e.name for e in skills_dir.iterdir()}
        assert projected == {"s_bi", "s_usr", "s_plg"}, (
            f"allow_all=True should project all skills, got {projected}"
        )

    @given(
        builtin_names=st.frozensets(
            _path_segment.map(lambda s: f"s_{s}"), min_size=1, max_size=4,
        ),
        user_names=st.frozensets(
            _path_segment.map(lambda s: f"s_u_{s}"), min_size=0, max_size=3,
        ),
        allowed_subset=st.data(),
        allow_all=st.booleans(),
    )
    @settings(
        max_examples=20,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @pytest.mark.asyncio
    async def test_property_tier_precedence(
        self,
        tmp_path: Path,
        builtin_names: frozenset[str],
        user_names: frozenset[str],
        allowed_subset: st.DataObject,
        allow_all: bool,
    ):
        """Property: built-ins always projected; user gated by allow_all/list.

        **Validates: Requirements 3.4**
        """
        import uuid

        # Ensure no overlap between built-in and user names
        assume(not builtin_names & user_names)

        example_dir = tmp_path / str(uuid.uuid4())
        example_dir.mkdir()
        workspace = example_dir / "SwarmWS"
        workspace.mkdir()

        builtin_dir = example_dir / "builtin_skills"
        user_dir = example_dir / "user_skills"
        plugin_dir = example_dir / "plugin_skills"
        for d in (builtin_dir, user_dir, plugin_dir):
            d.mkdir(parents=True, exist_ok=True)

        cache = {}
        for name in builtin_names:
            p = builtin_dir / name
            p.mkdir(exist_ok=True)
            (p / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            cache[name] = SkillInfo(
                folder_name=name, name=name,
                description="t", version="1.0.0",
                source_tier="built-in", path=p,
            )
        for name in user_names:
            p = user_dir / name
            p.mkdir(exist_ok=True)
            (p / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
            cache[name] = SkillInfo(
                folder_name=name, name=name,
                description="t", version="1.0.0",
                source_tier="user", path=p,
            )

        # Draw a random subset of user names as allowed
        user_list = sorted(user_names)
        allowed = allowed_subset.draw(
            st.frozensets(st.sampled_from(user_list))
            if user_list
            else st.just(frozenset()),
        )

        mgr = MagicMock()
        mgr.builtin_path = builtin_dir
        mgr.user_skills_path = user_dir
        mgr.plugin_skills_path = plugin_dir
        mgr.get_cache = AsyncMock(return_value=cache)

        layer = ProjectionLayer(mgr)
        await layer.project_skills(
            workspace,
            allowed_skills=list(allowed),
            allow_all=allow_all,
        )

        skills_dir = workspace / ".claude" / "skills"
        projected = {
            e.name for e in skills_dir.iterdir()
        } if skills_dir.exists() else set()

        # Built-in skills ALWAYS projected
        for name in builtin_names:
            assert name in projected, (
                f"Built-in skill {name} must always be projected"
            )

        # User skills: gated
        for name in user_names:
            if allow_all or name in allowed:
                assert name in projected, (
                    f"User skill {name} should be projected "
                    f"(allow_all={allow_all}, allowed={allowed})"
                )
            else:
                assert name not in projected, (
                    f"User skill {name} should NOT be projected "
                    f"(allow_all={allow_all}, allowed={allowed})"
                )


# ---------------------------------------------------------------------------
# Test 2d: Git status code parsing preservation
# ---------------------------------------------------------------------------


# Mapping of porcelain XY codes to expected status strings.
# This encodes the parsing logic in _get_git_status() so regressions
# are caught if the parsing is accidentally changed.
_PORCELAIN_CASES: list[tuple[str, str, str]] = [
    # (xy_code, description, expected_status)
    ("??", "untracked file", "untracked"),
    ("!!", "ignored file", "ignored"),
    ("M ", "modified in index", "modified"),
    (" M", "modified in worktree", "modified"),
    ("MM", "modified in both", "modified"),
    ("A ", "added to index", "added"),
    ("AM", "added then modified", "added"),
    (" D", "deleted in worktree", "deleted"),
    ("D ", "deleted in index", "deleted"),
    ("UU", "both modified (conflict)", "conflicting"),
    ("AA", "both added (conflict)", "conflicting"),
    ("DD", "both deleted (conflict)", "conflicting"),
    ("AU", "added by us (conflict)", "conflicting"),
    ("UA", "added by them (conflict)", "conflicting"),
    ("DU", "deleted by us (conflict)", "conflicting"),
    ("UD", "deleted by them (conflict)", "conflicting"),
    ("R ", "renamed in index", "renamed"),
    (" T", "type changed in worktree", "modified"),
    ("T ", "type changed in index", "modified"),
]


class TestGitStatusCodeParsing:
    """Preservation: _get_git_status() maps porcelain codes correctly.

    **Validates: Requirements 3.6**

    The parsing logic in ``_get_git_status()`` converts two-character
    porcelain XY codes into our status strings.  This logic is NOT
    being changed.  These tests confirm the existing mapping.

    We test by mocking ``subprocess.run`` to return crafted porcelain
    output and verifying the parsed dict.
    """

    @pytest.mark.parametrize(
        "xy_code,description,expected_status",
        _PORCELAIN_CASES,
        ids=[c[1] for c in _PORCELAIN_CASES],
    )
    def test_individual_status_code(
        self,
        tmp_path: Path,
        xy_code: str,
        description: str,
        expected_status: str,
    ):
        """Each porcelain XY code maps to the correct status string.

        **Validates: Requirements 3.6**
        """
        # Create a fake .git dir so _get_git_status doesn't bail early
        (tmp_path / ".git").mkdir()

        # Build porcelain -z output: "XY path\0"
        filepath = "test/file.txt"
        # For renames, we need two NUL-separated paths
        if "R" in xy_code:
            porcelain_output = f"{xy_code} old/path.txt\0{filepath}\0"
        else:
            porcelain_output = f"{xy_code} {filepath}\0"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = porcelain_output

        with patch(
            "routers.workspace_api.subprocess.run",
            return_value=mock_result,
        ):
            result = _get_git_status(tmp_path)

        assert filepath in result, (
            f"File should be in result for code {xy_code!r}"
        )
        assert result[filepath] == expected_status, (
            f"Code {xy_code!r} ({description}) should map to "
            f"{expected_status!r}, got {result[filepath]!r}"
        )

    def test_multiple_entries_parsed(self, tmp_path: Path):
        """Multiple entries in porcelain output are all parsed.

        **Validates: Requirements 3.6**
        """
        (tmp_path / ".git").mkdir()

        # Build output with multiple entries
        porcelain_output = (
            "?? new_file.txt\0"
            " M modified_file.py\0"
            "A  added_file.js\0"
            " D deleted_file.rs\0"
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = porcelain_output

        with patch(
            "routers.workspace_api.subprocess.run",
            return_value=mock_result,
        ):
            result = _get_git_status(tmp_path)

        assert result == {
            "new_file.txt": "untracked",
            "modified_file.py": "modified",
            "added_file.js": "added",
            "deleted_file.rs": "deleted",
        }

    def test_no_git_dir_returns_empty(self, tmp_path: Path):
        """Workspace without .git directory returns empty dict.

        **Validates: Requirements 3.6**
        """
        # No .git dir created
        result = _get_git_status(tmp_path)
        assert result == {}

    @given(
        xy_code=st.sampled_from([
            c[0] for c in _PORCELAIN_CASES if "R" not in c[0]
        ]),
        filepath=st.tuples(_path_segment, _path_segment).map(
            lambda t: f"{t[0]}/{t[1]}.txt"
        ),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_property_status_code_mapping(
        self, tmp_path: Path, xy_code: str, filepath: str,
    ):
        """Property: for all (xy_code, filepath), parsing is deterministic.

        **Validates: Requirements 3.6**

        For all valid non-rename porcelain codes and arbitrary file paths,
        _get_git_status() produces a consistent, non-empty mapping.
        """
        import uuid
        example_dir = tmp_path / str(uuid.uuid4())
        example_dir.mkdir()
        (example_dir / ".git").mkdir()

        porcelain_output = f"{xy_code} {filepath}\0"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = porcelain_output

        with patch(
            "routers.workspace_api.subprocess.run",
            return_value=mock_result,
        ):
            result = _get_git_status(example_dir)

        assert filepath in result, (
            f"File {filepath} should be parsed from code {xy_code!r}"
        )
        assert result[filepath] in (
            "untracked", "modified", "added", "deleted",
            "renamed", "conflicting", "ignored",
        ), f"Status should be a known value, got {result[filepath]!r}"
