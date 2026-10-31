"""scripts/precommit_bvt_manifest.sh: a commit that deletes a test still named by the BVT
manifest is stopped, and says which file (design
docs/design/2026-10-31-swarm-out-single-loop-fixes.md §2.9). Runs in a throwaway git repo
holding the two scripts and a two-line manifest: no workspace, no network."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, timeout=30)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    (repo / "backend" / "tests").mkdir(parents=True)
    for name in ("run_bvt.sh", "precommit_bvt_manifest.sh"):
        shutil.copy(ROOT / "scripts" / name, repo / "scripts" / name)
    tests = repo / "backend" / "tests"
    (tests / "test_a.py").write_text("def test_a():\n    pass\n")
    (tests / "test_b.py").write_text("def test_b():\n    pass\n")
    (tests / "bvt_manifest.txt").write_text("test_a.py  # a\ntest_b.py  # b\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, timeout=30)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/precommit_bvt_manifest.sh"], cwd=repo,
                          capture_output=True, text=True, timeout=60)


def test_deleting_a_listed_test_is_stopped_and_named(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "backend/tests/test_b.py")
    got = _check(repo)
    assert got.returncode == 1 and "test_b.py" in got.stderr


def test_deleting_it_with_its_manifest_line_passes(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", "backend/tests/test_b.py")
    (repo / "backend" / "tests" / "bvt_manifest.txt").write_text("test_a.py  # a\n")
    _git(repo, "add", "backend/tests/bvt_manifest.txt")
    assert _check(repo).returncode == 0


def test_a_commit_outside_the_tests_is_not_checked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backend" / "tests" / "test_b.py").unlink()       # broken, but not part of this commit
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "README.md")
    assert _check(repo).returncode == 0
