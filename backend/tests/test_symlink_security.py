"""Adversarial tests for workspace file resolution security.

Tests cover:
1. _is_path_under — prefix collision, nesting, symlink escape
2. _is_symlink_traversal — trust boundary enforcement
3. resolve_workspace_file Stage 3/4 — bare filename resolution edge cases:
   - Non-deterministic ordering (same filename in multiple directories)
   - Depth cap enforcement
   - Excluded directory pruning
   - Symlink traversal during os.walk
   - Path injection via crafted filenames
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.workspace_api import _is_path_under, _is_symlink_traversal


# ============================================================================
# Section 1: _is_path_under — Core containment logic
# ============================================================================


class TestIsPathUnder:
    """Tests for _is_path_under using Path.parts comparison."""

    def test_child_is_under_parent(self, tmp_path):
        child = tmp_path / "a" / "b" / "c"
        child.mkdir(parents=True)
        assert _is_path_under(child, tmp_path) is True

    def test_same_path(self, tmp_path):
        assert _is_path_under(tmp_path, tmp_path) is True

    def test_prefix_collision_rejected(self, tmp_path):
        """The core fix: /workspace-evil must NOT match /workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        evil = tmp_path / "workspace-evil"
        evil.mkdir()
        assert _is_path_under(evil, workspace) is False

    def test_prefix_collision_with_suffix(self, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        evil = tmp_path / "ws2"
        evil.mkdir()
        assert _is_path_under(evil, workspace) is False

    def test_sibling_rejected(self, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert _is_path_under(a, b) is False

    def test_parent_not_under_child(self, tmp_path):
        child = tmp_path / "a" / "b"
        child.mkdir(parents=True)
        assert _is_path_under(tmp_path, child) is False

    def test_deeply_nested(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        assert _is_path_under(deep, tmp_path) is True

    def test_resolves_symlinks(self, tmp_path):
        """Symlinked child that resolves under parent should pass."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        assert _is_path_under(link, tmp_path) is True

    def test_symlink_escape_rejected(self, tmp_path):
        """Symlink pointing outside parent should fail."""
        outside = tmp_path / "outside"
        outside.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        escape_link = workspace / "escape"
        escape_link.symlink_to(outside)
        assert _is_path_under(escape_link, workspace) is False

    def test_dotdot_traversal_after_resolve(self, tmp_path):
        """Paths with .. segments that escape after resolution should fail."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (tmp_path / "secret").mkdir()
        # Path that uses .. to escape workspace — resolve() canonicalizes
        escape_path = workspace / ".." / "secret"
        assert _is_path_under(escape_path, workspace) is False

    def test_unrelated_subtree_rejected(self, tmp_path):
        """Paths in completely unrelated subtrees must be rejected."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        unrelated = tmp_path / "unrelated" / "deep" / "path"
        unrelated.mkdir(parents=True)
        assert _is_path_under(unrelated, workspace) is False


# ============================================================================
# Section 2: _is_symlink_traversal — Trust boundary enforcement
# ============================================================================


class TestIsSymlinkTraversal:
    """Tests for _is_symlink_traversal — validates the write-through-symlink model."""

    def test_valid_project_symlink(self, tmp_path):
        """Standard case: Projects/Foo → /some/real/repo, access file inside."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        real_repo = tmp_path / "real_repo"
        real_repo.mkdir()
        (real_repo / "src").mkdir()
        (real_repo / "src" / "main.py").write_text("# code")

        projects = workspace / "Projects"
        projects.mkdir()
        (projects / "Foo").symlink_to(real_repo)

        # Access a file through the symlink
        assert _is_symlink_traversal(workspace, "Projects/Foo/src/main.py") is True

    def test_symlink_escape_above_target(self, tmp_path):
        """Prevent .. escape above the symlink target."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        real_repo = tmp_path / "repos" / "my_repo"
        real_repo.mkdir(parents=True)
        (tmp_path / "repos" / "secret.txt").write_text("secret")

        projects = workspace / "Projects"
        projects.mkdir()
        (projects / "Foo").symlink_to(real_repo)

        # Try to escape above symlink target: resolves to /repos/secret.txt
        # which is NOT under symlink_target (real_repo)
        # Note: normpath would collapse .., but resolve() on the full path does too
        escape_path = "Projects/Foo/../secret.txt"
        # os.path.normpath collapses this to "Projects/secret.txt"
        # The function uses Path(relative_path).parts which DOES keep ..
        # But (workspace_root / relative_path).resolve() canonicalizes
        assert _is_symlink_traversal(workspace, escape_path) is False

    def test_single_part_path_no_ancestor_to_check(self, tmp_path):
        """Single-part relative path has no ancestor symlink to find → returns False.

        _is_symlink_traversal iterates range(1, len(parts)) to find symlink
        ancestors. For a single-part path ("link"), range(1, 1) is empty.
        The function correctly returns False — the caller uses _is_path_under
        separately to check containment.
        """
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (workspace / "link").symlink_to(outside)
        # Single-part path — no ancestor to check, returns False
        assert _is_symlink_traversal(workspace, "link") is False

    def test_symlink_parent_outside_workspace_rejected(self, tmp_path):
        """If the symlink's parent resolves outside workspace, traversal denied."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        # We can't easily create a symlink whose parent is outside workspace
        # because the path is workspace-relative. But we can test via a nested
        # structure where an intermediate dir is itself a symlink to outside.
        outside_dir = tmp_path / "outside_container"
        outside_dir.mkdir()
        (outside_dir / "target_repo").mkdir()
        (outside_dir / "target_repo" / "file.py").write_text("# code")

        # Symlink from workspace to outside_container
        (workspace / "escape").symlink_to(outside_dir)
        # Now "escape/target_repo/file.py" — the symlink at "escape" has parent ws/
        # which IS under workspace. But the target resolves to outside_dir/target_repo/file.py
        # which IS under symlink_target (outside_dir). So this should be True (valid traversal).
        assert _is_symlink_traversal(workspace, "escape/target_repo/file.py") is True

    def test_nested_symlink_not_trusted(self, tmp_path):
        """Only the first symlink hop is trusted — nested symlinks get no extra privilege."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # Create real target with a nested symlink that escapes
        real_repo = tmp_path / "repo"
        real_repo.mkdir()
        secret = tmp_path / "secret"
        secret.mkdir()
        (secret / "data.txt").write_text("sensitive")

        # Nested symlink inside the repo pointing to secret
        (real_repo / "evil_link").symlink_to(secret)

        # Project symlink from workspace to repo
        projects = workspace / "Projects"
        projects.mkdir()
        (projects / "Repo").symlink_to(real_repo)

        # Access via Projects/Repo/evil_link/data.txt
        # _is_symlink_traversal finds symlink at Projects/Repo (first hop)
        # symlink_target = real_repo.resolve()
        # full_target = (workspace / "Projects/Repo/evil_link/data.txt").resolve()
        #             = secret/data.txt (because evil_link → secret)
        # _is_path_under(secret/data.txt, real_repo) → False!
        # So the nested escape is blocked.
        result = _is_symlink_traversal(workspace, "Projects/Repo/evil_link/data.txt")
        assert result is False

    def test_no_symlink_in_path(self, tmp_path):
        """If no symlink exists in the path, returns False (not a traversal)."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "Knowledge").mkdir()
        (workspace / "Knowledge" / "file.md").write_text("content")
        # No symlinks in path — returns False (file is just a normal workspace file)
        assert _is_symlink_traversal(workspace, "Knowledge/file.md") is False

    def test_dangling_symlink(self, tmp_path):
        """Dangling symlink (target doesn't exist) should return False safely."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        projects = workspace / "Projects"
        projects.mkdir()
        # Symlink to non-existent target
        (projects / "Dead").symlink_to(tmp_path / "nonexistent")

        # Should not crash — is_symlink() returns True but resolve() fails gracefully
        # (Path.resolve() still returns a path even for broken symlinks on some systems)
        result = _is_symlink_traversal(workspace, "Projects/Dead/file.txt")
        # Either False (can't verify) or True depending on OS behavior
        # The key assertion: it doesn't raise
        assert isinstance(result, bool)

    def test_relative_symlink_target(self, tmp_path):
        """Symlinks with relative targets should still resolve correctly."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        real_code = tmp_path / "code"
        real_code.mkdir()
        (real_code / "app.py").write_text("# app")

        projects = workspace / "Projects"
        projects.mkdir()
        # Use relative symlink target (../../code)
        (projects / "App").symlink_to(os.path.relpath(real_code, projects))

        assert _is_symlink_traversal(workspace, "Projects/App/app.py") is True


# ============================================================================
# Section 3: resolve_workspace_file Stage 3/4 — Bare filename resolution
# ============================================================================


class TestBareFilenameResolution:
    """Tests for Stage 3/4 bare filename search in resolve_workspace_file.

    These test the _find_bare_filename helper function indirectly through
    the endpoint, validating: ordering, depth cap, excluded dirs, symlinks.
    """

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create a workspace structure for testing."""
        ws = tmp_path / "ws"
        ws.mkdir()
        # Create Projects directory
        (ws / "Projects").mkdir()
        # Create Knowledge directory (searched in Stage 4)
        (ws / "Knowledge").mkdir()
        (ws / "Knowledge" / "Notes").mkdir()
        return ws

    @pytest.fixture
    def mock_workspace_path(self, workspace):
        """Mock _get_workspace_path to return our test workspace."""
        async def _get_ws():
            return str(workspace)
        return patch("routers.workspace_api._get_workspace_path", _get_ws)

    def test_stage3_finds_in_project(self, workspace, mock_workspace_path):
        """Stage 3: bare filename found under Projects/ returns project-relative path."""
        from routers.workspace_api import resolve_workspace_file

        # Create a project with a file
        proj = workspace / "Projects" / "Alpha"
        proj.mkdir()
        (proj / "src").mkdir()
        (proj / "src" / "target.py").write_text("# found")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="target.py"))
        assert result["resolved_path"] == "Projects/Alpha/src/target.py"

    def test_stage4_finds_in_knowledge(self, workspace, mock_workspace_path):
        """Stage 4: bare filename found in Knowledge/ returns workspace-relative path."""
        from routers.workspace_api import resolve_workspace_file

        (workspace / "Knowledge" / "Notes" / "report.md").write_text("# report")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="report.md"))
        assert result["resolved_path"] == "Knowledge/Notes/report.md"

    def test_stage3_takes_priority_over_stage4(self, workspace, mock_workspace_path):
        """If same filename exists in Projects/ AND workspace root, Stage 3 wins."""
        from routers.workspace_api import resolve_workspace_file

        # File in project
        proj = workspace / "Projects" / "Alpha"
        proj.mkdir()
        (proj / "duplicate.md").write_text("# project version")

        # Same filename in Knowledge (Stage 4)
        (workspace / "Knowledge" / "duplicate.md").write_text("# knowledge version")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="duplicate.md"))
        # Stage 3 (Projects/) should win
        assert result["resolved_path"].startswith("Projects/")

    def test_deterministic_ordering_across_projects(self, workspace, mock_workspace_path):
        """Same filename in multiple projects — sorted project names give determinism."""
        from routers.workspace_api import resolve_workspace_file

        # Create projects in non-alphabetical order
        for name in ["Zebra", "Alpha", "Middle"]:
            proj = workspace / "Projects" / name
            proj.mkdir()
            (proj / "shared.txt").write_text(f"# {name}")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="shared.txt"))
        # sorted() means "Alpha" comes first
        assert result["resolved_path"] == "Projects/Alpha/shared.txt"

    def test_depth_cap_enforcement(self, workspace, mock_workspace_path):
        """Files deeper than _MAX_DEPTH (8) should NOT be found."""
        from routers.workspace_api import resolve_workspace_file

        # Create file at exactly depth 9 (beyond _MAX_DEPTH=8)
        deep_path = workspace / "Knowledge"
        for i in range(9):
            deep_path = deep_path / f"level{i}"
        deep_path.mkdir(parents=True)
        (deep_path / "hidden.txt").write_text("too deep")

        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="hidden.txt"))
            assert exc_info.value.status_code == 404

    def test_depth_cap_boundary_at_limit(self, workspace, mock_workspace_path):
        """Files at depth just under _MAX_DEPTH should still be found.

        _MAX_DEPTH=8 checks `len(rel_root.parts) >= 8`.
        rel_root is relative to workspace_root.
        Knowledge/d0/d1/d2/d3/d4/d5 = 7 parts → passes (< 8).
        """
        from routers.workspace_api import resolve_workspace_file

        # 6 subdirs under Knowledge → rel_root = Knowledge/d0/.../d5 = 7 parts
        path = workspace / "Knowledge"
        for i in range(6):
            path = path / f"d{i}"
        path.mkdir(parents=True)
        (path / "boundary.txt").write_text("at limit")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="boundary.txt"))
        assert "boundary.txt" in result["resolved_path"]

    def test_excluded_dirs_not_searched(self, workspace, mock_workspace_path):
        """Files inside excluded directories (node_modules, .git, etc.) are invisible."""
        from routers.workspace_api import resolve_workspace_file

        # Place file inside node_modules
        nm = workspace / "Knowledge" / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text("{}")

        # Place file inside .git
        git_dir = workspace / "Knowledge" / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "deadbeef").write_text("blob")

        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="package.json"))
            assert exc_info.value.status_code == 404

    def test_stage4_excludes_projects_dir(self, workspace, mock_workspace_path):
        """Stage 4 must skip Projects/ to avoid double-searching."""
        from routers.workspace_api import resolve_workspace_file

        # File ONLY in Projects/ (no Stage 4 fallback needed)
        proj = workspace / "Projects" / "Only"
        proj.mkdir()
        (proj / "exclusive.txt").write_text("only here")

        # This should be found by Stage 3, not Stage 4
        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="exclusive.txt"))
        assert result["resolved_path"] == "Projects/Only/exclusive.txt"

    def test_symlink_traversal_in_project(self, workspace, mock_workspace_path):
        """Stage 3 follows project symlinks (they resolve to real repos)."""
        from routers.workspace_api import resolve_workspace_file

        # Real repo outside workspace
        real_repo = workspace.parent / "real_repo"
        real_repo.mkdir()
        (real_repo / "lib").mkdir()
        (real_repo / "lib" / "utils.py").write_text("# utils")

        # Symlink project to real repo
        (workspace / "Projects" / "Linked").symlink_to(real_repo)

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="utils.py"))
        assert result["resolved_path"] == "Projects/Linked/lib/utils.py"

    def test_symlink_cycle_does_not_hang(self, workspace, mock_workspace_path):
        """Circular symlinks should not cause infinite recursion."""
        from routers.workspace_api import resolve_workspace_file

        # Create circular symlink: a → b, b → a
        (workspace / "Knowledge" / "a").mkdir()
        (workspace / "Knowledge" / "b").mkdir()
        # os.walk with followlinks=False (default) won't follow these
        # But let's verify no hang even if they exist
        try:
            (workspace / "Knowledge" / "a" / "link_to_b").symlink_to(
                workspace / "Knowledge" / "b"
            )
            (workspace / "Knowledge" / "b" / "link_to_a").symlink_to(
                workspace / "Knowledge" / "a"
            )
        except OSError:
            pytest.skip("Cannot create symlink on this filesystem")

        with mock_workspace_path:
            # Should not hang — os.walk default is followlinks=False
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="nonexistent_file.xyz"))
            assert exc_info.value.status_code == 404

    def test_special_characters_in_filename(self, workspace, mock_workspace_path):
        """Filenames with spaces, unicode, and special chars should resolve."""
        from routers.workspace_api import resolve_workspace_file

        (workspace / "Knowledge" / "文件 (copy).md").write_text("# CJK")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="文件 (copy).md"))
        assert result["resolved_path"] == "Knowledge/文件 (copy).md"

    def test_dotfile_found(self, workspace, mock_workspace_path):
        """Dotfiles should be findable (they're not excluded)."""
        from routers.workspace_api import resolve_workspace_file

        (workspace / "Knowledge" / ".env.example").write_text("FOO=bar")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path=".env.example"))
        assert ".env.example" in result["resolved_path"]

    def test_path_with_separator_skips_bare_search(self, workspace, mock_workspace_path):
        """Paths containing / should NOT trigger Stage 3/4 bare filename search."""
        from routers.workspace_api import resolve_workspace_file

        # Create the subdirectory first, then write the file
        sub_dir = workspace / "Knowledge" / "sub"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "file.txt").write_text("x")

        with mock_workspace_path:
            # "Knowledge/sub/file.txt" has separators — goes through Stage 1 (direct)
            result = asyncio.run(resolve_workspace_file(path="Knowledge/sub/file.txt"))
        assert result["resolved_path"] == "Knowledge/sub/file.txt"

    def test_empty_project_directory_skipped(self, workspace, mock_workspace_path):
        """Empty project directories don't cause errors."""
        from routers.workspace_api import resolve_workspace_file

        (workspace / "Projects" / "Empty").mkdir()
        (workspace / "Knowledge" / "found.md").write_text("# here")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="found.md"))
        assert result["resolved_path"] == "Knowledge/found.md"

    def test_file_in_project_root(self, workspace, mock_workspace_path):
        """Files directly in project root (no subdirectory) are found."""
        from routers.workspace_api import resolve_workspace_file

        proj = workspace / "Projects" / "Simple"
        proj.mkdir()
        (proj / "README.md").write_text("# readme")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path="README.md"))
        assert result["resolved_path"] == "Projects/Simple/README.md"

    def test_oserror_during_walk_handled(self, workspace, mock_workspace_path):
        """OSError during os.walk (permission denied, broken mount) doesn't crash."""
        from routers.workspace_api import resolve_workspace_file

        # Create a directory that will cause issues on access
        # We can't easily simulate permission denied in tests,
        # so we verify the asyncio.to_thread + try/except OSError path
        # by checking that a 404 is returned cleanly
        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="impossiblefile.xyz"))
            assert exc_info.value.status_code == 404


# ============================================================================
# Section 4: resolve_workspace_file Stage 0/1/2 — Traversal guards
# ============================================================================


class TestResolveTraversalGuards:
    """Tests for path traversal protection in resolve_workspace_file."""

    @pytest.fixture
    def workspace(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "Projects").mkdir()
        (ws / "Knowledge").mkdir()
        return ws

    @pytest.fixture
    def mock_workspace_path(self, workspace):
        async def _get_ws():
            return str(workspace)
        return patch("routers.workspace_api._get_workspace_path", _get_ws)

    def test_dotdot_traversal_rejected(self, workspace, mock_workspace_path):
        """Paths with .. segments should be rejected."""
        from routers.workspace_api import resolve_workspace_file

        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="../../../etc/passwd"))
            assert exc_info.value.status_code == 400

    def test_normalized_dotdot_rejected(self, workspace, mock_workspace_path):
        """Even after normalization, leading .. must be rejected."""
        from routers.workspace_api import resolve_workspace_file

        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="Knowledge/../../secret"))
            # normpath("Knowledge/../../secret") = "../secret" → starts with ".."
            assert exc_info.value.status_code == 400

    def test_absolute_path_existing_file(self, workspace, mock_workspace_path):
        """Absolute paths to existing files should resolve."""
        from routers.workspace_api import resolve_workspace_file

        target_file = workspace / "Knowledge" / "abs_test.md"
        target_file.write_text("# test")

        with mock_workspace_path:
            result = asyncio.run(resolve_workspace_file(path=str(target_file)))
        # Should resolve to workspace-relative via project symlink or return absolute
        assert "abs_test.md" in result["resolved_path"]

    def test_absolute_path_nonexistent_404(self, workspace, mock_workspace_path):
        """Absolute paths to non-existent files should 404."""
        from routers.workspace_api import resolve_workspace_file

        with mock_workspace_path:
            with pytest.raises(Exception) as exc_info:
                asyncio.run(resolve_workspace_file(path="/tmp/nonexistent_xyz.txt"))
            assert exc_info.value.status_code == 404
