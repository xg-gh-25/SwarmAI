"""canvas_session — what ONE chat session produced, for its Canvas tabs.

Never shows another session's work, and is the same live, after a reload or a restart:

- **Artifacts** ← core/session_outputs (the session's attribution record, written as
  its turns run), each row joined with the workspace product catalog
  (ProductRegistry.lookup: role, label, favorite — the same answer the left-nav
  Artifacts overlay gives for that file) and with whether the file is in its repo's
  HEAD (``inHead``: the rail's NEW / UPD badge; one ``git ls-tree`` per repo). A
  session recorded before that store existed is imported once from its
  ``turn_deliverables`` blocks.
- **Changes** ← the pipeline runs the session OWNS (run.json ``session_id`` /
  ``session_history``, the owner gate's record — core/run_owner_gate). A run's code
  lives in trees only that run writes (docs/design/pipeline-run-worktree.md), so
  nothing has to be attributed:

    live   — each ``active`` worktree of the run: its change set against its HEAD
             (the run's base; run_worktree.change_tree plus its untracked new files,
             the run's deliverables dir left out). Located from run_id + repo name
             only (run_worktree.wt_path) and used only when git lists it on the
             run's branch — a path in run.json is never trusted.
    landed — the commits run.json records, each with its own files; ``pushed`` when
             the repo's upstream contains it.
    released — a worktree released without landing (nothing left to diff): said, not
             guessed.

  An edit made straight in a shared checkout is not a run's and is not listed.

The two parts are independent requests (``parts``): Artifacts are a file read on the
single-worker ``canvas_session`` pool (behind the session_outputs writes the router
submitted first); Changes run their git on the ``canvas_changes`` pool, so an
Artifacts refresh never waits for git. Commit facts are immutable and cached by sha.

Public: ``build``, ``artifact_rows``, ``in_head``, ``session_runs``, ``run_changes``,
``deliverable_turns``, ``MAX_FILES``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MAX_FILES = 200          # per session (Artifacts), per tree (Changes)
MAX_COMMITS = 50         # per run
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")
BACKFILL_NEEDLES = ['"type": "turn_deliverables"']


def deliverable_turns(rows: Iterable[tuple[str, list]]) -> list[tuple[str, list[dict]]]:
    """``(created_at, files)`` of each turn_deliverables block (backfill input)."""
    out = []
    for at, blocks in rows:
        for blk in blocks:
            if isinstance(blk, dict) and blk.get("type") == "turn_deliverables":
                out.append((at, [f for f in blk.get("files") or [] if isinstance(f, dict)]))
    return out


# ── in HEAD? (the rail's NEW / UPD) ─────────────────────────────────────────
# A directory's repo root, found the way git finds it (the nearest `.git` entry
# upward); memoised per directory, a repo root does not move.
_ROOT_OF: dict[str, Optional[str]] = {}


def _repo_root(directory: str) -> Optional[str]:
    seen = []
    d = directory
    while True:
        if d in _ROOT_OF:
            root = _ROOT_OF[d]
            break
        seen.append(d)
        if os.path.lexists(os.path.join(d, ".git")):
            root = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            root = None
            break
        d = parent
    if len(_ROOT_OF) > 4096:
        _ROOT_OF.clear()
    for x in seen:
        _ROOT_OF[x] = root
    return root


def in_head(paths: list[str]) -> dict[str, Optional[bool]]:
    """{absolute path: is it in its repo's HEAD} — True (tracked: the rail's UPD),
    False (not in HEAD: NEW), None (no repo, no file, or git could not say). One
    ``git ls-tree`` per repo. Blocking."""
    from core.run_worktree import git
    out: dict[str, Optional[bool]] = {p: None for p in paths}
    by_root: dict[str, dict[str, str]] = {}
    for p in paths:
        real = os.path.realpath(p)
        if not os.path.isfile(real):
            continue
        root = _repo_root(os.path.dirname(real))
        if root:
            by_root.setdefault(root, {})[os.path.relpath(real, root)] = p
    for root, rels in by_root.items():
        r = git(root, "ls-tree", "-z", "--name-only", "--full-tree", "HEAD", "--", *rels,
                env={"GIT_LITERAL_PATHSPECS": "1"})
        if r.returncode != 0:
            continue          # no HEAD yet / git error: unknown, not NEW
        tracked = set(r.stdout.split("\0"))
        for rel, p in rels.items():
            out[p] = rel in tracked
    return out


def artifact_rows(rows: list[dict], workspace: Optional[str]) -> list[dict]:
    """Attribution rows (session_outputs.read) joined with the product catalog, with
    existence and whether the file is in its repo's HEAD. Blocking."""
    rows = rows[:MAX_FILES]
    facts: dict[str, dict] = {}
    if workspace and rows:
        try:
            from core.product_registry import ProductRegistry
            facts = ProductRegistry(workspace).lookup([r["absolutePath"] for r in rows])
        except Exception:  # noqa: BLE001 — the catalog is a label aid; rows still list
            logger.warning("canvas_session: product lookup failed", exc_info=True)
    try:
        heads = in_head([r["absolutePath"] for r in rows if not r.get("deleted")])
    except Exception:  # noqa: BLE001 — the badge is an aid; rows still list
        logger.warning("canvas_session: in-HEAD lookup failed", exc_info=True)
        heads = {}
    out = []
    for r in rows:
        f = facts.get(r["absolutePath"], {})
        out.append({**r, "role": f.get("role", "Other"), "product": bool(f.get("product")),
                    "starred": bool(f.get("starred")),
                    "displayLabel": f.get("displayLabel") or os.path.basename(r["path"]),
                    "exists": os.path.exists(r["absolutePath"]),
                    "inHead": heads.get(r["absolutePath"]),
                    "writes": r.get("writes", 1)})
    return out


# ── the run index ───────────────────────────────────────────────────────────
# run.json files are many (hundreds) and change rarely: listed with scandir and
# parsed once per mtime.
_RUN_CACHE: dict[str, tuple[float, dict]] = {}


def _run_jsons(workspace: str) -> list[str]:
    out = []
    try:
        projects = list(os.scandir(os.path.join(workspace, "Projects")))
    except OSError:
        return out
    for p in projects:
        runs = os.path.join(p.path, ".artifacts", "runs")
        try:
            out.extend(os.path.join(e.path, "run.json") for e in os.scandir(runs) if e.is_dir())
        except OSError:
            continue
    return out


def _run_facts(path: str) -> Optional[dict]:
    """The facts Changes needs from one run.json (owners, worktrees, commits), cached
    by mtime; None when it is gone or unreadable."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _RUN_CACHE.pop(path, None)
        return None
    hit = _RUN_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    owners = {data.get("session_id")} if isinstance(data.get("session_id"), str) else set()
    for h in data.get("session_history") or []:
        if isinstance(h, dict) and isinstance(h.get("session_id"), str):
            owners.add(h["session_id"])
    req = data.get("requirement")
    title = (req.strip().splitlines() or [""])[0][:120] if isinstance(req, str) else ""
    facts = {
        "run": str(data.get("id") or os.path.basename(os.path.dirname(path))),
        "owners": owners, "title": title, "status": str(data.get("status") or ""),
        "project": data.get("project") if isinstance(data.get("project"), str) else None,
        "createdAt": str(data.get("created_at") or ""),
        "worktrees": [w for w in data.get("worktrees") or [] if isinstance(w, dict)],
        "commits": [{"repo": str(c.get("repo") or ""), "sha": str(c.get("sha") or "")}
                    for c in data.get("commits") or []
                    if isinstance(c, dict) and _SHA.fullmatch(str(c.get("sha") or ""))],
    }
    _RUN_CACHE[path] = (mtime, facts)
    return facts


def session_runs(session_id: str, workspace: str) -> list[dict]:
    """The runs *session_id* owned or took over (run.json), newest first. Blocking: a
    stat per run.json, a parse only of the changed ones."""
    runs = [f for f in map(_run_facts, _run_jsons(workspace)) if f and session_id in f["owners"]]
    return sorted(runs, key=lambda f: f["createdAt"], reverse=True)


# ── one run's changes (blocking, read-only git) ─────────────────────────────

def _git(cwd: str, *args: str) -> str:
    from core.run_worktree import _out
    return _out(cwd, *args)


def _known_repo(main: str) -> Optional[str]:
    """*main* as a real path when it is a code repo Canvas may read, else None."""
    from core.turn_changes import allowed_repo
    real = os.path.realpath(main) if main else ""
    return real if real and os.path.isdir(real) and allowed_repo(real) else None


def _live_worktree(run_id: str, entry: dict, main: str) -> Optional[str]:
    """The run's worktree for *entry* — derived from run_id + repo name, used only when
    git lists it on the run's branch — or None."""
    from core import run_worktree as rw
    name = str(entry.get("repo_name") or "")
    if entry.get("state") != "active" or not rw.REPO_NAME_RE.fullmatch(name):
        return None
    try:
        path = rw.wt_path(run_id, name)
    except Exception:  # noqa: BLE001 — a malformed id is no worktree
        logger.debug("worktree path failed run=%s repo=%s", run_id, name, exc_info=True)
        return None
    if os.path.realpath(str(path)) != str(path) or not rw._no_symlinks(path) or not path.is_dir():
        return None
    return str(path) if rw.is_registered(main, run_id, name) else None


def _tree_changes(run_id: str, wt: str) -> list[dict]:
    """The worktree's change set against its HEAD: one row per file."""
    from core import run_worktree as rw
    from core.turn_changes import _parse_name_status, _parse_numstat
    base = _git(wt, "rev-parse", "HEAD^{tree}").strip()
    tree = rw.change_tree(wt, run_id, add=rw.untracked_new_files(wt, run_id))
    if tree == base:
        return []
    numstat = _parse_numstat(_git(wt, "diff-tree", "-r", "--no-renames", "--numstat", "-z", base, tree))
    status = _parse_name_status(_git(wt, "diff-tree", "-r", "--no-renames", "--name-status", "-z", base, tree))
    rows = []
    for path in sorted(numstat)[:MAX_FILES]:
        added, deleted = numstat[path]
        rows.append({"path": path, "absolutePath": os.path.join(wt, path),
                     "status": status.get(path, "modified"), "added": added, "deleted": deleted,
                     "start": base, "end": tree})
    return rows


# Commits never change: (repo, sha as recorded) -> facts. Bounded.
_COMMITS: dict[tuple[str, str], dict] = {}
_PUSHED: set[tuple[str, str]] = set()
_COMMITS_MAX = 4000
_commit_lock = threading.Lock()


def _commit_facts(root: str, shas: list[str]) -> dict[str, dict]:
    """{recorded sha: {sha, short, subject, at, files}} for the commits of *root* —
    one ``git log`` for the ones not cached; a sha the repo no longer has is absent."""
    with _commit_lock:
        want = [s for s in shas if (root, s) not in _COMMITS]
    if want:
        out = _git(root, "log", "--no-walk=unsorted", "--ignore-missing", "--no-renames",
                   "--format=%x1e%H%x1f%s%x1f%cI", "--numstat", "-z", *want)
        got: dict[str, dict] = {}
        for chunk in out.split("\x1e"):
            head, _, rest = chunk.partition("\0")
            parts = head.split("\x1f")
            if len(parts) != 3:
                continue
            files = []
            for rec in rest.lstrip("\n").split("\0"):
                f = rec.split("\t", 2)
                if len(f) == 3:
                    files.append({"path": f[2], "added": None if f[0] == "-" else int(f[0]),
                                  "deleted": None if f[1] == "-" else int(f[1])})
            got[parts[0]] = {"sha": parts[0], "short": parts[0][:8], "subject": parts[1],
                             "at": parts[2], "files": files[:MAX_FILES]}
        with _commit_lock:
            for s in want:
                hit = next((c for full, c in got.items() if full.startswith(s.lower())), None)
                if hit:
                    if len(_COMMITS) >= _COMMITS_MAX:
                        _COMMITS.clear()
                    _COMMITS[(root, s)] = hit
    with _commit_lock:
        return {s: _COMMITS[(root, s)] for s in shas if (root, s) in _COMMITS}


def _pushed(root: str, shas: list[str]) -> set[str]:
    """The full shas of *shas* the repo's upstream contains (once true, cached)."""
    upstream = _git_ok(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        return set()
    from core.run_worktree import git
    out = set()
    for sha in shas:
        if (root, sha) in _PUSHED:
            out.add(sha)
            continue
        if git(root, "merge-base", "--is-ancestor", sha, upstream).returncode == 0:
            _PUSHED.add((root, sha))
            out.add(sha)
    return out


def _git_ok(cwd: str, *args: str) -> str:
    from core.run_worktree import git
    p = git(cwd, *args)
    return p.stdout.strip() if p.returncode == 0 else ""


def run_changes(run: dict) -> Optional[dict]:
    """One owned run's Changes: {run, title, status, project, repos[]}; None when the
    run has nothing to show. Blocking."""
    repos: dict[str, dict] = {}

    def repo_row(main: str) -> dict:
        return repos.setdefault(main, {"root": main, "repo": os.path.basename(main), "worktree": None,
                                       "branch": None, "files": [], "commits": [], "released": False})

    for entry in run["worktrees"]:
        main = _known_repo(str(entry.get("main") or ""))
        if not main:
            continue
        wt = _live_worktree(run["run"], entry, main)
        if wt:
            row = repo_row(main)
            row["worktree"], row["branch"] = wt, f"run/{run['run']}"
            try:
                row["files"] = _tree_changes(run["run"], wt)
            except Exception as exc:  # noqa: BLE001 — one tree failing does not hide the rest
                logger.info("canvas_session: %s changes unreadable: %s", wt, exc)
                row["error"] = str(exc)[:200]
        elif entry.get("state") == "released" and not entry.get("integrated") and not run["commits"]:
            repo_row(main)["released"] = True

    by_repo: dict[str, list[str]] = {}
    for c in run["commits"]:
        main = _known_repo(c["repo"])
        if main:
            by_repo.setdefault(main, []).append(c["sha"])
    for main, shas in by_repo.items():
        try:
            facts = _commit_facts(main, shas[:MAX_COMMITS])
            pushed = _pushed(main, [f["sha"] for f in facts.values()])
        except Exception as exc:  # noqa: BLE001
            logger.info("canvas_session: commits of %s unreadable: %s", main, exc)
            repo_row(main)["error"] = str(exc)[:200]
            continue
        repo_row(main)["commits"] = [{**f, "pushed": f["sha"] in pushed}
                                     for f in sorted(facts.values(), key=lambda f: f["at"], reverse=True)]

    rows = [r for r in repos.values() if r["files"] or r["commits"] or r["released"] or r.get("error")]
    if not rows:
        return None
    return {"run": run["run"], "title": run["title"], "status": run["status"],
            "project": run["project"], "createdAt": run["createdAt"], "repos": rows}


def changes_of(session_id: str, workspace: Optional[str]) -> list[dict]:
    """Changes of every run the session owns, newest first. Blocking."""
    if not workspace:
        return []
    return [c for c in map(run_changes, session_runs(session_id, workspace)) if c]


# ── the request ─────────────────────────────────────────────────────────────

def _artifacts(session_id: str, workspace: Optional[str], backfill_turns: Optional[list]) -> list[dict]:
    """Blocking, on the canvas_session pool (after the router's queued writes)."""
    from core import session_outputs
    if backfill_turns is not None:
        session_outputs.backfill(session_id, backfill_turns)
    return artifact_rows(session_outputs.read(session_id)["rows"], workspace)


async def build(session_id: str, parts: str = "all") -> dict[str, Any]:
    """{artifacts, changes} of one session (module docstring). *parts*: ``artifacts``,
    ``changes`` or ``all``; a part not asked for is None ("not computed", not "none")."""
    import asyncio
    from core import executors, session_outputs
    from core.project_registry import get_swarmws
    from database import db
    try:
        workspace: Optional[str] = str(get_swarmws())
    except Exception as exc:  # noqa: BLE001 — no workspace: no roles, no runs
        logger.debug("canvas_session: no workspace for %s: %s", session_id, exc)
        workspace = None

    async def artifacts() -> list[dict]:
        backfill_turns = None
        if not await executors.run_in("canvas_session", lambda: session_outputs.read(session_id)["backfilled"]):
            backfill_turns = deliverable_turns(await db.messages.list_session_blocks(session_id, BACKFILL_NEEDLES))
        return await executors.run_in("canvas_session", _artifacts, session_id, workspace, backfill_turns)

    async def changes() -> list[dict]:
        return await executors.run_in("canvas_changes", changes_of, session_id, workspace)

    want_a, want_c = parts in ("all", "artifacts"), parts in ("all", "changes")
    a, c = await asyncio.gather(artifacts() if want_a else _none(), changes() if want_c else _none())
    return {"artifacts": a, "changes": c}


async def _none() -> None:
    return None
