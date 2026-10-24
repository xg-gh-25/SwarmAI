"""Tests for backend/scripts/check_no_revert.py — refuse a branch that undoes main.

Methodology: real git repos in tmp_path. The incident is replayed exactly: a branch
made from main, main moves on (another session lands a change), the branch squashes
with ``git add -A && git reset --soft main && git commit`` from its old tree, rebases
and would fast-forward. Invariants: that commit is caught and names exactly the
undone file; a normal squash (``reset --soft $(merge-base)``) is not; a branch that
really edits a file main also edited is not; a branch with no reflog is not judged.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_no_revert as cnr  # noqa: E402


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "mine.py").write_text("v0\n")
    (r / "theirs.py").write_text("v0\n")
    git(r, "add", ".")
    git(r, "commit", "-qm", "base")
    return r


def _branch_then_main_moves(r: Path) -> None:
    git(r, "branch", "feat")
    (r / "theirs.py").write_text("v1 from another session\n")   # lands on main meanwhile
    git(r, "commit", "-qam", "other session")


def _work_on_feat(r: Path) -> None:
    git(r, "checkout", "-q", "feat")
    (r / "mine.py").write_text("v1 mine\n")
    git(r, "commit", "-qam", "wip 1")
    (r / "mine.py").write_text("v2 mine\n")
    git(r, "commit", "-qam", "wip 2")


def test_squash_by_reset_soft_main_from_an_old_tree_is_caught(tmp_path):
    r = _repo(tmp_path)
    _branch_then_main_moves(r)
    _work_on_feat(r)
    git(r, "add", "-A")
    git(r, "reset", "-q", "--soft", "main")          # the incident's squash
    git(r, "commit", "-qm", "squashed")
    git(r, "rebase", "-q", "main")                   # passes: nothing to replay
    assert git(r, "merge-base", "--is-ancestor", "main", "feat") == ""   # ff-only would pass
    assert cnr.reverted_paths(str(r), "feat", "main") == ["theirs.py"]
    assert cnr.main(["feat", "--repo", str(r)]) == 1
    assert cnr.main(["feat", "--repo", str(r), "--allow", "theirs.py"]) == 0


def test_squash_from_the_merge_base_is_not(tmp_path):
    r = _repo(tmp_path)
    _branch_then_main_moves(r)
    _work_on_feat(r)
    git(r, "reset", "-q", "--soft", git(r, "merge-base", "HEAD", "main"))
    git(r, "commit", "-qm", "squashed")
    git(r, "rebase", "-q", "main")
    assert (r / "theirs.py").read_text() == "v1 from another session\n"
    assert cnr.reverted_paths(str(r), "feat", "main") == []
    assert cnr.main(["feat", "--repo", str(r)]) == 0


def test_a_real_edit_of_a_file_main_also_changed_is_not(tmp_path):
    r = _repo(tmp_path)
    _branch_then_main_moves(r)
    git(r, "checkout", "-q", "feat")
    (r / "theirs.py").write_text("v0\nmine below\n")
    git(r, "commit", "-qam", "edit theirs")
    git(r, "checkout", "-q", "main")
    (r / "theirs.py").write_text("v1 from another session\nmine below\n")
    git(r, "commit", "-qam", "resolved by hand")
    git(r, "checkout", "-q", "feat")
    git(r, "reset", "-q", "--hard", "main")
    (r / "theirs.py").write_text("v2 both\n")
    git(r, "commit", "-qam", "both")
    assert cnr.reverted_paths(str(r), "feat", "main") == []


def test_a_resurrected_file_main_deleted_is_caught(tmp_path):
    r = _repo(tmp_path)
    git(r, "branch", "feat")
    git(r, "rm", "-q", "theirs.py")
    git(r, "commit", "-qm", "delete on main")
    git(r, "checkout", "-q", "feat")
    (r / "mine.py").write_text("v1\n")
    git(r, "add", "-A")
    git(r, "reset", "-q", "--soft", "main")
    git(r, "commit", "-qm", "squashed")
    assert cnr.reverted_paths(str(r), "feat", "main") == ["theirs.py"]


def test_no_reflog_is_not_judged(tmp_path):
    r = _repo(tmp_path)
    _branch_then_main_moves(r)
    _work_on_feat(r)
    git(r, "reflog", "expire", "--expire=now", "--all")
    assert cnr.branch_start(str(r), "feat") is None
    assert cnr.main(["feat", "--repo", str(r)]) == 0
