"""scripts/precommit_uv_lock.sh: a commit that changes backend/pyproject.toml must keep
backend/uv.lock in step (design 2026-10-31-swarm-out-agent-owns-its-runs §2.9). CI,
release builds and Hives install with ``uv sync --locked``, which refuses a stale lock.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UV = shutil.which("uv")
PYPROJECT = '[project]\nname = "demo"\nversion = "{v}"\nrequires-python = ">=3.11"\ndependencies = []\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True, timeout=30)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "scripts").mkdir(parents=True)
    (repo / "backend").mkdir()
    shutil.copy(ROOT / "scripts" / "precommit_uv_lock.sh", repo / "scripts")
    (repo / "backend" / "pyproject.toml").write_text(PYPROJECT.format(v="0.1.0"))
    subprocess.run([UV, "lock", "-q"], cwd=repo / "backend", check=True, timeout=60)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, timeout=30)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "scripts/precommit_uv_lock.sh"], cwd=repo,
                          capture_output=True, text=True, timeout=90)


def test_a_pyproject_change_without_its_lock_is_stopped(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text(PYPROJECT.format(v="0.2.0"))
    _git(repo, "add", "backend/pyproject.toml")
    got = _check(repo)
    assert got.returncode == 1 and "uv lock" in got.stderr


def test_the_same_change_with_its_lock_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text(PYPROJECT.format(v="0.2.0"))
    subprocess.run([UV, "lock", "-q"], cwd=repo / "backend", check=True, timeout=60)
    _git(repo, "add", "-A")
    assert _check(repo).returncode == 0


def test_a_commit_that_does_not_touch_pyproject_is_not_checked(tmp_path):
    repo = _repo(tmp_path)
    (repo / "backend" / "pyproject.toml").write_text(PYPROJECT.format(v="0.2.0"))   # changed, not staged
    (repo / "notes.md").write_text("x")
    _git(repo, "add", "notes.md")
    assert _check(repo).returncode == 0

