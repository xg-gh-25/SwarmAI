"""pipeline_test.py in an isolated run (docs/design/pipeline-run-worktree.md §6.2, §7.3,
§15 acceptance 2 and 8).

Methodology: a temp main repo + a real run worktree; ``_target_repo`` /
``_worktree_evidence`` called in-process; the generated sitecustomize is exercised in
a child interpreter whose PYTHONPATH also carries the MAIN backend (standing in for
the venv's editable .pth). Invariants: an isolated run is tested in its validated
worktree and never falls back to main; evidence records T and the un-added files; a
module the run deleted does not import from main.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import run_worktree as rw
from scripts import pipeline_test as pt

RID = "run_pt1"


def _g(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    tmp = Path(os.path.realpath(tmp_path))
    main = tmp / "swarmai"
    (main / "backend").mkdir(parents=True)
    _g(main, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@e.com"), ("core.hooksPath", str(tmp))):
        _g(main, "config", k, v)
    (main / "backend" / "gone_mod.py").write_text("X = 1\n")
    _g(main, "add", "-A")
    _g(main, "commit", "-q", "-m", "i")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp / "wt"))
    monkeypatch.setenv("SWARM_TEST_REPO_ROOT", str(main))
    monkeypatch.setattr(pt, "_WORKTREE", {})
    monkeypatch.delenv("PIPELINE_TEST_STRIP_ROOT", raising=False)
    e = rw.create(RID, "swarmai", main)
    yield {"main": main, "entry": e, "wt": rw.wt_path(RID, "swarmai"), "tmp": tmp}
    os.environ.pop("PIPELINE_TEST_STRIP_ROOT", None)


def test_isolated_run_targets_its_worktree(repo):
    state = {"worktree_mode": "isolated", "worktrees": [repo["entry"]]}
    target, err = pt._target_repo(RID, state)
    assert err is None and target == repo["wt"]


def test_invalid_worktree_never_falls_back_to_main(repo):
    bad = dict(repo["entry"], git_common_dir="/nope/.git")
    target, err = pt._target_repo(RID, {"worktree_mode": "isolated", "worktrees": [bad]})
    assert target is None and "invalid" in err


def test_shared_run_targets_main(repo):
    assert pt._target_repo(RID, {"worktree_mode": "shared"}) == (repo["main"], None)


def test_evidence_records_tree_and_untracked(repo):
    pt._target_repo(RID, {"worktree_mode": "isolated", "worktrees": [repo["entry"]]})
    (repo["wt"] / "probe.py").write_text("p\n")

    class C:
        ctx_repo = None
    ctx = C()
    ctx.repo, ctx.warnings = repo["wt"], []
    ev = pt._worktree_evidence(ctx)["swarmai"]
    assert ev["tree"] == rw.change_tree(repo["wt"], RID) and ev["untracked"] == ["probe.py"]


def test_deleted_module_does_not_import_from_main(repo, tmp_path):
    pt._target_repo(RID, {"worktree_mode": "isolated", "worktrees": [repo["entry"]]})
    (repo["wt"] / "backend" / "gone_mod.py").unlink()
    site = pt._site_dir(Path(os.path.realpath(tmp_path)) / "cache")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(repo["wt"] / "backend"), str(site), str(repo["main"] / "backend")]))
    code = "import gone_mod"
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       cwd=str(repo["wt"] / "backend"))
    assert p.returncode != 0 and "ModuleNotFoundError" in p.stderr
    env.pop("PIPELINE_TEST_STRIP_ROOT")
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       cwd=str(repo["wt"] / "backend"))
    assert p.returncode == 0  # the fallback the sitecustomize exists to remove
