"""canvas_session — what ONE chat session produced, for its Canvas tabs.

Built only from the session's own persisted turn records, so it is the same after a
reload or a restart and never shows another session's work:

- **Artifacts** ← every ``turn_deliverables`` block (session_router: the content /
  knowledge files each turn wrote). One row per file: kind, product role
  (product_registry.derive_role), first / last turn time, how many turns wrote it,
  whether it still exists.
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

  The repo's commits are the ones found above plus the commits of pipeline runs
  this session surfaced (``surface_run_outputs`` → run.json ``commits``).

A turn still streaming has no block yet: the live rail (swarm:file-changed) covers
it until its ``result``. Git is read-only (core/turn_changes._git, literal
pathspecs) and only for a repo turn_changes.allowed_repo accepts. Blocking: run
``git_state`` in an executor pool; ``build`` does.

Public: ``build``, ``extract``, ``git_state``, ``artifact_rows``, ``MAX_FILES``.
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
NEEDLES = ['"type": "turn_deliverables"', '"type": "turn_changes"', "surface_run_outputs"]


# ── extraction (pure) ───────────────────────────────────────────────────────

def extract(rows: Iterable[tuple[str, list]]) -> dict[str, Any]:
    """The session's records from ``(created_at, blocks)`` rows, oldest first:
    ``artifacts`` {absolutePath: row}, ``edits`` {gitRoot: {path: row}},
    ``runs`` [run_id] (first-seen order)."""
    artifacts: dict[str, dict] = {}
    edits: dict[str, dict[str, dict]] = {}
    runs: list[str] = []
    for at, blocks in rows:
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            kind = blk.get("type")
            if kind == "turn_deliverables":
                for f in blk.get("files") or []:
                    if not isinstance(f, dict):
                        continue
                    ap = f.get("absolutePath") or f.get("path")
                    if not isinstance(ap, str) or not ap:
                        continue
                    row = artifacts.get(ap)
                    if row is None:
                        artifacts[ap] = {"path": str(f.get("path") or ap), "absolutePath": ap,
                                         "kind": f.get("kind"), "firstAt": at, "lastAt": at, "turns": 1}
                    else:
                        row.update(lastAt=at, turns=row["turns"] + 1, kind=f.get("kind") or row["kind"])
            elif kind == "turn_changes":
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
            elif kind == "tool_use" and str(blk.get("name") or "").endswith("surface_run_outputs"):
                rid = (blk.get("input") or {}).get("run_id") if isinstance(blk.get("input"), dict) else None
                if isinstance(rid, str) and _RUN_ID.fullmatch(rid) and rid not in runs:
                    runs.append(rid)
    return {"artifacts": artifacts, "edits": edits, "runs": runs}


def artifact_rows(artifacts: dict[str, dict], workspace: Optional[str]) -> list[dict]:
    """Artifacts newest first, with role and existence."""
    from core.product_registry import derive_role
    ws = os.path.realpath(workspace) if workspace else None
    out = []
    for row in artifacts.values():
        ap = row["absolutePath"]
        real = os.path.realpath(ap)
        role = "Other"
        if ws and real.startswith(ws + os.sep):
            try:
                role = derive_role(os.path.relpath(real, ws), None).value
            except Exception:  # noqa: BLE001 — a role is a grouping aid
                logger.debug("canvas_session: derive_role failed for %s", ap, exc_info=True)
        out.append({**row, "role": role, "exists": os.path.exists(ap)})
    out.sort(key=lambda r: r["lastAt"], reverse=True)
    return out[:MAX_FILES]


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


def run_commits(run_id: str, workspace: str) -> list[dict]:
    """A pipeline run's recorded commits (run.json ``commits``): [{repoPath, sha, files}]."""
    if not _RUN_ID.fullmatch(run_id or ""):
        return []
    for run_json in Path(workspace).glob(f"Projects/*/.artifacts/runs/{run_id}/run.json"):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [{"repoPath": str(c.get("repo") or ""), "sha": str(c.get("sha") or ""),
                 "files": [str(f) for f in c.get("files") or [] if f], "run": run_id}
                for c in data.get("commits") or []
                if isinstance(c, dict) and _SHA.fullmatch(str(c.get("sha") or ""))]
    return []


def _with_runs(repos: dict[str, dict], extracted_runs: list[str], workspace: Optional[str]) -> None:
    """Add the session's pipeline-run commits to their repos (a repo only a run
    touched gets its own row)."""
    from core.turn_changes import _main_checkout, allowed_repo
    if not workspace:
        return
    for rid in extracted_runs:
        for c in run_commits(rid, workspace):
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
            row.setdefault("commits", []).append(
                {"sha": head[0], "short": head[1], "subject": head[2], "at": head[3],
                 "source": "run", "run": rid, "files": c["files"][:MAX_FILES]})


def changes_rows(extracted: dict[str, Any], workspace: Optional[str]) -> list[dict]:
    """Changes per repo (blocking): files + commits, repos named and projected."""
    from core.repo_changes import _bindings
    from core.turn_changes import _main_checkout, allowed_repo
    repos: dict[str, dict] = {}
    for root, files in extracted["edits"].items():
        real = os.path.realpath(root)
        if not os.path.isdir(real) or not allowed_repo(real):
            continue    # not a known code repo any more: its object store is not read
        repos[real] = git_state(real, files)
    _with_runs(repos, extracted["runs"], workspace)
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


async def build(session_id: str) -> dict[str, Any]:
    """{artifacts, changes} of one session (module docstring)."""
    from core import executors
    from core.project_registry import get_swarmws
    from database import db
    rows = await db.messages.list_session_blocks(session_id, NEEDLES)
    extracted = extract(rows)
    try:
        workspace: Optional[str] = str(get_swarmws())
    except Exception as exc:  # noqa: BLE001 — no workspace: no roles, no runs
        logger.debug("canvas_session: no workspace for %s: %s", session_id, exc)
        workspace = None
    artifacts = artifact_rows(extracted["artifacts"], workspace)
    changes = (await executors.run_in("turn_changes", changes_rows, extracted, workspace)
               if extracted["edits"] or extracted["runs"] else [])
    return {"artifacts": artifacts, "changes": changes}
