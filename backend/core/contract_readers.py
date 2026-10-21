"""contract_readers — the one rule for the declared readers of a changed contract.

A run declares the readers of each contract it changes in two stores: the PLAN's
``implementation_constraints.consumers[].readers`` and, after PLAN, run.json
``consumer_readers`` (``run-update --add-consumer-reader``). Two readers use them: the
consumers gate (``scripts/consumers_gate.py``) and the committed DESIGN_SPEC.md
(``core/run_docs.py``). They once read the stores their own way — a first-match list
in the gate, PLAN readers only in the document — so a corrected declaration could not
take effect and an exemption accepted after PLAN never reached the document. Both now
read ``resolve``, with the roots from ``run_roots``:

- ``unit(at, roots)`` is the canonical key of one declaration: a repo-relative
  ``path::qualname``; a bare path is ``path::<file>`` (module-level code of a ``.py`` is
  ``path::<module>``), so one unit has one key however it is written.
- ``resolve`` keeps one entry per (consumer id, unit): PLAN readers first, then
  ``consumer_readers`` in their stored order, a later declaration replacing an earlier
  one. Each entry keeps the declarations it replaced (``replaced``), so the document can
  show what a post-PLAN declaration overrode. An invalid declaration is returned in
  ``rejected`` (with its error) and replaces nothing; the CLI refuses one at write time,
  so only a PLAN reader can be rejected for good.
- ``gated_entry(run_state)`` is the worktree entry of the repo the consumers gate
  judges: the first active entry with a repo_name.
- ``run_roots(run_state)`` is the roots of that one repo: its worktree path and the main
  checkout of every entry with its repo_name (active or released), so a document
  rendered after the worktree is gone still makes the same paths relative, and a path in
  another repo of the run is never made relative to this one. With no active entry the
  repo is the one every entry names; entries naming different repos give [].

No git and no reads of file contents: path arithmetic (``os.path``), plus a stat of
each root to resolve a symlinked one.

Public: ``rel``, ``unit``, ``resolve``, ``gated_entry``, ``run_roots``, ``STATUSES``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

STATUSES = ("migrated", "unaffected")


def rel(path: str, roots: "Iterable[str | Path]") -> "str | None":
    """*path* as a repo-relative POSIX path, or None when it is absolute outside every
    root, empty, or leaves the tree (``..``)."""
    p = str(path or "").strip()
    if not p:
        return None
    if os.path.isabs(p):
        for root in roots or []:
            r = str(root)
            cands = {r.rstrip(os.sep)}
            if os.path.exists(r):
                cands.add(os.path.realpath(r).rstrip(os.sep))
            hit = next((c for c in cands if c and (p == c or p.startswith(c + os.sep))), None)
            if hit:
                p = os.path.relpath(p, hit)
                break
        else:
            return None
    p = os.path.normpath(p).replace(os.sep, "/")
    if p == ".." or p.startswith("../") or os.path.isabs(p):
        return None
    return p


def unit(at: str, roots: "Iterable[str | Path]") -> "str | None":
    """The canonical unit of a declaration ``path::qualname`` (bare path → ``<file>``)."""
    path, sep, q = str(at or "").strip().partition("::")
    r = rel(path, roots)
    if r is None or r == ".":
        return None
    q = q.strip() if sep else ""
    return f"{r}::{q or '<file>'}"


def resolve(consumers: "list | None", extra: "list | None",
            roots: "Iterable[str | Path]") -> "tuple[dict[str, list[dict]], list[dict]]":
    """({consumer id: [entry]}, rejected). entry = {unit, at, status, reason, source
    ('plan' | 'run'), replaced [{status, reason, source}]}, ordered by latest declaration;
    rejected = [{id, at, status, source, error}] for declarations that name no unit
    inside *roots* or carry no valid status."""
    roots = list(roots or [])
    by_id: "dict[str, dict[str, dict]]" = {}
    rejected: "list[dict]" = []
    items = [c for c in (consumers or []) if isinstance(c, dict)]
    for c in items:
        by_id.setdefault(str(c.get("id") or "?"), {})
    decls = [(str(c.get("id") or "?"), r, "plan") for c in items
             for r in (c.get("readers") or []) if isinstance(r, dict)]
    decls += [(str(r.get("id") or ""), r, "run") for r in (extra or []) if isinstance(r, dict)]
    for cid, r, source in decls:
        if cid not in by_id:
            continue
        at = str(r.get("at") or "").strip()
        u = unit(at, roots)
        status = str(r.get("status") or "").strip().lower()
        why = (f"{cid}: reader '{at}' is outside the repo — name it repo-relative "
               "(path::function)" if u is None else
               f"{cid}: reader '{at}' has status '{status}' — use migrated or unaffected"
               if status not in STATUSES else None)
        if why:
            rejected.append({"id": cid, "at": at, "status": status, "source": source, "error": why})
            continue
        new = {"unit": u, "at": at, "status": status,
               "reason": str(r.get("reason") or "").strip(), "source": source, "replaced": []}
        old = by_id[cid].get(u)
        if old is not None:
            new["replaced"] = old["replaced"] + [
                {"status": old["status"], "reason": old["reason"], "source": old["source"]}]
            del by_id[cid][u]   # re-insert: the dict order follows the latest declaration
        by_id[cid][u] = new
    return {cid: list(units.values()) for cid, units in by_id.items()}, rejected


def gated_entry(run_state: "dict | None") -> "dict | None":
    """The worktree entry of the repo the consumers gate judges: the first entry with
    state 'active' and a repo_name, or None."""
    state = run_state if isinstance(run_state, dict) else {}
    entries = state.get("worktrees")
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict) and e.get("state") == "active" and e.get("repo_name"):
            return e
    return None


def run_roots(run_state: "dict | None") -> "list[str]":
    """The roots of the gated repo (``gated_entry``): its worktree path and the main
    checkout of every entry with its repo_name, as strings, de-duplicated. With no active
    entry, the repo every entry names; [] when they name different repos or there are no
    entries. Another repo of the run is never a root: its paths would alias this repo's."""
    state = run_state if isinstance(run_state, dict) else {}
    entries = [e for e in (state.get("worktrees") if isinstance(state.get("worktrees"), list)
                           else []) if isinstance(e, dict) and e.get("repo_name")]
    g = gated_entry(state)
    if g is not None:
        name = str(g["repo_name"])
    else:
        names = {str(e["repo_name"]) for e in entries}
        if len(names) != 1:
            return []
        name = names.pop()
    from core import run_worktree as rw
    out: "list[str]" = []
    run_id = str(state.get("id") or "")
    for e in entries:
        if str(e["repo_name"]) != name:
            continue
        try:
            if run_id:
                out.append(str(rw.wt_path(run_id, name)))
        except rw.WorktreeError:
            return []   # an unsafe run id / repo name names no worktree (nor trusts its main)
        if e.get("main"):
            out.append(str(e["main"]))
    return list(dict.fromkeys(out))
