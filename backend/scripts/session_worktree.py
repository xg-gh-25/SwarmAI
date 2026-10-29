"""session_worktree — a native session's own git worktree, on demand (design
docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.8).

When another session has uncommitted edits in the same checkout
(core/shared_checkout.py tells the agent), the agent makes its change here instead:

    "$SWARM_BACKEND_BIN" py -m scripts.session_worktree create <repo path> [--name N]
    "$SWARM_BACKEND_BIN" py -m scripts.session_worktree land <worktree path>
    "$SWARM_BACKEND_BIN" py -m scripts.session_worktree remove <worktree path>

- create: a new branch ``session/<key>-<name>`` (``session/<name>`` without a session
  id; ``<key>`` = core.app_session_id.session_key) at the main checkout's HEAD, checked
  out in ``<app data>/session-worktrees/<name>/<repo dir name>``; the dirs the repo
  declares in ``.swarmai/pipeline.toml`` ``[worktree] link`` are symlinked in
  (core/run_worktree). The other session's uncommitted edits stay in the main checkout.
- land: the worktree must have no uncommitted changes; its branch is rebased onto the
  main checkout's current branch, then ``merge --ff-only`` there. A refusal (conflict,
  or the main checkout has uncommitted edits to the same files) is printed as is and
  nothing is forced. Before the merge, a branch that puts a file back to where the
  branch began while the base changed it since is refused (scripts/check_no_revert).
  The merge writes the landing's one record, git's own reflog entry of the
  destination branch, stamped ``swarm-land <branch> session=<key> turn=<id>
  onto=<target>`` (design docs/design/2026-10-25-turn-result-card.md §3); "Already up
  to date" lands nothing (``landed: false``).
- remove: removes the worktree and deletes its branch, only once the branch is merged
  into the main checkout's branch (land first). Never deletes unmerged work.

Output is one JSON object on stdout; exit 0 on success, 1 on a refusal, 2 on bad use.
Not ``<app data>/worktrees``: that tree belongs to pipeline runs and its sweeper
quarantines anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import run_worktree as rw  # noqa: E402
from scripts import check_no_revert  # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BRANCH_PREFIX = "session/"


class Refused(Exception):
    pass


def root() -> Path:
    env = os.environ.get("SWARM_SESSION_WORKTREE_ROOT")
    return Path(os.path.realpath(env)) if env else rw._data_dir() / "session-worktrees"


def _git(cwd, *args, timeout=120.0, env=None):
    return rw.git(cwd, *args, timeout=timeout, env=env)


def _out(cwd, *args) -> str:
    p = _git(cwd, *args)
    if p.returncode != 0:
        raise Refused((p.stderr or p.stdout).strip()[:500] or f"git {args[0]} failed")
    return p.stdout.strip()


def _main_checkout(path: Path) -> Path:
    path = Path(os.path.realpath(path))
    top = rw._toplevel(path if path.is_dir() else path.parent)
    if not top:
        raise Refused(f"{path} is not inside a git repository")
    top = Path(os.path.realpath(top))
    if not (top / ".git").is_dir():
        raise Refused(f"{top} is a linked worktree; pass the main checkout")
    return top


def _branch_of_main(main: Path) -> str:
    ref = rw._symbolic_head(main)
    if not ref or not ref.startswith("refs/heads/"):
        raise Refused(f"{main} is on a detached HEAD")
    return ref[len("refs/heads/"):]


def _attribution() -> "tuple[str, str]":
    """(session key, turn id) for the landing stamp (core/session_landings.attribution)."""
    from core.session_landings import attribution
    return attribution()


def create(repo: str, name: "str | None") -> dict:
    main = _main_checkout(Path(repo))
    base = _branch_of_main(main)
    busy = rw.busy_state(main)
    if busy:
        raise Refused(f"{main} has a {busy} in progress")
    name = name or time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    if not NAME_RE.match(name):
        raise Refused(f"bad name {name!r}")
    path = root() / name / main.name
    if path.exists():
        raise Refused(f"{path} already exists")
    key = _attribution()[0]
    branch = BRANCH_PREFIX + (f"{key}-{name}" if key != "none" else name)
    path.parent.mkdir(parents=True, exist_ok=True)
    p = _git(main, "worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    if p.returncode != 0:
        raise Refused((p.stderr or p.stdout).strip()[:500])
    links = rw._link_dependencies(main, path)
    rw._exclude_paths(rw._common_dir(main), links)
    return {"path": str(path), "branch": branch, "base_branch": base, "main": str(main),
            "dep_links": links}


def _own_worktree(path: str) -> "tuple[Path, Path, str]":
    """(worktree, main checkout, branch) for a worktree this tool created."""
    wt = Path(os.path.realpath(path))
    if not str(wt).startswith(str(root()) + os.sep):
        raise Refused(f"{wt} is not a session worktree (under {root()})")
    top = rw._toplevel(wt)
    if not top or Path(os.path.realpath(top)) != wt:
        raise Refused(f"{wt} is not a worktree root")
    common = Path(rw._common_dir(wt))
    main = Path(os.path.realpath(common.parent))
    ref = rw._symbolic_head(wt) or ""
    if not ref.startswith("refs/heads/" + BRANCH_PREFIX):
        raise Refused(f"{wt} is not on a {BRANCH_PREFIX}* branch")
    return wt, main, ref[len("refs/heads/"):]


def land(path: str) -> dict:
    wt, main, branch = _own_worktree(path)
    if _out(wt, "status", "--porcelain"):
        raise Refused(f"{wt} has uncommitted changes; commit them first")
    base = _branch_of_main(main)
    # Pin the base tip once: the rebase and the revert check both use this commit, so
    # another session landing in between cannot make the check judge a tip the branch
    # was never rebased onto (it would call that session's files our reverts).
    target = _out(main, "rev-parse", "--verify", f"refs/heads/{base}^{{commit}}")
    p = _git(wt, "rebase", "-q", target)
    if p.returncode != 0:
        _git(wt, "rebase", "--abort")
        raise Refused(f"rebase onto {base} stopped (conflict); resolve it in {wt} by hand: "
                      + (p.stderr or p.stdout).strip()[:400])
    # ff-only checks that the branch continues the base, not what it does to files:
    # a squash from an old tree passes it and undoes what others landed meanwhile.
    reverted = check_no_revert.reverted_paths(str(main), branch, target)
    if reverted:
        raise Refused(f"{branch} puts {len(reverted)} file(s) back to where it began, "
                      f"undoing what {base} changed since: {', '.join(reverted[:10])}"
                      + (" ..." if len(reverted) > 10 else "")
                      + f". Squash with `git reset --soft $(git merge-base HEAD {base})`.")
    # The check holds only for the pinned tip. If the base moved since, say so; a move
    # after this read is still caught by ff-only (the branch does not contain it).
    if _out(main, "rev-parse", "--verify", f"refs/heads/{base}^{{commit}}") != target:
        raise Refused(f"{base} moved while landing (another session landed); "
                      "nothing merged, run land again")
    head = _out(wt, "rev-parse", "HEAD")
    key, turn = _attribution()
    attribution = {"session": key, "turn": turn}
    if head == target:     # "Already up to date": no ref moves, so no landing
        return {"landed": False, "landed_branch": branch, "into": base, "main": str(main),
                "head": head[:12], "attribution": attribution}
    # The stamp is the landing's record: git writes it, under its own ref lock, into
    # the destination's reflog entry; the session's cards read only that
    # (design docs/design/2026-10-25-turn-result-card.md §3.1).
    from core.session_landings import stamp
    p = _git(main, "-c", "core.logAllRefUpdates=always", "merge", "--ff-only", "-q", branch,
             env={"GIT_REFLOG_ACTION": stamp(branch, target)})
    if p.returncode != 0:
        raise Refused(f"merge --ff-only into {base} refused: "
                      + (p.stderr or p.stdout).strip()[:400])
    has_log = _git(main, "reflog", "exists", f"refs/heads/{base}").returncode == 0
    return {"landed": True, "landed_branch": branch, "into": base, "main": str(main),
            "onto": target, "new": head, "head": head[:12], "attribution": attribution,
            "record": "ok" if has_log else "missing"}


def remove(path: str) -> dict:
    wt, main, branch = _own_worktree(path)
    base = _branch_of_main(main)
    if _git(main, "merge-base", "--is-ancestor", branch, base).returncode != 0:
        raise Refused(f"{branch} is not merged into {base}; land it first (nothing removed)")
    for rel in rw.dep_links(main):          # our symlinks, so the worktree reads clean
        link = wt / rel
        if link.is_symlink():
            link.unlink()
    p = _git(main, "worktree", "remove", str(wt))
    if p.returncode != 0:
        raise Refused((p.stderr or p.stdout).strip()[:500])
    _out(main, "branch", "-d", "-q", branch)
    try:
        wt.parent.rmdir()
    except OSError:
        pass
    return {"removed": str(wt), "branch_deleted": branch}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="session_worktree", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("repo"); c.add_argument("--name")
    sub.add_parser("land").add_argument("worktree")
    sub.add_parser("remove").add_argument("worktree")
    a = ap.parse_args(argv)
    try:
        out = (create(a.repo, a.name) if a.cmd == "create"
               else land(a.worktree) if a.cmd == "land" else remove(a.worktree))
    except (Refused, rw.WorktreeError) as exc:
        print(json.dumps({"ok": False, "error": getattr(exc, "message", None) or str(exc)}))
        return 1
    print(json.dumps({"ok": True, **out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
