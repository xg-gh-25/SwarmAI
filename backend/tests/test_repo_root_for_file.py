"""Cross-repo files_touched resolution for run-commit (run_a5999658, Part B).

`_repo_root_for_file` must resolve a files_touched entry to its OWNING git repo:
- an ABSOLUTE path → the repo of its own parent (unchanged, back-compat);
- a RELATIVE path pointing into the SwarmWS workspace → the SwarmWS repo, even
  when the process cwd is a DIFFERENT repo (the swarmai launch repo). Before the
  fix, a relative SwarmWS path resolved against Path.cwd() (swarmai) → the file
  read as "does not exist on disk" → run-commit dropped it → COMPLETE gate blocked
  on uncommitted_source.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.artifact_cli import _repo_root_for_file


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for c in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(root), *c], check=True, capture_output=True)
    (root / "seed").write_text("s")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "s"], check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    launch = tmp_path / "swarmai"
    data = tmp_path / "data"
    workspace = data / "SwarmWS"
    _init_repo(launch)
    _init_repo(workspace)
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.chdir(launch)  # process cwd = the launch repo (like the real pipeline)
    return launch, workspace


def _tl(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    ).stdout.strip()


def test_relative_workspace_path_resolves_to_workspace_repo(repos):
    """A relative path that exists ONLY in the SwarmWS workspace resolves to the
    workspace repo — not the cwd (launch) repo. THE BUG this fixes."""
    launch, workspace = repos
    rel = "agentcore-data-agent-handson/lambdas/handler.py"
    (workspace / "agentcore-data-agent-handson" / "lambdas").mkdir(parents=True)
    (workspace / rel).write_text("# handler\n")

    root = _repo_root_for_file(rel)
    assert root == _tl(workspace), f"expected workspace repo, got {root}"


def test_relative_cwd_path_resolves_to_cwd_repo(repos):
    """A relative path that exists in the cwd (launch) repo resolves there (back-compat)."""
    launch, workspace = repos
    (launch / "backend").mkdir()
    (launch / "backend" / "mod.py").write_text("# m\n")
    root = _repo_root_for_file("backend/mod.py")
    assert root == _tl(launch)


def test_absolute_path_unchanged(repos):
    """Absolute path resolves from its own parent regardless of cwd (unchanged)."""
    launch, workspace = repos
    f = workspace / "abs.py"
    f.write_text("# a\n")
    root = _repo_root_for_file(str(f))
    assert root == _tl(workspace)


def test_relative_nonexistent_falls_back_to_cwd(repos):
    """A relative path that exists in NEITHER repo falls back to the cwd repo root
    (back-compat: a not-yet-created file still resolves, never crashes)."""
    launch, workspace = repos
    root = _repo_root_for_file("does/not/exist.py")
    assert root == _tl(launch)


def test_relative_deleted_tracked_path_resolves_to_the_repo_that_tracks_it(repos):
    """A file deleted from the WORKSPACE repo no longer exists anywhere on disk, so
    the existence probe cannot find its repo — git's index can. Without this the
    deletion binds to the cwd (launch) repo and is dropped there as a ghost."""
    launch, workspace = repos
    rel = "notes/removed.md"
    (workspace / "notes").mkdir()
    (workspace / rel).write_text("x\n")
    subprocess.run(["git", "-C", str(workspace), "add", rel], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "add"], check=True, capture_output=True)
    (workspace / rel).unlink()
    (workspace / "notes").rmdir()

    assert _repo_root_for_file(rel) == _tl(workspace)


def test_absolute_path_with_removed_parent_dir_still_resolves(repos):
    launch, workspace = repos
    f = workspace / "gone_dir" / "f.py"
    assert not f.parent.exists()
    assert _repo_root_for_file(str(f)) == _tl(workspace)
