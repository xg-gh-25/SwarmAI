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

Public: ``Deadline``, ``read_repo``, ``read_session``, ``landed_path``, ``TURN_CAP``,
``CANVAS_CAP``.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

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


def _scan(root: str, key: str, deadline: Deadline, turn_id: "Optional[str]", cap: int,
          since_days: "Optional[int]") -> dict:
    """One checkout's stamps of this session, newest first, not yet checked:
    ``{dest, cands: [(at, branch, turn, onto, new)], capped, unknown}``. ``capped``:
    the read hit *cap* before the reflog (or the ``since_days`` window) ended."""
    out: dict = {"root": root, "dest": None, "cands": [], "capped": False, "unknown": None}
    try:
        rc, ref = _git(root, deadline, "symbolic-ref", "-q", "HEAD", ok=(0, 1))
        if rc != 0 or not ref.strip().startswith("refs/heads/"):
            out["unknown"] = "no-branch"
            return out
        dest = out["dest"] = ref.strip()[len("refs/heads/"):]
        if _git(root, deadline, "reflog", "exists", f"refs/heads/{dest}", ok=(0, 1))[0] != 0:
            out["unknown"] = "no-reflog"
            return out
        raw = _git(root, deadline, "reflog", "show", "--date=unix",
                   "--format=%H%x00%gs%x00%gd", f"-n{cap}", f"refs/heads/{dest}", "--")[1]
        lines = [x for x in raw.splitlines() if x]
        out["capped"] = len(lines) >= cap
        floor = time.time() - since_days * 86400 if since_days else None
        for line in lines:
            parts = line.split("\0")
            if len(parts) != 3:
                continue
            new, msg, selector = parts
            at_m = re.search(r"@\{(\d+)\}$", selector)
            at = int(at_m.group(1)) if at_m else 0
            if floor is not None and at_m and at < floor:
                out["capped"] = False        # the window ended inside the read
                break
            m = _STAMP.match(msg)
            if not m or m.group(2) != key or (turn_id and m.group(3) != turn_id):
                continue
            out["cands"].append((at, m.group(1), m.group(3), m.group(4), new))
    except _OutOfTime:
        out["unknown"] = "timeout"
    except Exception as exc:  # noqa: BLE001 — a display aid: said, never raised
        logger.warning("session_landings: %s unreadable: %s", root, exc)
        out["unknown"] = "unreadable"
    return out


def _expand(scans: "list[dict]", deadline: Deadline, limit: "Optional[int]",
            expect: "Optional[int]") -> dict:
    """The scans' stamps merged newest first across checkouts, at most *limit*
    expanded (checked against git). Complete when every scan ended before its cap,
    or when *expect* stamps (the lands this turn handed its id) were found."""
    res: dict = {"landed": [], "unverified": 0, "truncated": False, "unknown": []}
    cands = [(c, sc) for sc in scans for c in sc["cands"]]
    cands.sort(key=lambda x: -x[0][0])      # stable: one repo's reflog order is kept
    res["truncated"] = any(sc["capped"] for sc in scans)
    if expect is not None and len(cands) >= expect:
        res["truncated"] = False             # every stamp handed out is found
        cands = cands[:expect]
    timed_out: set = set()
    for i, ((_at, branch, turn, onto, new), sc) in enumerate(cands):
        if limit is not None and len(res["landed"]) >= limit:
            res["truncated"] = True
            break
        try:
            land = _landing(sc["root"], sc["dest"], deadline, branch, turn, onto, new)
        except _OutOfTime:
            timed_out.update(id(s) for _c, s in cands[i:])
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("session_landings: %s unreadable: %s", sc["root"], exc)
            sc["unknown"] = sc["unknown"] or "unreadable"
            continue
        if land is None:
            res["unverified"] += 1
        else:
            res["landed"].append(land)
    for sc in scans:
        reason = sc["unknown"] or ("timeout" if id(sc) in timed_out else None)
        if reason:
            res["unknown"].append({"repo": os.path.basename(sc["root"]), "reason": reason})
    return res


def read_repo(root: str, key: str, *, deadline: Deadline, turn_id: "Optional[str]" = None,
              cap: int = TURN_CAP, since_days: "Optional[int]" = None,
              limit: "Optional[int]" = None, expect: "Optional[int]" = None) -> dict:
    """This session's landings into the checkout *root*'s current branch, newest first:
    ``{landed, unverified, truncated, unknown}`` (``unknown``: one reason or None).
    ``turn_id``: only that turn's. ``since_days`` / ``limit``: Canvas's window (by the
    reflog's own date, judged on every entry). ``expect``: how many lands were handed
    this turn's id (run_owner_gate counts them); finding that many is complete even
    when the read hit its cap — a busy branch's reflog is longer than any cap (this
    repo's main: ~1700 entries)."""
    res = _expand([_scan(root, key, deadline, turn_id, cap, since_days)], deadline,
                  limit, expect)
    res["unknown"] = res["unknown"][0]["reason"] if res["unknown"] else None
    return res


def read_session(session_id: str, *, deadline: Deadline, turn_id: "Optional[str]" = None,
                 cap: int = TURN_CAP, since_days: "Optional[int]" = None,
                 limit: "Optional[int]" = None, expect: "Optional[int]" = None) -> dict:
    """``read_repo`` over every bound main checkout, with ONE *limit* / *expect* for
    the session and newest first across them: ``{landed, unverified, truncated,
    unknown: [{repo, reason}]}``. Blocking: run it on a dedicated pool."""
    from core.app_session_id import session_key, valid_app_session_id
    if not valid_app_session_id(session_id):
        return {"landed": [], "unverified": 0, "truncated": False, "unknown": []}
    key = session_key(session_id)
    scans = [_scan(root, key, deadline, turn_id, cap, since_days)
             for root in _bound_checkouts()]
    return _expand(scans, deadline, limit, expect)


#: landed_path's reflog reads: (checkout, name) -> (branch tip, {stamp keys}); bounded
_LANDED_PATHS: "dict[tuple[str, str], tuple[tuple, frozenset]]" = {}
_LANDED_PATHS_MAX = 64
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")   # session_worktree.NAME_RE


def landed_path(path: str, session_id: "Optional[str]" = None) -> "Optional[str]":
    """A file path inside a REMOVED session worktree -> the same file in the main
    checkout it was landed into, or None (design 2026-10-25-landings-consumers.md D1).
    Fail closed: the path is ``<root>/<name>/<repo dir>/<rel>`` and not a pipeline
    run's; exactly one bound checkout has that basename (NFC + casefold); its branch's
    reflog holds a stamp of ``session/<key>-<name>`` with this session's key (given a
    session id) or with exactly one key (without); the checkout is under $HOME and
    ``<rel>`` is a file under it. Grants
    no access: the caller's own guard applies to the result. Blocking."""
    import unicodedata
    from pathlib import Path
    from core import run_worktree
    from core.app_session_id import session_key, valid_app_session_id
    from scripts.session_worktree import root as sw_root
    try:
        p = os.path.abspath(str(path))
        if os.path.lexists(p) or run_worktree.run_for_path(p) is not None:
            return None
        parts = Path(os.path.realpath(p)).relative_to(os.path.realpath(str(sw_root()))).parts
    except (ValueError, OSError):
        return None
    if len(parts) < 3 or any(x in (".", "..") for x in parts) or not _NAME.match(parts[0]):
        return None
    name, repo_dir, rest = parts[0], parts[1], parts[2:]
    fold = lambda x: unicodedata.normalize("NFC", x).casefold()  # noqa: E731
    cands = [r for r in _bound_checkouts() if fold(os.path.basename(r)) == fold(repo_dir)]
    if len(cands) != 1:
        return None
    main = cands[0]
    if not main.startswith(os.path.realpath(str(Path.home())) + os.sep):
        return None                      # a checkout outside $HOME is never mapped
    keys = _stamp_keys(main, name)
    if keys is None:
        return None
    if session_id is not None:
        if not valid_app_session_id(session_id) or session_key(session_id) not in keys:
            return None
    elif len(keys) != 1:
        return None
    target = os.path.realpath(os.path.join(main, *rest))
    if not target.startswith(main + os.sep) or not os.path.isfile(target):
        return None
    return target


def _stamp_keys(main: str, name: str) -> "Optional[frozenset]":
    """The session keys whose stamp in *main*'s branch reflog lands
    ``session/<key>-<name>``; None when unreadable. Cached by the branch tip."""
    from core.session_changes import _tips
    # the branch AND its tip: two branches at one commit have different reflogs
    tip = next(((ref, t) for r, ref, t in _tips() if r == main and t), None)
    hit = _LANDED_PATHS.get((main, name))
    if hit and tip and hit[0] == tip:
        return hit[1]
    deadline = Deadline(2.0)
    try:
        rc, ref = _git(main, deadline, "symbolic-ref", "-q", "HEAD", ok=(0, 1))
        if rc != 0 or not ref.strip().startswith("refs/heads/"):
            return None
        raw = _git(main, deadline, "reflog", "show", "--format=%gs", f"-n{CANVAS_CAP}",
                   ref.strip(), "--")[1]
    except _OutOfTime:
        return None
    except Exception as exc:  # noqa: BLE001 — a lookup aid: None, never raised
        logger.warning("session_landings: %s unreadable: %s", main, exc)
        return None
    keys = set()
    for msg in raw.splitlines():
        m = _STAMP.match(msg)
        if m and m.group(1) == f"session/{m.group(2)}-{name}":
            keys.add(m.group(2))
    out = frozenset(keys)
    if tip:
        if len(_LANDED_PATHS) >= _LANDED_PATHS_MAX:
            _LANDED_PATHS.pop(next(iter(_LANDED_PATHS)))
        _LANDED_PATHS[(main, name)] = (tip, out)
    return out


def _bound_checkouts() -> list[str]:
    """The bound repos' main checkouts (a linked worktree or a pipeline run's is
    not one: their ``.git`` is a file)."""
    try:
        from core.needs_human_review import _bound_worktree_roots
        from core.project_registry import get_swarmws
        roots = [os.path.realpath(p) for p, _r in _bound_worktree_roots(str(get_swarmws()))]
    except Exception as exc:  # noqa: BLE001 — no roots: no landings to read
        logger.warning("session_landings: bound repos unavailable: %s", exc)
        return []
    return sorted({r for r in roots if os.path.isdir(os.path.join(r, ".git"))})
