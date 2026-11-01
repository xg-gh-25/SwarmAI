"""filter_programs counts a filter program only when the repo's own config names it.

A program in the user's global or system config (git-lfs on every CI runner) runs in
all their git use already; flagging it made the callers report skip every working
tree on such a machine (the CI_IMPACT probe failed on GitHub's runners).
"""
import subprocess
from pathlib import Path

from core.code_intel.changed import filter_programs


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _env(monkeypatch, tmp_path: Path, global_text: str = "") -> None:
    g = tmp_path / "global.gitconfig"
    g.write_text(global_text, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(g))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def test_a_global_filter_program_does_not_count(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, '[filter "lfs"]\n\tclean = git-lfs clean -- %f\n'
                                '\tprocess = git-lfs filter-process\n')
    assert filter_programs(_repo(tmp_path)) == []


def test_the_repo_config_naming_a_filter_program_counts(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, '[filter "lfs"]\n\tprocess = git-lfs filter-process\n')
    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "filter.evil.clean", "sh -c x"], check=True)
    assert filter_programs(repo) == ["filter.evil.clean"]


def test_a_file_the_repo_config_includes_counts(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    inc = tmp_path / "inc.gitconfig"
    inc.write_text('[filter "x"]\n\tsmudge = sh -c y\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "config", "include.path", str(inc)], check=True)
    assert filter_programs(repo) == ["filter.x.smudge"]
