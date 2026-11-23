"""A session worktree's dependency links (``[worktree] link``) are a real directory of
entry links: a reinstall in the worktree (``npm ci`` empties the directory it finds)
never empties the main checkout's directory, and ``remove`` drops only the links."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    data = tmp_path / "appdata"
    (data / "SwarmWS").mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)


def test_reinstall_in_a_session_worktree_keeps_the_main_checkouts_dir(tmp_path):
    from scripts import session_worktree as sw

    main = Path(os.path.realpath(tmp_path)) / "repo"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    (main / ".swarmai").mkdir()
    (main / ".swarmai" / "pipeline.toml").write_text('[worktree]\nlink = ["web/node_modules"]\n')
    (main / "web").mkdir()
    (main / "web" / "package.json").write_text("{}\n")
    _g(main, "add", "-A")
    _g(main, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    nm = main / "web" / "node_modules"
    (nm / "vite").mkdir(parents=True)
    (nm / "vite" / "package.json").write_text("{}\n")

    wt = Path(sw.create(str(main), "deps")["path"])
    wt_nm = wt / "web" / "node_modules"
    assert wt_nm.is_dir() and not wt_nm.is_symlink() and (wt_nm / "vite").is_symlink()
    for name in os.listdir(wt_nm):                     # what npm ci does first
        p = wt_nm / name
        p.unlink() if p.is_symlink() or p.is_file() else shutil.rmtree(p)
    assert (nm / "vite" / "package.json").exists()
    (wt_nm / "vite").mkdir()                            # the worktree's own install
    sw.remove(str(wt))
    assert not wt.exists() and (nm / "vite" / "package.json").exists()
