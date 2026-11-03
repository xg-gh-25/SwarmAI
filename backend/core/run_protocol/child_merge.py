"""The merge tool of a Swarm Run (design docs/design/2026-10-31-swarm-run.md §5.4, §6
point 2; impl docs/design/2026-11-03-swarm-run-impl.md step 5). Optional: a lead run may
read ``refs/hive/...`` and write the change itself instead.

``merge(engine, dispatch_ids)``: each child's commits are taken back into
``refs/hive/<dispatch>/<key>`` (Swarm Out's ``return-prepare``, the lead run's command)
and put, in the order given, on a chain of commits on this run's snapshot ref
``refs/swarm/snapshots/<run>/chain-<repo>``. The chain starts with a commit of the run
worktree's change as it is now (tracked edits and new untracked files), parent the
worktree's HEAD (the run's base). Each child is a three-way ``merge-tree`` of the
chain's tip and the child's tip over the child's own base; a conflict is reported and
leaves the chain as it was. A child whose commits change a path that makes git run code
here on its own (``hive_return.GIT_PROTECTED``) is refused, the rule an agent's landing
follows; the paths a child pipeline run writes under ``.aidlc/deliverables/`` are left out.

``apply(engine)``: the chain's tip is laid into the run worktree (``read-tree --reset
-u``, then the index back to HEAD: HEAD stays the run's base and the change stays
uncommitted, the worktree invariant). Edits made in the worktree since the chain last
took it are first put on the chain too, so nothing there is overwritten.

Only the run's snapshot refs and its worktree are written: main, the branches and the
child's ``refs/hive`` refs are never moved (I8). Each repository's work is under the
run's operation lock (core/run_protocol/oplock).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from core import run_worktree as rw

_LABEL_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_DELIVERABLES = ".aidlc/deliverables/"


class MergeError(ValueError):
    """The merge or apply cannot be done as asked; the message says why."""


def _label(kind: str, repo: str) -> str:
    return f"{kind}-{_LABEL_SAFE.sub('_', repo)}"[:32]


def _rev_or_none(cwd: Path, ref: str) -> "str | None":
    p = rw.git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    out = (p.stdout or "").strip() if isinstance(p.stdout, str) else (p.stdout or b"").decode().strip()
    return out if p.returncode == 0 and out else None


def _tree_of(cwd: Path, commit: str) -> str:
    return rw._out(cwd, "rev-parse", f"{commit}^{{tree}}").strip()


def _worktree_tree(wt: Path, run_id: str) -> str:
    return rw.change_tree(wt, run_id, add=rw.untracked_new_files(wt, run_id))


def _commit(wt: Path, tree: str, parent: str, msg: str) -> str:
    return rw._out(wt, "commit-tree", tree, "-p", parent, "-m", msg).strip()


def _merge_tree(wt: Path, base: str, ours: str, theirs: str) -> "tuple[str | None, list[str]]":
    """(tree, []) when clean; (None, conflicted paths) on a conflict."""
    p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "merge-tree", "--write-tree",
                        "--name-only", f"--merge-base={base}", ours, theirs],
                       cwd=str(wt), capture_output=True, text=True, timeout=120, check=False)
    lines = [x for x in p.stdout.splitlines()]
    if p.returncode == 0 and lines:
        return lines[0].strip(), []
    if p.returncode == 1:
        names = []
        for x in lines[1:]:
            if not x.strip():
                break
            names.append(x.strip())
        return None, names
    raise MergeError(f"git merge-tree failed: {p.stderr.strip()[:200]}")


def _without_deliverables(wt: Path, tree: str, keep_from: str) -> str:
    """*tree* with every path under ``.aidlc/deliverables/`` as it is in *keep_from*."""
    paths = [p for p in rw._out(wt, "ls-tree", "-r", "--name-only", "-z", tree).split("\0")
             if p.startswith(_DELIVERABLES)]
    kept = {p for p in rw._out(wt, "ls-tree", "-r", "--name-only", "-z", keep_from).split("\0")
            if p.startswith(_DELIVERABLES)}
    drop = [p for p in paths if p not in kept]
    if not drop:
        return tree
    tmp, env = rw._temp_index(wt)
    try:
        rw._out(wt, "read-tree", tree, env=env)
        rw._out(wt, "rm", "-q", "--cached", "--", *drop, env={**env, "GIT_LITERAL_PATHSPECS": "1"})
        return rw._out(wt, "write-tree", env=env).strip()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _repo_of(engine, prepared: dict) -> "tuple[str, Path] | tuple[None, None]":
    """The run's repository name and worktree for a prepared return (matched by the
    main checkout it was taken into)."""
    roots = engine.contract.get("repo_roots") or {}
    repos = engine.effective_repos()
    want = Path(str(prepared.get("repo") or "")).resolve()
    for name, root in roots.items():
        try:
            same = Path(str(root)).resolve() == want
        except OSError:
            same = False
        if same and name in repos:
            return name, Path(repos[name])
    return None, None


def _chain_start(wt: Path, run_id: str, repo: str) -> "tuple[str, str]":
    """The chain's ref and its tip, starting it at the worktree's change when new."""
    chain = rw.snapshot_ref(run_id, _label("chain", repo))
    applied = rw.snapshot_ref(run_id, _label("took", repo))
    tip = _rev_or_none(wt, chain)
    if tip is None:
        head = rw._rev(wt, "HEAD")
        tip = _commit(wt, _worktree_tree(wt, run_id), head, f"swarm run {run_id}: the worktree's change")
        rw._out(wt, "update-ref", chain, tip)
        rw._out(wt, "update-ref", applied, tip)
    return chain, tip


def merge(engine, dispatch_ids: list[str]) -> dict[str, Any]:
    from core import hive_return
    from core.run_protocol import child_cli
    from core.run_protocol.oplock import operation
    from config import get_app_data_dir
    out: list[dict[str, Any]] = []
    for did in dispatch_ids:
        row = child_cli._own(engine.run_id, did)
        repos = [r for r in row.get("repos") or [] if isinstance(r, dict) and r.get("access") == "write"]
        if not repos:
            out.append({"dispatch_id": did, "ok": False, "detail": "this child brings no commits back"})
            continue
        for r in repos:
            got = child_cli._http("POST", f"/runs/{did}/return/prepare",
                                  {"by": "agent", "session": child_cli._session(), "url": r.get("url"),
                                   "why": f"take {row.get('wp')}'s commits into Swarm Run {engine.run_id}"})
            if got.get("ok") is False:
                out.append({"dispatch_id": did, "repo": r.get("name"), "ok": False, "detail": got.get("detail")})
                continue
            git_exec = [p for p in got.get("protected_files") or []
                        if hive_return._is_protected(p, list(hive_return.GIT_PROTECTED))]
            if git_exec:
                out.append({"dispatch_id": did, "repo": r.get("name"), "ok": False, "refused": True,
                            "detail": "these commits change files that make this machine run code on its "
                                      "own (git hooks, filters, submodules): " + ", ".join(git_exec[:10])
                                      + "; re-run the child asking it to leave them out"})
                continue
            name, wt = _repo_of(engine, got)
            if wt is None:
                out.append({"dispatch_id": did, "repo": r.get("name"), "ok": False,
                            "detail": "this run has no worktree of that repository"})
                continue
            with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", engine.run_id):
                chain, tip = _chain_start(wt, engine.run_id, name)
                # over the commit the child started from (its declared sha), else prepare's base
                tree, conflicts = _merge_tree(wt, r.get("sha") or got["base"], tip, got["tip"])
                if tree is None:
                    out.append({"dispatch_id": did, "repo": name, "ok": False, "conflicts": conflicts,
                                "detail": "these paths conflict with what the chain has; nothing was "
                                          "changed (write the change yourself, or re-run the child on it)"})
                    continue
                tree = _without_deliverables(wt, tree, tip)
                new = _commit(wt, tree, tip, f"swarm run {engine.run_id}: {row.get('wp')} ({did})")
                rw._out(wt, "update-ref", chain, new, tip)
            out.append({"dispatch_id": did, "repo": name, "ok": True, "chain": chain, "tip": new,
                        "files": [f.get("path") for f in got.get("files") or [] if isinstance(f, dict)][:50]})
    return {"ok": bool(out) and all(x.get("ok") for x in out), "merged": out,
            "next": "`pipeline-v1 child apply` lays the chain into the worktree"}


def apply(engine) -> dict[str, Any]:
    from core.run_protocol.oplock import operation
    from config import get_app_data_dir
    out: list[dict[str, Any]] = []
    with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", engine.run_id):
        for name, path in engine.effective_repos().items():
            wt = Path(path)
            chain = rw.snapshot_ref(engine.run_id, _label("chain", name))
            took = rw.snapshot_ref(engine.run_id, _label("took", name))
            tip, last = _rev_or_none(wt, chain), _rev_or_none(wt, took)
            if tip is None or last is None:
                continue
            now = _worktree_tree(wt, engine.run_id)
            if now != _tree_of(wt, last):
                # edited since the chain last took the worktree: those edits go on the chain
                mine = _commit(wt, now, last, f"swarm run {engine.run_id}: the worktree's edits")
                tree, conflicts = _merge_tree(wt, last, tip, mine)
                if tree is None:
                    out.append({"repo": name, "ok": False, "conflicts": conflicts,
                                "detail": "the worktree's edits conflict with the merged children; "
                                          "nothing was changed"})
                    continue
                new = _commit(wt, tree, tip, f"swarm run {engine.run_id}: the worktree's edits")
                rw._out(wt, "update-ref", chain, new, tip)
                tip = new
            # the index holds the worktree's files, so no untracked file is "in the way";
            # the tip is laid in, then the index goes back to HEAD (the change unstaged)
            rw._out(wt, "read-tree", now)
            rw._out(wt, "read-tree", "--reset", "-u", tip)
            rw._out(wt, "read-tree", "HEAD")
            rw._out(wt, "update-ref", took, tip)
            out.append({"repo": name, "ok": True, "tip": tip})
    if not out:
        return {"ok": False, "error": "nothing merged yet: `pipeline-v1 child merge <dispatch>…` first"}
    return {"ok": all(x.get("ok") for x in out), "applied": out}
