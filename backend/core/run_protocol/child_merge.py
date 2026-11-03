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
follows; ``.aidlc/deliverables/`` stays as the chain has it (a child pipeline run's own
records, added, changed or deleted there, are never the lead run's). One child's or
repository's failure is reported as that item's; the others go on.

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


#: the chain's and a child base's commits are the run's own snapshots (never landed, never
#: pushed): a fixed identity, so they need no git identity on the machine (a fresh Hive or
#: a CI runner has none)
_RUN_ID = {"GIT_AUTHOR_NAME": "swarm-run", "GIT_AUTHOR_EMAIL": "run@swarm.local",
           "GIT_COMMITTER_NAME": "swarm-run", "GIT_COMMITTER_EMAIL": "run@swarm.local"}


def _commit(wt: Path, tree: str, parent: str, msg: str) -> str:
    return rw._out(wt, "commit-tree", tree, "-p", parent, "-m", msg, env=_RUN_ID).strip()


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


def _subtree(wt: Path, commit_or_tree: str, path: str) -> "str | None":
    p = rw.git(wt, "rev-parse", "--verify", "--quiet", f"{commit_or_tree}:{path}")
    out = (p.stdout or "").strip() if isinstance(p.stdout, str) else (p.stdout or b"").decode().strip()
    return out if p.returncode == 0 and out else None


def _without_deliverables(wt: Path, tree: str, keep_from: str) -> str:
    """*tree* with ``.aidlc/deliverables/`` exactly as it is in *keep_from*: what a child
    added there is left out, and what it changed or deleted there is put back (a child
    pipeline run's own records are never the lead run's)."""
    where = _DELIVERABLES.rstrip("/")
    if _subtree(wt, tree, where) == _subtree(wt, keep_from, where):
        return tree
    tmp, env = rw._temp_index(wt)
    try:
        rw._out(wt, "read-tree", tree, env=env)
        # --force: only the private index is changed (its entries differ from HEAD and
        # the worktree by construction), never a file
        rw._out(wt, "rm", "-r", "-q", "--force", "--cached", "--ignore-unmatch", "--", where,
                env={**env, "GIT_LITERAL_PATHSPECS": "1"})
        kept = rw._out(wt, "ls-tree", "-r", "-z", "--full-tree", keep_from, "--", where)
        if kept:
            rw._out(wt, "update-index", "-z", "--index-info", env=env, input_text=kept)
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
    from core.run_protocol import child_cli
    out: list[dict[str, Any]] = []
    for did in dispatch_ids:
        try:
            row = child_cli._own(engine.run_id, did)
        except child_cli.ChildError as exc:
            out.append({"dispatch_id": did, "ok": False, "detail": str(exc)})
            continue
        repos = [r for r in row.get("repos") or [] if isinstance(r, dict) and r.get("access") == "write"]
        if not repos:
            out.append({"dispatch_id": did, "ok": False, "detail": "this child brings no commits back"})
            continue
        for r in repos:
            try:
                out.append(_merge_one(engine, did, row, r))
            except Exception as exc:  # noqa: BLE001 — this child's, named; the chain keeps what it took
                out.append({"dispatch_id": did, "repo": r.get("name"), "ok": False,
                            "detail": f"git failed on this child ({type(exc).__name__}: {str(exc)[:200]}); "
                                      "the chain keeps what was merged before it"})
    return {"ok": bool(out) and all(x.get("ok") for x in out), "merged": out,
            "next": "`pipeline-v1 child apply` lays the chain into the worktree"}


def _merge_one(engine, did: str, row: dict, r: dict) -> dict[str, Any]:
    """One child's commits in one repository onto the run's chain (see the module doc)."""
    from core import hive_return
    from core.run_protocol import child_cli
    from core.run_protocol.oplock import operation
    from config import get_app_data_dir
    got = child_cli._http("POST", f"/runs/{did}/return/prepare",
                          {"by": "agent", "session": child_cli._session(), "url": r.get("url"),
                           "why": f"take {row.get('wp')}'s commits into Swarm Run {engine.run_id}"})
    if got.get("ok") is False:
        return {"dispatch_id": did, "repo": r.get("name"), "ok": False, "detail": got.get("detail")}
    git_exec = [p for p in got.get("protected_files") or []
                if hive_return._is_protected(p, list(hive_return.GIT_PROTECTED))]
    if git_exec:
        return {"dispatch_id": did, "repo": r.get("name"), "ok": False, "refused": True,
                "detail": "these commits change files that make this machine run code on its "
                          "own (git hooks, filters, submodules): " + ", ".join(git_exec[:10])
                          + "; re-run the child asking it to leave them out"}
    name, wt = _repo_of(engine, got)
    if wt is None:
        return {"dispatch_id": did, "repo": r.get("name"), "ok": False,
                "detail": "this run has no worktree of that repository"}
    with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", engine.run_id):
        chain, tip = _chain_start(wt, engine.run_id, name)
        # over the commit the child started from (its declared sha), else prepare's base
        tree, conflicts = _merge_tree(wt, r.get("sha") or got["base"], tip, got["tip"])
        if tree is None:
            return {"dispatch_id": did, "repo": name, "ok": False, "conflicts": conflicts,
                    "detail": "these paths conflict with what the chain has; nothing was "
                              "changed (write the change yourself, or re-run the child on it)"}
        tree = _without_deliverables(wt, tree, tip)
        new = _commit(wt, tree, tip, f"swarm run {engine.run_id}: {row.get('wp')} ({did})")
        rw._out(wt, "update-ref", chain, new, tip)
    return {"dispatch_id": did, "repo": name, "ok": True, "chain": chain, "tip": new,
            "files": [f.get("path") for f in got.get("files") or [] if isinstance(f, dict)][:50]}


def apply(engine) -> dict[str, Any]:
    from core.run_protocol.oplock import operation
    from config import get_app_data_dir
    out: list[dict[str, Any]] = []
    with operation(get_app_data_dir() / "state" / "pipeline_agent_audit", engine.run_id):
        for name, path in engine.effective_repos().items():
            try:
                done = _apply_one(engine.run_id, name, Path(path))
            except Exception as exc:  # noqa: BLE001 — this repository's, named; the others go on
                done = {"repo": name, "ok": False,
                        "detail": f"git failed ({type(exc).__name__}: {str(exc)[:200]}); the worktree "
                                  "of this repository may be as it was or have the chain laid in: "
                                  "check `git status` there"}
            if done is not None:
                out.append(done)
    if not out:
        return {"ok": False, "error": "nothing merged yet: `pipeline-v1 child merge <dispatch>…` first"}
    return {"ok": all(x.get("ok") for x in out), "applied": out}


def _apply_one(run_id: str, name: str, wt: Path) -> "dict[str, Any] | None":
    """The chain's tip laid into one repository's worktree, or None when it has no chain."""
    chain = rw.snapshot_ref(run_id, _label("chain", name))
    took = rw.snapshot_ref(run_id, _label("took", name))
    tip, last = _rev_or_none(wt, chain), _rev_or_none(wt, took)
    if tip is None or last is None:
        return None
    now = _worktree_tree(wt, run_id)
    if now != _tree_of(wt, last):
        # edited since the chain last took the worktree: those edits go on the chain
        mine = _commit(wt, now, last, f"swarm run {run_id}: the worktree's edits")
        tree, conflicts = _merge_tree(wt, last, tip, mine)
        if tree is None:
            return {"repo": name, "ok": False, "conflicts": conflicts,
                    "detail": "the worktree's edits conflict with the merged children; nothing was changed"}
        new = _commit(wt, tree, tip, f"swarm run {run_id}: the worktree's edits")
        rw._out(wt, "update-ref", chain, new, tip)
        tip = new
    # the agent writes the worktree without the run's lock: an edit made while the
    # chain was being merged would be overwritten by the lay-in, so it is refused
    if _worktree_tree(wt, run_id) != now:
        return {"repo": name, "ok": False,
                "detail": "the worktree changed while the chain was being merged; nothing was laid in "
                          "(the chain has the earlier edits): run `child apply` again"}
    # the index holds the worktree's files, so no untracked file is "in the way";
    # the tip is laid in, then the index goes back to HEAD (the change unstaged)
    rw._out(wt, "read-tree", now)
    rw._out(wt, "read-tree", "--reset", "-u", tip)
    rw._out(wt, "read-tree", "HEAD")
    rw._out(wt, "update-ref", took, tip)
    # an edit that landed in the last moment before the lay-in is not silently lost:
    # the worktree is compared with what was laid in, and any path that differs is named
    after = _worktree_tree(wt, run_id)
    if after != _tree_of(wt, tip):
        paths = [x for x in rw._out(wt, "diff-tree", "-r", "--name-only", "-z", _tree_of(wt, tip), after)
                 .split("\0") if x]
        return {"repo": name, "ok": False, "tip": tip, "differs": paths[:50],
                "detail": "the chain was laid in, but the worktree changed meanwhile: these paths are "
                          "not what the chain has; check them (`git diff` there)"}
    return {"repo": name, "ok": True, "tip": tip}
