"""session_landings — a session's landings, read from the stamp git keeps.

``session_worktree land`` merges with ``GIT_REFLOG_ACTION="swarm-land <branch>
session=<key> turn=<id> onto=<target>"``, so the destination branch's reflog entry
for that update is the landing's one record: git writes it under its own ref lock,
and the entry's sha is the branch head (design
docs/design/2026-10-25-turn-result-card.md §3-§4, v9). This module only reads it:

- no turn window, no neighbour logic: an entry means a landing only by its own
  message, matched exactly, with this session's key;
- every shown fact is checked against git now: ``onto`` an ancestor of ``new``
  (else the stamp is dropped and counted ``unverified``), the commits and files of
  ``onto..new``, and whether ``new`` is still on the destination branch;
- what cannot be read is said: ``unknown`` (``no-branch``, ``no-reflog``,
  ``timeout``), and ``truncated`` when the read returned exactly its cap.

All git calls share one ``Deadline``: each call's timeout is the time left, and no
call starts once it has run out (a cancelled executor future does not stop git, so
the per-call timeout is what bounds it). Read-only, ``GIT_OPTIONAL_LOCKS=0``.

Public: ``Deadline``, ``read_repo``, ``read_session``, ``TURN_CAP``, ``CANVAS_CAP``.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Optional

TURN_CAP = 200
CANVAS_CAP = 2000
MAX_SUBJECTS = 10

_STAMP = re.compile(
    r"^swarm-land (session/[A-Za-z0-9._/-]+) session=([0-9a-f]{12}) "
    r"turn=([0-9a-f]{32}|none) onto=([0-9a-f]{40}): Fast-forward$")
_DIFF = ("diff", "--no-renames", "--no-ext-diff", "--no-textconv")


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        return self.end - time.monotonic()


class _OutOfTime(Exception):
    pass


def _git(root: str, deadline: Deadline, *args: str, ok=(0,)) -> "tuple[int, str]":
    left = deadline.left()
    if left <= 0:
        raise _OutOfTime()
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C", "GIT_LITERAL_PATHSPECS": "1"}
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, timeout=left,
                           env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise _OutOfTime() from exc
    if r.returncode not in ok:
        raise RuntimeError(f"git {args[0]} failed ({r.returncode})")
    return r.returncode, r.stdout.decode("utf-8", "replace")


def _landing(root: str, dest: str, deadline: Deadline, branch: str, turn: str, onto: str,
             new: str) -> "Optional[dict]":
    """One stamped entry checked against git, or None when its stamp does not hold."""
    from core.turn_changes import MAX_DIFF_LINES, _parse_name_status, _parse_numstat
    rc, _ = _git(root, deadline, "merge-base", "--is-ancestor", onto, new, ok=(0, 1, 128))
    if rc != 0 or onto == new:     # a real land never stamps an empty range
        return None
    rng = f"{onto}..{new}"
    count = int(_git(root, deadline, "rev-list", "--count", rng)[1].strip() or 0)
    log = _git(root, deadline, "log", f"--max-count={MAX_SUBJECTS}", "--format=%h%x00%s", rng)[1]
    subjects = [dict(zip(("sha", "subject"), line.split("\0", 1)))
                for line in log.splitlines() if "\0" in line]
    trees = _git(root, deadline, "rev-parse", f"{onto}^{{tree}}", f"{new}^{{tree}}")[1].split()
    start, end = trees[0], trees[1]
    numstat = _parse_numstat(_git(root, deadline, *_DIFF, "--numstat", "-z", start, end)[1])
    status = _parse_name_status(_git(root, deadline, *_DIFF, "--name-status", "-z", start, end)[1])
    on = _git(root, deadline, "merge-base", "--is-ancestor", new, f"refs/heads/{dest}",
              ok=(0, 1))[0] == 0
    repo = os.path.basename(root)
    files = []
    for path, (added, deleted) in sorted(numstat.items()):
        binary = added is None
        files.append({
            "path": path, "absolutePath": os.path.join(root, path), "repo": repo,
            "status": status.get(path, "modified"), "added": added, "deleted": deleted,
            "binary": binary,
            "diffable": not binary and (added or 0) + (deleted or 0) <= MAX_DIFF_LINES,
            "gitRoot": root, "start": start, "end": end,
        })
    return {"repo": repo, "gitRoot": root, "dest": dest, "branch": branch,
            "turn": turn, "onto": onto, "new": new, "count": count, "subjects": subjects,
            "state": "on" if on else "off", "files": files}


def read_repo(root: str, key: str, *, deadline: Deadline, turn_id: "Optional[str]" = None,
              cap: int = TURN_CAP, since_days: "Optional[int]" = None,
              limit: "Optional[int]" = None) -> dict:
    """This session's landings into the checkout *root*'s current branch, newest first:
    ``{landed, unverified, truncated, unknown}``. ``turn_id``: only that turn's.
    ``since_days`` / ``limit``: Canvas's window (by the reflog's own date)."""
    out: dict = {"landed": [], "unverified": 0, "truncated": False, "unknown": None}
    try:
        rc, ref = _git(root, deadline, "symbolic-ref", "-q", "HEAD", ok=(0, 1))
        if rc != 0 or not ref.strip().startswith("refs/heads/"):
            out["unknown"] = "no-branch"
            return out
        dest = ref.strip()[len("refs/heads/"):]
        if _git(root, deadline, "reflog", "exists", f"refs/heads/{dest}", ok=(0, 1))[0] != 0:
            out["unknown"] = "no-reflog"
            return out
        raw = _git(root, deadline, "reflog", "show", "--date=unix",
                   "--format=%H%x00%gs%x00%gd", f"-n{cap}", f"refs/heads/{dest}", "--")[1]
        lines = [x for x in raw.splitlines() if x]
        if len(lines) >= cap:
            out["truncated"] = True
        floor = time.time() - since_days * 86400 if since_days else None
        for line in lines:
            parts = line.split("\0")
            if len(parts) != 3:
                continue
            new, msg, selector = parts
            m = _STAMP.match(msg)
            if not m or m.group(2) != key or (turn_id and m.group(3) != turn_id):
                continue
            if floor is not None:
                at = re.search(r"@\{(\d+)\}$", selector)
                if at and int(at.group(1)) < floor:
                    out["truncated"] = False
                    break
            if limit is not None and len(out["landed"]) >= limit:
                out["truncated"] = True
                break
            land = _landing(root, dest, deadline, m.group(1), m.group(3), m.group(4), new)
            if land is None:
                out["unverified"] += 1
            else:
                out["landed"].append(land)
    except _OutOfTime:
        out["unknown"] = "timeout"
    except Exception:  # noqa: BLE001 — a display aid: said, never raised
        out["unknown"] = "unreadable"
    return out


def read_session(session_id: str, *, deadline: Deadline, turn_id: "Optional[str]" = None,
                 cap: int = TURN_CAP, since_days: "Optional[int]" = None,
                 limit: "Optional[int]" = None) -> dict:
    """``read_repo`` over every bound main checkout: ``{landed, unverified,
    truncated, unknown: [{repo, reason}]}``. Blocking: run it on a dedicated pool."""
    from core.app_session_id import session_key, valid_app_session_id
    res: dict = {"landed": [], "unverified": 0, "truncated": False, "unknown": []}
    if not valid_app_session_id(session_id):
        return res
    key = session_key(session_id)
    for root in _bound_checkouts():
        one = read_repo(root, key, deadline=deadline, turn_id=turn_id, cap=cap,
                        since_days=since_days, limit=limit)
        res["landed"] += one["landed"]
        res["unverified"] += one["unverified"]
        res["truncated"] = res["truncated"] or one["truncated"]
        if one["unknown"]:
            res["unknown"].append({"repo": os.path.basename(root), "reason": one["unknown"]})
    return res


def _bound_checkouts() -> list[str]:
    """The bound repos' main checkouts (a linked worktree or a pipeline run's is
    not one: their ``.git`` is a file)."""
    try:
        from core.needs_human_review import _bound_worktree_roots
        from core.project_registry import get_swarmws
        roots = [os.path.realpath(p) for p, _r in _bound_worktree_roots(str(get_swarmws()))]
    except Exception:  # noqa: BLE001
        return []
    return sorted({r for r in roots if os.path.isdir(os.path.join(r, ".git"))})
