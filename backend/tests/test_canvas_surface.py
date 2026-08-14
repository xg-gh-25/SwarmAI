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

import os
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


@pytest.fixture
def no_noise_denylist(monkeypatch):
    """Opt OUT of the canvas_noise root denylist for the fixtures below.

    Every one of these tests stands in for a file under a REAL user location
    (``~/Desktop/AI-Native/notes.txt``, an external git repo), but pytest's
    ``tmp_path`` lives under the macOS per-user ``$TMPDIR``
    (``/var/folders/**/T/pytest-of-*``), which ``canvas_noise`` denies as OS temp. So
    the root table is emptied here — otherwise these would assert the DENYLIST's
    behavior, not the git/tree classification they exist to pin.

    The gate's own wiring is verified WITHOUT this fixture in
    ``test_temp_scratch_never_surfaces_even_outside_every_tree`` below, and its
    shape rules in ``test_canvas_noise.py``.
    """
    monkeypatch.setattr("core.canvas_noise._noise_roots", lambda: ())


def test_external_git_repo_file_is_external_diff(tmp_path: Path, swarmws: Path, no_noise_denylist):
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


def test_plain_fs_file_no_git_is_external_nodiff(tmp_path: Path, swarmws: Path, no_noise_denylist):
    d = tmp_path / "AI-Native"
    d.mkdir()
    f = d / "notes.txt"
    f.write_text("hello\n")

    v = is_canvas_surfaceable(str(f), swarmws_root=swarmws)
    assert v.surfaceable is True
    assert v.kind == "external-nodiff"
    assert v.base_ref is None


def test_gitignored_external_file_does_NOT_surface(tmp_path: Path, swarmws: Path, no_noise_denylist):
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


def test_inside_swarmws_is_not_this_predicates_job(tmp_path: Path, swarmws: Path, no_noise_denylist):
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


# ── canvas_noise wiring (NO no_noise_denylist fixture — the real root table) ────

def test_temp_scratch_never_surfaces_even_outside_every_tree(tmp_path: Path, swarmws: Path):
    """The reported bug: agent scratch under OS temp flooded the OUTPUTS rail.

    Such a file is outside every known tree and in no git repo, so the pre-denylist
    predicate returned ``(True, "external-nodiff")`` — a persistent row that also
    auto-popped the Canvas. It must now decline. Uses ``tmp_path`` deliberately: it
    IS a real OS-temp path (``/var/folders/**/T/...`` on macOS, ``/tmp/...`` on
    Linux), which is exactly the shape being denied.
    """
    f = tmp_path / "p1.diff"
    f.write_text("--- a\n+++ b\n")

    v = is_canvas_surfaceable(str(f), swarmws_root=swarmws)
    assert v.surfaceable is False, f"OS-temp scratch must not surface, got {v}"
    assert v.kind is None


def test_compiled_artifact_outside_tree_does_not_surface(monkeypatch, tmp_path: Path, swarmws: Path):
    """A shape-denied file (``.pyc``) declines even when its ROOT is allowed — the
    extension/segment layers are independent of the root table."""
    monkeypatch.setattr("core.canvas_noise._noise_roots", lambda: ())
    d = tmp_path / "AI-Native"
    d.mkdir()
    f = d / "mod.pyc"
    f.write_bytes(b"\x00")

    assert is_canvas_surfaceable(str(f), swarmws_root=swarmws).surfaceable is False


def test_non_regular_file_does_not_surface(monkeypatch, tmp_path: Path, swarmws: Path):
    """A FIFO/socket/device is never a reviewable document (``/private/tmp``'s
    ``dotnet-diagnostic-*-socket`` class). Named without a telltale extension so the
    regular-file check — not the ext denylist — is what declines it."""
    monkeypatch.setattr("core.canvas_noise._noise_roots", lambda: ())
    d = tmp_path / "AI-Native"
    d.mkdir()
    fifo = d / "diagnostic-pipe"
    os.mkfifo(fifo)

    assert is_canvas_surfaceable(str(fifo), swarmws_root=swarmws).surfaceable is False


def test_deleted_external_file_still_surfaces(monkeypatch, tmp_path: Path, swarmws: Path):
    """The regular-file check must be guarded on existence: the DELETE gate calls this
    predicate on an already-removed path to drop its stale rail row. A gone file must
    still classify, or external deletes would leave orphan rows forever."""
    monkeypatch.setattr("core.canvas_noise._noise_roots", lambda: ())
    d = tmp_path / "AI-Native"
    d.mkdir()
    gone = d / "notes.txt"  # never created

    v = is_canvas_surfaceable(str(gone), swarmws_root=swarmws)
    assert v.surfaceable is True
    assert v.kind == "external-nodiff"
