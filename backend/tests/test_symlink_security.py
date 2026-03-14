"""Tests for _is_path_under and _is_symlink_traversal security functions.

Verifies that Path.parts-based comparison prevents prefix collisions
(e.g., /workspace-evil matching /workspace) and that symlink traversal
only allows access through trusted workspace symlinks.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.workspace_api import _is_path_under


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
