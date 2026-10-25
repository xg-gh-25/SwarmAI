#!/usr/bin/env python3
"""worktree_reattach — give a linked git worktree its admin entry back after another
session's `git worktree prune` deleted it (AGENTS.md "Never prune").

Agents run in sandboxes that cannot see each other's scratch directories, so a live
worktree can look deleted from another session, and `git worktree prune` there
deletes its ``.git/worktrees/<name>`` entry. The files survive; git in the worktree
fails with "not a git repository". This puts it back without touching a file:

    python backend/scripts/worktree_reattach.py <worktree path> --branch <its branch>

1. Refuse unless *path* has a ``.git`` file whose gitdir is gone (a healthy worktree,
   or a directory that is no linked worktree, is left alone), the branch exists, and
   no other worktree has it checked out. The branch is given, never guessed: its name
   lived in the deleted entry.
2. ``git worktree add --no-checkout --lock`` the branch at an empty temporary path
   next to it (git makes a fresh admin entry), point *path*'s ``.git`` file at that
   entry, ``git worktree repair <path>`` (the entry now points back at *path*), and
   delete the temporary directory.
3. ``git reset -q``: the index (lost with the entry) is rebuilt from HEAD. The working
   tree is not touched, so uncommitted and untracked files stay; edits that were
   staged show as unstaged.

The worktree comes back locked, so the next prune cannot repeat this.
Output is one JSON object on stdout; exit 0 on success, 1 on a refusal.
Public: ``reattach``, ``main``.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

LOCK_REASON = ("agent session scratch; re-attached after a prune (AGENTS.md: lock your "
               "worktree, never prune). Unlock before remove.")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          timeout=120, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


def _refuse(error: str) -> dict:
    return {"ok": False, "error": error}


def reattach(path: str, *, branch: str) -> dict:
    wt = Path(path).resolve()
    dotgit = wt / ".git"
    if not dotgit.is_file():
        return _refuse(f"{wt} has no .git file: it is not a linked worktree")
    line = dotgit.read_text(encoding="utf-8").strip()
    if not line.startswith("gitdir:"):
        return _refuse(f"{dotgit} does not name a gitdir")
    admin = Path(line.split(":", 1)[1].strip())
    if not admin.is_absolute():
        admin = (wt / admin).resolve()
    if admin.is_dir() and _git(wt, "rev-parse", "--git-dir").returncode == 0:
        return _refuse(f"{wt} is healthy: git works there; nothing to do")
    if admin.parent.name != "worktrees":
        return _refuse(f"{admin} is not under a repository's worktrees/ directory")
    common = admin.parent.parent
    main = common.parent if common.name == ".git" else None
    if main is None or _git(main, "rev-parse", "--git-dir").returncode != 0:
        return _refuse(f"cannot find the main checkout of {common}")
    if _git(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
        return _refuse(f"no branch {branch!r} in {main}")
    listed = _git(main, "worktree", "list", "--porcelain").stdout
    if f"branch refs/heads/{branch}\n" in listed + "\n":
        return _refuse(f"branch {branch!r} is checked out in another worktree")

    holder = wt.parent / f".reattach-{secrets.token_hex(4)}"
    temp = holder / wt.name
    holder.mkdir()
    original = dotgit.read_text(encoding="utf-8")
    try:
        added = _git(main, "worktree", "add", "-q", "--no-checkout", "--lock",
                     "--reason", LOCK_REASON, str(temp), branch)
        if added.returncode != 0:
            return _refuse(f"git worktree add failed: {added.stderr.strip()}")
        dotgit.write_text((temp / ".git").read_text(encoding="utf-8"), encoding="utf-8")
        repaired = _git(main, "worktree", "repair", str(wt))
        if repaired.returncode != 0:
            dotgit.write_text(original, encoding="utf-8")
            _git(main, "worktree", "remove", "--force", "--force", str(temp))
            return _refuse(f"git worktree repair failed: {repaired.stderr.strip()}")
        reset = _git(wt, "reset", "-q")
        if reset.returncode != 0:
            return _refuse(f"re-attached, but rebuilding the index failed: "
                           f"{reset.stderr.strip()}; run `git reset -q` in {wt}")
    finally:
        shutil.rmtree(holder, ignore_errors=True)
    return {"ok": True, "worktree": str(wt), "branch": branch,
            "head": _git(wt, "rev-parse", "HEAD").stdout.strip(), "locked": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", help="the broken worktree's directory")
    parser.add_argument("--branch", required=True, help="the branch it had checked out")
    args = parser.parse_args(argv)
    out = reattach(args.path, branch=args.branch)
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
