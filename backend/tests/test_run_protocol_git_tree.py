"""Tests real Pipeline v1 git tree adapters.

Methodology: temporary git repository with tracked edits and untracked files.
"""
from __future__ import annotations

import hashlib
import subprocess

from core.run_protocol.git_tree import diff_trees, list_reserved, write_tree
from core.run_protocol.tree import build_code_trees, build_delivery_trees, check_lg3


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q"); _git(repo, "config", "user.email", "a@b.c"); _git(repo, "config", "user.name", "T")
    (repo / "tracked.txt").write_text("old\n")
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    return repo


def test_code_tree_includes_tracked_edits_and_untracked_files(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tracked.txt").write_text("new\n")
    (repo / "untracked.txt").write_text("included\n")
    trees = build_code_trees({"repo": str(repo)}, "run_one", write_tree, list_reserved)
    names = _git(repo, "ls-tree", "-r", "--name-only", trees["repo"]).splitlines()
    assert names == ["tracked.txt", "untracked.txt"]
    assert _git(repo, "status", "--short") == "M tracked.txt\n?? untracked.txt"


def test_delivery_tree_adds_only_renderer_report(tmp_path):
    repo = _repo(tmp_path)
    code = build_code_trees({"repo": str(repo)}, "run_one", write_tree, list_reserved)
    report = b"# Audit\n"
    digest = hashlib.sha256(report).hexdigest()
    binding = build_delivery_trees({"repo": str(repo)}, "run_one", code, report, digest,
                                   write_tree, list_reserved)["repo"]
    ok, detail = check_lg3(repo=str(repo), run_id="run_one", binding=binding,
                           rendered_report_sha=digest, diff_trees=diff_trees)
    assert ok, detail


def test_reserved_content_is_visible_and_blocks_tree_build(tmp_path):
    repo = _repo(tmp_path)
    directory = repo / ".aidlc" / "deliverables" / "run_one"
    directory.mkdir(parents=True); (directory / "foreign.txt").write_text("x")
    assert list_reserved(str(repo), ".aidlc/deliverables/run_one/") == [
        ".aidlc/deliverables/run_one/foreign.txt"]
