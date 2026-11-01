"""base_tree — the run's base as plain files, to check what is red before the run
(design docs/design/2026-11-01-pipeline-delivery-friction.md D6).

A test that fails in TEST may fail the same way on the code the run started from; the
report then says ``pre_existing`` with its reason. To find out, the agent runs the same
command on the base. ``pipeline-v1 base-tree --repo <name>`` puts that base, the run's
fork point of the repo, in a directory of the system temp dir (never in the workspace:
it is not a git worktree, and nothing commits it), with the repo's declared dependency
links (``.swarmai/pipeline.toml`` ``[worktree] link``) as in a run worktree. A second
call for the same run and repo returns the same directory. The run's worktree release
removes it (``remove``); the system clears its temp dir as well.

Public: ``extract``, ``remove``, ``root_for``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_RUN_ID = re.compile(r"^run_[A-Za-z0-9_]{1,64}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class BaseTreeError(ValueError):
    """The base cannot be put on disk; the message says why."""


def root_for(run_id: str) -> Path:
    """The run's directory under the system temp dir."""
    if not _RUN_ID.match(run_id or ""):
        raise BaseTreeError(f"not a run id: {run_id!r}")
    return Path(tempfile.gettempdir()) / "swarmai-base" / run_id


def extract(engine: Any, repo: str) -> dict[str, Any]:
    """``{"path", "base", "repo", "links", "reused"}``: the run's base of *repo* as files.
    *engine*: the run's ``RunEngine`` (its repos and their fork base)."""
    if not _REPO.match(repo or ""):
        raise BaseTreeError(f"not a repo name: {repo!r}")
    repos = engine.effective_repos()
    if repo not in repos:
        raise BaseTreeError(f"{repo!r} is not one of this run's repos {sorted(repos)}")
    base = engine._fork_base(repo, repos[repo])
    main = Path(engine.contract["repo_roots"][repo])
    target = root_for(engine.run_id) / repo
    marker = target / ".swarmai-base"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == base:
        return {"path": str(target), "base": base, "repo": repo, "links": [], "reused": True}
    if target.exists():
        shutil.rmtree(target)          # an older base of this run: main moved since
    target.mkdir(parents=True, mode=0o700)
    archive = subprocess.run(["git", "-C", str(repos[repo]), "archive", "--format=tar", base],
                             capture_output=True, timeout=600)
    if archive.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise BaseTreeError("git archive failed: "
                            + archive.stderr.decode("utf-8", "replace").strip()[:300])
    untar = subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout,
                           capture_output=True, timeout=600)
    if untar.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        raise BaseTreeError("tar failed: " + untar.stderr.decode("utf-8", "replace").strip()[:300])
    from core.run_worktree import dep_links
    links = []
    for rel in dep_links(main):
        src, dst = main / rel, target / rel
        if src.is_dir() and dst.parent.is_dir() and not os.path.lexists(dst):
            os.symlink(str(src), str(dst))
            links.append(rel)
    marker.write_text(base + "\n", encoding="utf-8")
    return {"path": str(target), "base": base, "repo": repo, "links": links, "reused": False}


def remove(run_id: str) -> bool:
    """Delete the run's base directories (its worktree release calls this). Only the
    files this module extracted: the directory is under the temp dir and named by the
    run id. Returns whether anything was there."""
    try:
        root = root_for(run_id)
    except BaseTreeError:
        return False
    if not root.is_dir() or root.is_symlink():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True
