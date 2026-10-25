"""Tests for backend/scripts/worktree_reattach.py: re-attach a linked worktree whose
admin entry another session's `git worktree prune` deleted (AGENTS.md "Never prune").

Methodology: real git repos in tmp_path. The incident is replayed: a locked-less
worktree on its own branch with a commit, plus uncommitted, staged and untracked
edits; its directory is made invisible to git (moved aside), `git worktree prune`
runs, the directory is moved back. Invariants after reattach:
  • git works in the worktree again, on the same branch, at the same commit;
  • every file in it is byte-identical (uncommitted edits, untracked files kept;
    the lost index is rebuilt from HEAD, so staged edits show as unstaged, never lost);
  • the worktree is locked; nothing was written outside it and the repo's admin dir;
  • a worktree that is healthy, a path that is not a worktree, and a branch that is
    checked out elsewhere are refused without changing anything.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import worktree_reattach as wr  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def _pruned(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "config", "user.email", "t@t")
    git(main, "config", "user.name", "t")
    (main / "a.py").write_text("v0\n")
    (main / "b.py").write_text("v0\n")
    git(main, "add", ".")
    git(main, "commit", "-qm", "base")
    wt = tmp_path / "scratch" / "wt-topic"
    git(main, "worktree", "add", "-q", "-b", "agent/topic", str(wt), "main")
    (wt / "a.py").write_text("committed\n")
    git(wt, "commit", "-qam", "work")
    (wt / "a.py").write_text("committed + unstaged\n")
    (wt / "b.py").write_text("staged\n")
    git(wt, "add", "b.py")
    (wt / "new.py").write_text("untracked\n")
    # another sandbox cannot see the dir, and prunes
    hidden = tmp_path / "hidden"
    wt.rename(hidden)
    git(main, "worktree", "prune")
    hidden.rename(wt)
    assert git(wt, "status", check=False).returncode != 0   # the incident: broken
    return main, wt


def _files(wt: Path) -> dict[str, bytes]:
    return {str(p.relative_to(wt)): p.read_bytes() for p in sorted(wt.rglob("*"))
            if p.is_file() and p.name != ".git"}


def test_reattach_restores_git_and_keeps_every_file(tmp_path):
    main, wt = _pruned(tmp_path)
    tip = git(main, "rev-parse", "agent/topic").stdout.strip()
    before = _files(wt)
    out = wr.reattach(str(wt), branch="agent/topic")
    assert out["ok"] is True, out
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "agent/topic"
    assert git(wt, "rev-parse", "HEAD").stdout.strip() == tip
    assert _files(wt) == before
    status = git(wt, "status", "--porcelain").stdout
    assert " M a.py" in status and " M b.py" in status and "?? new.py" in status
    listed = git(main, "worktree", "list", "--porcelain").stdout
    assert f"worktree {wt.resolve()}" in listed and "locked" in listed
    assert not [p for p in (tmp_path / "scratch").iterdir() if p != wt]   # no temp left


def test_a_missing_branch_is_refused(tmp_path):
    """The branch name lived in the deleted admin dir; it is never guessed."""
    main, wt = _pruned(tmp_path)
    out = wr.reattach(str(wt), branch="agent/nope")
    assert out["ok"] is False and "no branch" in out["error"], out


def test_a_healthy_worktree_is_left_alone(tmp_path):
    main, wt = _pruned(tmp_path)
    wr.reattach(str(wt), branch="agent/topic")
    out = wr.reattach(str(wt), branch="agent/topic")
    assert out["ok"] is False and "healthy" in out["error"], out


def test_a_path_that_is_not_a_linked_worktree_is_refused(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    out = wr.reattach(str(plain), branch="x")
    assert out["ok"] is False and ".git" in out["error"], out


def test_a_branch_checked_out_elsewhere_is_refused(tmp_path):
    main, wt = _pruned(tmp_path)
    git(main, "worktree", "add", "-q", str(tmp_path / "other"), "agent/topic")
    before = _files(wt)
    out = wr.reattach(str(wt), branch="agent/topic")
    assert out["ok"] is False and "checked out" in out["error"], out
    assert _files(wt) == before and git(wt, "status", check=False).returncode != 0


def test_cli_prints_json(tmp_path, capsys):
    main, wt = _pruned(tmp_path)
    assert wr.main([str(wt), "--branch", "agent/topic"]) == 0
    assert '"ok": true' in capsys.readouterr().out
