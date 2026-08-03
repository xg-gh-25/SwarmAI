"""Tests for resolve_path_to_physical() — the extracted resolver helper.

Cycle 1 of run_e626e121 (Canvas-trigger unification, Extract≠Extend). The path
cascade (absolute → project-symlink → direct → bare-name recursive) was inline in
the /workspace/file/resolve HTTP handler; it is extracted into a callable module
helper so the streaming orchestrator can resolve a written file's PHYSICAL absolute
path once, without an HTTP round-trip. The endpoint delegates to the helper.

Behavior-preserving: same cascade, but returns BOTH the workspace-relative display
path AND the physical absolute path (the copy-path directive needs the absolute).
"""
import os
from pathlib import Path

import pytest

from routers.workspace_api import resolve_path_to_physical


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A minimal workspace: a root file + a project symlink to an external repo."""
    (tmp_path / "Knowledge").mkdir()
    (tmp_path / "Knowledge" / "note.md").write_text("hi")
    # external "repo" that a project symlink points at (mirrors Projects/<X> → repo)
    ext = tmp_path.parent / "extrepo"
    ext.mkdir(exist_ok=True)
    (ext / "backend").mkdir(exist_ok=True)
    (ext / "backend" / "foo.py").write_text("x = 1")
    projects = tmp_path / "Projects"
    projects.mkdir()
    link = projects / "MyProj"
    if not link.exists():
        link.symlink_to(ext)
    return tmp_path


def test_direct_workspace_relative(ws: Path):
    r = resolve_path_to_physical("Knowledge/note.md", ws)
    assert r is not None
    assert r["relative"] == "Knowledge/note.md"
    # absolute must be the REAL physical path, not the relative string
    assert os.path.isabs(r["absolute"])
    assert Path(r["absolute"]).read_text() == "hi"


def test_via_project_symlink(ws: Path):
    # agent emits a source-repo-relative path; resolver finds it under Projects/*
    r = resolve_path_to_physical("backend/foo.py", ws)
    assert r is not None
    assert r["relative"] == "Projects/MyProj/backend/foo.py"
    assert os.path.isabs(r["absolute"])
    assert r["absolute"].endswith("/backend/foo.py")


def test_bare_filename_recursive(ws: Path):
    r = resolve_path_to_physical("note.md", ws)
    assert r is not None
    assert r["relative"] == "Knowledge/note.md"
    assert os.path.isabs(r["absolute"])


def test_absolute_path_inside_project_becomes_relative(ws: Path):
    abs_in = str((ws / "Projects" / "MyProj" / "backend" / "foo.py"))
    r = resolve_path_to_physical(abs_in, ws)
    assert r is not None
    assert r["relative"] == "Projects/MyProj/backend/foo.py"
    assert os.path.isabs(r["absolute"])


def test_nonexistent_returns_none(ws: Path):
    assert resolve_path_to_physical("does/not/exist.py", ws) is None


def test_null_byte_returns_none(ws: Path):
    # was HTTP 400; as a plain helper it fails safe → None (no raise on hot path)
    assert resolve_path_to_physical("foo\x00.py", ws) is None


def test_traversal_returns_none(ws: Path):
    assert resolve_path_to_physical("../etc/passwd", ws) is None
