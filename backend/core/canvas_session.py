"""canvas_session — what ONE chat session produced, for its Canvas tabs.

Never shows another session's work, and is the same live, after a reload or a restart:

- **Artifacts** ← core/session_outputs (the session's attribution record, written as
  its turns run), each row joined with the workspace product catalog
  (ProductRegistry.lookup: role, label, favorite — the same answer the left-nav
  Artifacts overlay gives for that file). A session recorded before that store
  existed is imported once from its ``turn_deliverables`` blocks.
- **Changes** ← every ``turn_changes`` block (core/turn_changes: per turn, the code
  files it wrote, with the change trees before and after). Per repo, one row per file:
  the session's NET change (first turn's start tree → last turn's end tree, so a file
  edited in five turns is one row and a file put back as it was is no row), and where
  that content is NOW, found by content, not by who committed:

    ``committed`` — a local commit not on any remote introduces exactly the blob the
                    session left (its sha is given): robust to rebase / ff-merge /
                    worktree removal, and to other sessions' commits (they hold
                    other blobs).
    ``pushed``    — the branch's upstream holds that blob.
    ``uncommitted`` — the file on disk still is that blob, not yet committed.
    ``changed``   — none of these: edited (or reverted) after the session.

  The repo's commits are those found above, plus the commits of the pipeline runs
  the session OWNED (run.json ``session_id`` / ``session_history``, the owner gate's
  record — core/run_owner_gate) and of the runs whose output it surfaced (its
  ``source-final`` rows carry the commit as ``baseRef`` = ``<sha>^``).

A code edit of a turn still streaming has no turn_changes block yet; it shows at the
turn's result. Git is read-only (core/turn_changes._git, literal pathspecs) and only
for a repo turn_changes.allowed_repo accepts. Blocking parts run on the single-worker
``canvas_session`` pool, behind the session_outputs writes the router submitted first.

Public: ``build``, ``extract``, ``git_state``, ``artifact_rows``, ``session_runs``,
``MAX_FILES``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MAX_FILES = 200          # per session, each tab
MAX_COMMITS = 50         # per repo
_LOG_DEPTH = 400         # local-only commits searched per repo
_ZERO = "0" * 40
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
NEEDLES = ['"type": "turn_changes"']
BACKFILL_NEEDLES = ['"type": "turn_deliverables"']


# ── extraction (pure) ───────────────────────────────────────────────────────

def extract(rows: Iterable[tuple[str, list]]) -> dict[str, Any]:
    """The session's code edits from ``(created_at, blocks)`` rows, oldest first:
    ``edits`` {gitRoot: {path: row}}."""
    edits: dict[str, dict[str, dict]] = {}
    for at, blocks in rows:
        for blk in blocks:
            if not isinstance(blk, dict) or blk.get("type") != "turn_changes":
                continue
            for f in blk.get("files") or []:
                if not isinstance(f, dict):
                    continue
                root, rel = f.get("gitRoot"), f.get("path")
                start, end = f.get("start"), f.get("end")
                if not all(isinstance(v, str) and v for v in (root, rel, start, end)):
                    continue
                by = edits.setdefault(root, {})
                row = by.get(rel)
                if row is None:
                    by[rel] = {"path": rel, "repo": f.get("repo") or os.path.basename(root),
                               "absolutePath": f.get("absolutePath"), "start": start, "end": end,
                               "firstStatus": f.get("status"), "firstAt": at, "lastAt": at, "turns": 1,
                               "added": f.get("added"), "deleted": f.get("deleted")}
                else:
                    row.update(end=end, lastAt=at, turns=row["turns"] + 1,
                               absolutePath=f.get("absolutePath") or row["absolutePath"])
                    for k in ("added", "deleted"):   # turn sums: the fallback count only
                        if isinstance(f.get(k), int) and isinstance(row[k], int):
                            row[k] += f[k]
    return {"edits": edits}


def deliverable_turns(rows: Iterable[tuple[str, list]]) -> list[tuple[str, list[dict]]]:
    """``(created_at, files)`` of each turn_deliverables block (backfill input)."""
    out = []
    for at, blocks in rows:
        for blk in blocks:
            if isinstance(blk, dict) and blk.get("type") == "turn_deliverables":
                out.append((at, [f for f in blk.get("files") or [] if isinstance(f, dict)]))
    return out


def artifact_rows(rows: list[dict], workspace: Optional[str]) -> list[dict]:
    """Attribution rows (session_outputs.read) joined with the product catalog, with
    existence. Blocking."""
    rows = rows[:MAX_FILES]
    facts: dict[str, dict] = {}
    if workspace and rows:
        try:
            from core.product_registry import ProductRegistry
            facts = ProductRegistry(workspace).lookup([r["absolutePath"] for r in rows])
        except Exception:  # noqa: BLE001 — the catalog is a label aid; rows still list
            logger.warning("canvas_session: product lookup failed", exc_info=True)
    out = []
    for r in rows:
        f = facts.get(r["absolutePath"], {})
        out.append({**r, "role": f.get("role", "Other"), "product": bool(f.get("product")),
                    "starred": bool(f.get("starred")),
                    "displayLabel": f.get("displayLabel") or os.path.basename(r["path"]),
                    "exists": os.path.exists(r["absolutePath"]),
                    "turns": r.get("writes", 1)})
    return out


# ── git state (blocking) ────────────────────────────────────────────────────

def _git(root: str, *args: str, ok: Iterable[int] = (0,)) -> str:
    from core.turn_changes import _git as tc_git
    return tc_git(root, "-c", "core.quotePath=false", *args, ok=ok)


def _blobs(root: str, tree: str, rels: list[str]) -> dict[str, str]:
    """{path: blob} in *tree* (a path absent from it maps to the zero id)."""
    got = {rel: _ZERO for rel in rels}
    for rec in _git(root, "ls-tree", "-z", tree, "--", *rels).split("\0"):
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        if path in got and len(parts) == 3 and parts[1] == "blob":
            got[path] = parts[2]
    return got


def _local_commits(root: str, rels: list[str]) -> list[dict]:
    """Commits on a local branch and on no remote that touch *rels*, newest first,
    each with {path: new blob}."""
    out = _git(root, "log", "--branches", "--not", "--remotes", f"-n{_LOG_DEPTH}",
               "--no-renames", "--raw", "--no-abbrev",
               "--format=%x1e%H%x1f%h%x1f%s%x1f%cI", "--", *rels)
    commits = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 4:
            continue
        blobs = {}
        for line in rest.splitlines():
            if not line.startswith(":"):
                continue
            meta, _, path = line.partition("\t")
            m = meta.split()
            if len(m) >= 4:
                blobs[path] = m[3]
        commits.append({"sha": parts[0], "short": parts[1], "subject": parts[2], "at": parts[3],
                        "blobs": blobs})
    return commits


def _disk_blobs(root: str, files: dict[str, dict]) -> dict[str, Optional[str]]:
    """{path: blob of the file on disk now} (zero id: deleted; None: its worktree is
    gone, so the disk says nothing)."""
    out: dict[str, Optional[str]] = {}
    present: list[tuple[str, str]] = []
    for rel, row in files.items():
        ap = row.get("absolutePath") or os.path.join(root, rel)
        top = ap[: -len(rel)].rstrip(os.sep) if ap.endswith(rel) else os.path.dirname(ap)
        if not os.path.isdir(top):
            out[rel] = None
        elif os.path.isfile(ap):
            present.append((rel, ap))
        else:
            out[rel] = _ZERO
    if present:
        ids = _git(root, "hash-object", "--", *[ap for _, ap in present]).split()
        for (rel, _), blob in zip(present, ids):
            out[rel] = blob
    return out


def _net_counts(root: str, files: dict[str, dict]) -> None:
    """Set added/deleted to the net first-start → last-end counts (kept as the turn
    sums when the trees were pruned); drop files with no net change."""
    from core.turn_changes import _parse_numstat
    groups: dict[tuple[str, str], list[str]] = {}
    for rel, row in files.items():
        groups.setdefault((row["start"], row["end"]), []).append(rel)
    for (start, end), rels in groups.items():
        if start == end:
            for rel in rels:
                files.pop(rel)
            continue
        try:
            stat = _parse_numstat(_git(root, "diff", "--no-renames", "--no-ext-diff", "--no-textconv",
                                       "--numstat", "-z", start, end, "--", *rels))
        except RuntimeError:
            continue   # trees pruned: keep the turn sums
        for rel in rels:
            if rel not in stat:
                files.pop(rel)          # put back as it was
            else:
                files[rel]["added"], files[rel]["deleted"] = stat[rel]


def git_state(root: str, files: dict[str, dict]) -> dict[str, Any]:
    """One repo's Changes row: its files (net counts + state) and the commits that
    hold them. *files* is ``extract()['edits'][root]`` (copied, not mutated)."""
    files = {k: dict(v) for k, v in files.items()}
    row: dict[str, Any] = {"root": root, "repo": next(iter(files.values()))["repo"] if files else os.path.basename(root)}
    try:
        row["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        _net_counts(root, files)
        rels = sorted(files)[:MAX_FILES]
        files = {rel: files[rel] for rel in rels}
        ends: dict[str, list[str]] = {}
        for rel in rels:
            ends.setdefault(files[rel]["end"], []).append(rel)
        want: dict[str, Optional[str]] = {rel: None for rel in rels}
        for end, group in ends.items():
            try:
                want.update(_blobs(root, end, group))
            except RuntimeError:
                pass           # end tree pruned: state unknown
        commits = _local_commits(root, rels) if rels else []
        upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", ok=(0, 128)).strip()
        pushed = _blobs(root, upstream, rels) if upstream and rels else {}
        disk = _disk_blobs(root, files) if rels else {}
    except RuntimeError as exc:
        logger.info("canvas_session: git state of %s failed: %s", root, exc)
        return {**row, "error": str(exc)[:200], "files": [], "commits": []}

    used: dict[str, dict] = {}
    out_files = []
    for rel in rels:
        f, blob = files[rel], want[rel]
        state, sha = "changed", None
        if blob is not None:
            hit = next((c for c in commits if c["blobs"].get(rel) == blob), None)
            if hit:
                state, sha = "committed", hit["sha"]
                used.setdefault(hit["sha"], hit)
            elif pushed.get(rel) == blob:
                state = "pushed"
            elif disk.get(rel) == blob:
                state = "uncommitted"
        status = "deleted" if blob == _ZERO else ("added" if f.get("firstStatus") == "added" else "modified")
        out_files.append({"path": rel, "absolutePath": f.get("absolutePath"), "status": status,
                          "added": f.get("added"), "deleted": f.get("deleted"), "turns": f["turns"],
                          "firstAt": f["firstAt"], "lastAt": f["lastAt"],
                          "start": f["start"], "end": f["end"], "state": state, "commit": sha})
    row["upstream"] = upstream or None
    row["files"] = out_files
    row["commits"] = [{"sha": c["sha"], "short": c["short"], "subject": c["subject"], "at": c["at"],
                       "source": "session",
                       "files": sorted(p for p, b in c["blobs"].items() if p in files)}
                      for c in sorted(used.values(), key=lambda c: c["at"], reverse=True)][:MAX_COMMITS]
    return row


# run.json files are many (hundreds) and change rarely: parsed once per mtime.
_RUN_CACHE: dict[str, tuple[float, set[str], list[dict]]] = {}


def _run_facts(run_json: Path) -> tuple[set[str], list[dict]]:
    """(sessions that owned the run, its recorded commits) of one run.json, cached
    by mtime."""
    key = str(run_json)
    try:
        mtime = run_json.stat().st_mtime
    except OSError:
        _RUN_CACHE.pop(key, None)
        return set(), []
    hit = _RUN_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    try:
        data = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    owners = {data.get("session_id")} if isinstance(data.get("session_id"), str) else set()
    for h in data.get("session_history") or []:
        if isinstance(h, dict) and isinstance(h.get("session_id"), str):
            owners.add(h["session_id"])
    run_id = str(data.get("id") or run_json.parent.name)
    commits = [{"repoPath": str(c.get("repo") or ""), "sha": str(c.get("sha") or ""),
                "files": [str(f) for f in c.get("files") or [] if f], "run": run_id}
               for c in data.get("commits") or []
               if isinstance(c, dict) and _SHA.fullmatch(str(c.get("sha") or ""))]
    _RUN_CACHE[key] = (mtime, owners, commits)
    return owners, commits


# The run.json list itself (hundreds of dirs) is re-globbed at most every few seconds.
_RUN_LIST_TTL_S = 10.0
_run_list: dict[str, tuple[float, list[Path]]] = {}


def _run_jsons(workspace: str) -> list[Path]:
    import time
    hit = _run_list.get(workspace)
    if hit and time.monotonic() - hit[0] < _RUN_LIST_TTL_S:
        return hit[1]
    found = list(Path(workspace).glob("Projects/*/.artifacts/runs/*/run.json"))
    _run_list[workspace] = (time.monotonic(), found)
    return found


def session_runs(session_id: str, workspace: str) -> list[dict]:
    """Commits of the pipeline runs *session_id* owned (run.json session_id or
    session_history). Blocking (a stat per run.json; parses only changed ones)."""
    out = []
    for run_json in _run_jsons(workspace):
        owners, commits = _run_facts(run_json)
        if session_id in owners:
            out.extend(commits)
    return out


def surfaced_commits(artifacts: list[dict]) -> list[dict]:
    """Commits behind the session's source-final rows (baseRef = ``<sha>^``)."""
    by: dict[tuple[str, str], dict] = {}
    for a in artifacts:
        ref = a.get("baseRef") or ""
        if a.get("kind") != "source-final" or not ref.endswith("^") or not _SHA.fullmatch(ref[:-1]):
            continue
        ap = a.get("absolutePath") or ""
        rel = a.get("path") or ""
        repo = ap[: -len(rel)].rstrip(os.sep) if rel and ap.endswith(rel) else os.path.dirname(ap)
        c = by.setdefault((repo, ref[:-1]), {"repoPath": repo, "sha": ref[:-1], "files": [], "run": None})
        if rel:
            c["files"].append(rel)
    return list(by.values())


def _with_runs(repos: dict[str, dict], run_commits: list[dict]) -> None:
    """Add pipeline-run commits to their repos (a repo only a run touched gets its
    own row)."""
    from core.turn_changes import _main_checkout, allowed_repo
    for c in run_commits:
        if not c["repoPath"] or not os.path.isdir(c["repoPath"]):
            continue
        root = _main_checkout(os.path.realpath(c["repoPath"]))
        if not allowed_repo(root):
            continue
        try:
            head = _git(root, "log", "-1", "--format=%H%x1f%h%x1f%s%x1f%cI", c["sha"]).strip().split("\x1f")
        except RuntimeError:
            continue   # the run's commit is gone (rewritten): nothing to show
        if len(head) != 4:
            continue
        row = repos.setdefault(root, {"root": root, "repo": os.path.basename(root), "files": [], "commits": []})
        if any(x["sha"] == head[0] for x in row.get("commits", [])):
            continue
        commit = {"sha": head[0], "short": head[1], "subject": head[2], "at": head[3],
                  "source": "run", "files": c["files"][:MAX_FILES]}
        if c.get("run"):
            commit["run"] = c["run"]
        row.setdefault("commits", []).append(commit)


def changes_rows(extracted: dict[str, Any], run_commits: list[dict]) -> list[dict]:
    """Changes per repo (blocking): files + commits, repos named and projected."""
    from core.repo_changes import _bindings
    from core.turn_changes import _main_checkout, allowed_repo
    repos: dict[str, dict] = {}
    for root, files in extracted["edits"].items():
        real = os.path.realpath(root)
        if not os.path.isdir(real) or not allowed_repo(real):
            continue    # not a known code repo any more: its object store is not read
        repos[real] = git_state(real, files)
    _with_runs(repos, run_commits)
    bound = _bindings()
    by_main = {}
    for r, (name, project) in bound.items():
        try:
            by_main.setdefault(_main_checkout(r), (name, project))
        except Exception as exc:  # noqa: BLE001 — an unreadable binding groups under its own name
            logger.debug("canvas_session: main checkout of %s unknown: %s", r, exc)
            continue
    out = []
    for root, row in repos.items():
        name, project = bound.get(root) or by_main.get(root) or (row.get("repo"), None)
        row["repo"], row["project"] = name, project
        row.setdefault("upstream", None)
        row["commits"] = sorted(row.get("commits", []), key=lambda c: c["at"], reverse=True)[:MAX_COMMITS]
        if row.get("files") or row.get("commits") or row.get("error"):
            out.append(row)
    out.sort(key=lambda r: max([f["lastAt"] for f in r["files"]] + [c["at"] for c in r["commits"]] or [""]),
             reverse=True)
    return out


def _artifacts_only(session_id: str, workspace: Optional[str], backfill_turns: Optional[list]) -> list[dict]:
    from core import session_outputs
    if backfill_turns is not None:
        session_outputs.backfill(session_id, backfill_turns)
    return artifact_rows(session_outputs.read(session_id)["rows"], workspace)


def _artifacts_and_runs(session_id: str, workspace: Optional[str],
                        backfill_turns: Optional[list]) -> tuple[list[dict], list[dict]]:
    """Blocking, on the canvas_session pool (after the router's queued writes)."""
    from core import session_outputs
    if backfill_turns is not None:
        session_outputs.backfill(session_id, backfill_turns)
    rows = session_outputs.read(session_id)["rows"]
    artifacts = artifact_rows(rows, workspace)
    runs = surfaced_commits(rows)
    if workspace:
        runs = session_runs(session_id, workspace) + runs
    return artifacts, runs


async def build(session_id: str, changes: bool = True) -> dict[str, Any]:
    """{artifacts, changes} of one session (module docstring). ``changes=False``
    (the live refresh while a turn streams) skips the git work and the run index:
    ``changes`` is then None, meaning "not computed", not "none"."""
    from core import executors, session_outputs
    from core.project_registry import get_swarmws
    from database import db
    try:
        workspace: Optional[str] = str(get_swarmws())
    except Exception:  # noqa: BLE001 — no workspace: no roles, no runs
        workspace = None
    backfill_turns = None
    if not await executors.run_in("canvas_session", lambda: session_outputs.read(session_id)["backfilled"]):
        backfill_turns = deliverable_turns(await db.messages.list_session_blocks(session_id, BACKFILL_NEEDLES))
    if not changes:
        artifacts = await executors.run_in("canvas_session", _artifacts_only, session_id, workspace,
                                           backfill_turns)
        return {"artifacts": artifacts, "changes": None}
    artifacts, runs = await executors.run_in("canvas_session", _artifacts_and_runs, session_id, workspace,
                                             backfill_turns)
    extracted = extract(await db.messages.list_session_blocks(session_id, NEEDLES))
    rows = (await executors.run_in("canvas_session", changes_rows, extracted, runs)
            if extracted["edits"] or runs else [])
    return {"artifacts": artifacts, "changes": rows}
