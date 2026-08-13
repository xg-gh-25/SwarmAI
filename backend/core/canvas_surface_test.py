"""Tests for canvas_surface.is_canvas_surfaceable — the Canvas-ONLY predicate that
decides whether a SwarmWS-OUTSIDE file the session touched should surface in the
Canvas rail (run_5d9178bf).

Contract (verified against needs_human_review + Gate-1 WARN #2):
  - It is asked ONLY for paths needs_human_review already rejected (process/None).
  - It surfaces ONLY files OUTSIDE every known tree (inside-tree is
    needs_human_review's job — this predicate must NOT double-classify those).
  - external file in a git repo → CanvasSurface(True, "external-diff", base_ref=<sha>^)
  - external file with NO git repo (plain FS) → CanvasSurface(True, "external-nodiff")
  - external file that git check-ignore says is IGNORED (a gitignored secret in an
    external repo) → CanvasSurface(False, ...) — must NOT leak (Gate-1 WARN #2).
  - never raises (hot path).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.canvas_surface import is_canvas_surfaceable, CanvasSurface


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def swarmws(tmp_path: Path) -> Path:
    ws = tmp_path / "SwarmWS"
    (ws / "Projects").mkdir(parents=True)
    return ws


def test_external_git_repo_file_is_external_diff(tmp_path: Path, swarmws: Path):
    repo = tmp_path / "ext-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    f = repo / "main.py"
    f.write_text("print(1)\n")
    _git(repo, "add", "main.py")
    _git(repo, "commit", "-m", "init")

    v = is_canvas_surfaceable(str(f), swarmws_root=swarmws)
    assert v.surfaceable is True
    assert v.kind == "external-diff"
    assert v.base_ref  # a <sha>^ ref present


def test_plain_fs_file_no_git_is_external_nodiff(tmp_path: Path, swarmws: Path):
    d = tmp_path / "AI-Native"
    d.mkdir()
    f = d / "notes.txt"
    f.write_text("hello\n")

    v = is_canvas_surfaceable(str(f), swarmws_root=swarmws)
    assert v.surfaceable is True
    assert v.kind == "external-nodiff"
    assert v.base_ref is None


def test_gitignored_external_file_does_NOT_surface(tmp_path: Path, swarmws: Path):
    """Gate-1 WARN #2: a gitignored secret in an EXTERNAL repo must NOT leak."""
    repo = tmp_path / "ext-repo2"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("secrets.env\n")
    secret = repo / "secrets.env"
    secret.write_text("KEY=abc\n")

    v = is_canvas_surfaceable(str(secret), swarmws_root=swarmws)
    assert v.surfaceable is False


def test_inside_swarmws_is_not_this_predicates_job(tmp_path: Path, swarmws: Path):
    """A path INSIDE SwarmWS must be declined — needs_human_review owns those.
    is_canvas_surfaceable only handles OUTSIDE-tree files."""
    f = swarmws / "Projects" / "X" / "doc.md"
    f.parent.mkdir(parents=True)
    f.write_text("# doc\n")

    v = is_canvas_surfaceable(str(f), swarmws_root=swarmws)
    assert v.surfaceable is False


def test_never_raises_on_garbage(swarmws: Path):
    for bad in ["", "\x00null", "relative/not/abs.txt"]:
        v = is_canvas_surfaceable(bad, swarmws_root=swarmws)
        assert v.surfaceable is False
