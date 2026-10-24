"""check_no_revert — refuse to land a branch that puts files back the way they were.

A branch is landed with ``merge --ff-only``, which only checks that the branch
continues the target branch, not what it does to files. On 2026-10-24 a branch was
squashed with ``git reset --soft main`` from a worktree made 54 minutes earlier: its
index still held the old tree, so its one commit set 30 files back to the branch's
starting point and silently undid 8 commits other sessions had landed meanwhile
(a23fc2a00). ``rebase`` and ``merge --ff-only`` both passed.

The check: S is the commit the branch was created from (the oldest entry of the
branch's own reflog, so a later ``reset --soft`` / rebase cannot move it). For each
file the branch changes against the target T, the file is a REVERT when the branch's
version equals S's and T's does not: T changed it after the branch began, and the
branch puts it back. A real change to the same file never equals S's version.

    python backend/scripts/check_no_revert.py <branch> [--into main] [--repo .]
        [--allow <path>]...

- ``reverted_paths(repo, branch, into, start=None)`` — the reverted paths, sorted.
- ``branch_start(repo, branch)`` — S from the branch reflog, or None when it has none.

Exit 0: none (or no reflog to judge by, said on stderr). Exit 1: the paths, one per
line, and how to fix it. ``--allow`` names a path whose revert is intended.
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def _git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env=None)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _blob(repo: str, rev: str, path: str) -> "str | None":
    p = subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def branch_start(repo: str, branch: str) -> "str | None":
    """The commit the branch was created from: its reflog's oldest entry."""
    p = subprocess.run(["git", "-C", repo, "reflog", "show", "--format=%H",
                        f"refs/heads/{branch}", "--"], capture_output=True, text=True)
    shas = p.stdout.split() if p.returncode == 0 else []
    return shas[-1] if shas else None


def reverted_paths(repo: str, branch: str, into: str, start: "str | None" = None) -> list[str]:
    start = start or branch_start(repo, branch)
    if not start:
        return []
    changed = _git(repo, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames",
                   into, branch).splitlines()
    out = []
    for path in changed:
        s, t, b = (_blob(repo, rev, path) for rev in (start, into, branch))
        if b == s and t != s:
            out.append(path)
    return sorted(out)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("branch")
    ap.add_argument("--into", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--allow", action="append", default=[])
    a = ap.parse_args(argv)
    if not branch_start(a.repo, a.branch):
        print(f"check_no_revert: {a.branch} has no reflog; not checked", file=sys.stderr)
        return 0
    bad = [p for p in reverted_paths(a.repo, a.branch, a.into) if p not in a.allow]
    if not bad:
        return 0
    print(f"{a.branch} puts {len(bad)} file(s) back to where the branch began, undoing "
          f"what {a.into} changed since:")
    for p in bad:
        print(f"  {p}")
    print("Usual cause: a squash with `git reset --soft " + a.into + "` from an old tree. "
          "Squash with `git reset --soft $(git merge-base HEAD " + a.into + ")` and rebase "
          "again. If the revert is intended, pass --allow <path>.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
