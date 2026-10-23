"""Evidence binding for v1 judgments (conformance fixing spec 2026-10-23, K1).

A check may only conclude from evidence that is about the code here now. Every
check and every write that judges evidence gets it through this module, so that
question is answered in exactly one place:

- ``resolve_base`` — a run repo's base: HEAD for a shared repo (its root IS the main
  checkout); otherwise the fork point from the RECORDED integration branch. It never
  guesses: an isolated repo whose base git cannot name raises ``FactError``.
- ``bind_final`` — the final only when every expected repo row binds the current
  code tree and the current base; otherwise ``{}`` plus the reasons.
- ``required_validations`` — the BASE ``validations.json`` selected by the changed
  paths; the final's own list only says what ran, never what was required.
- ``RULE_PATHS`` / ``rule_violations`` — the files that decide what a run must
  verify, and whether a run touched them or ran on pipeline code other than its
  contract's (QX-2).
- ``resolve_refs`` — a refutation's refs, each resolved against the code tree, the
  run's verified events or the bound final; a ref that does not resolve is refused.
- ``decision_values`` — each decision exactly as its validated event recorded it.
- ``stale_closures`` — findings closed by a refutation or diff assertion that was
  judged on a code tree the run no longer has: the closure does not hold now.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.run_protocol import cache

SHA40 = re.compile(r"[0-9a-f]{40}")
# repo-relative: the v1 pipeline code lives under backend/ of the SwarmAI repo
RULE_PATHS: tuple[str, ...] = ("validations.json",) + tuple(
    f"backend/{path}" for path in cache.PIPELINE_CODE)
_PATH_REF = re.compile(r"(?P<path>[^:\s][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?")


class FactError(ValueError):
    """Evidence cannot be bound to the run's current facts."""


def _git(root: str | Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def resolve_base(root: str | Path, main: str | Path | None, branch: str | None) -> str:
    """The base of one run repo. *main* is the contract's repo root for it; *branch*
    the integration branch recorded when its worktree was created."""
    if not main or Path(main).resolve() == Path(root).resolve():
        head = _git(root, "rev-parse", "HEAD")
        if head and SHA40.fullmatch(head):
            return head
        raise FactError(f"{root}: HEAD is unreadable")
    if not isinstance(branch, str) or not branch.startswith("refs/heads/"):
        raise FactError(f"{root}: no integration branch was recorded for this worktree")
    target = _git(main, "rev-parse", "--verify", f"{branch}^{{commit}}")
    head = _git(root, "rev-parse", "HEAD")
    base = _git(root, "merge-base", head, target) if target and head else None
    if not base or not SHA40.fullmatch(base):
        raise FactError(f"{root}: cannot find where the worktree forked from {branch}")
    return base


def bind_final(final: Mapping[str, Any], *, expected: Sequence[str],
               code_trees: Mapping[str, str | None],
               bases: Mapping[str, str | None]) -> tuple[dict[str, Any], list[str]]:
    repos = final.get("repos") if isinstance(final, Mapping) else None
    reasons: list[str] = []
    if not isinstance(repos, Mapping) or not expected or set(repos) != set(expected):
        return {}, ["no final for every expected repo"]
    for name in expected:
        row = repos[name]
        if not isinstance(row, Mapping):
            reasons.append(f"{name}: final row malformed")
            continue
        if not code_trees.get(name) or row.get("code_tree") != code_trees.get(name):
            reasons.append(f"{name}: final was made on another code tree")
        if not bases.get(name) or row.get("base") != bases.get(name):
            reasons.append(f"{name}: final was made on another base")
    return ({} if reasons else dict(final)), reasons


def required_validations(roots: Mapping[str, str], bases: Mapping[str, str | None],
                         changed: Mapping[str, Sequence[str]]) -> dict[str, frozenset[str]]:
    from core.run_protocol import validations
    out: dict[str, frozenset[str]] = {}
    for name, root in roots.items():
        base = bases.get(name)
        if not base:
            raise FactError(f"{name}: base unknown, required validations unknown")
        try:
            manifest = validations.load_manifest(root, base)
            out[name] = frozenset(entry["id"] for entry in
                                  validations.required_for(manifest, list(changed.get(name, ()))))
        except validations.ValidationError as exc:
            raise FactError(f"{name}: {exc}") from exc
    return out


def rule_violations(changed: Sequence[str], final: Mapping[str, Any],
                    contract_pipeline_hash: str) -> list[str]:
    out = [f"run changed {path}" for path in changed
           if path in RULE_PATHS or any(path.startswith(rule) for rule in RULE_PATHS
                                        if rule.endswith("/"))]
    repos = final.get("repos") if isinstance(final, Mapping) else None
    for name, row in (repos or {}).items():
        recorded = (row.get("provenance") or {}).get("pipeline_code_hash") \
            if isinstance(row, Mapping) else None
        if recorded != contract_pipeline_hash:
            out.append(f"{name}: tests ran on pipeline code other than the contract's")
    return out


def resolve_refs(refs: Any, *, root: str | Path, code_tree: str,
                 event_ids: set[str] | frozenset[str],
                 final_nodes: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``path:L[-M]`` in the code tree, ``event:<id>`` of this run, or
    ``test:<nodeid>`` with a result in the bound final."""
    if not isinstance(refs, list) or not refs:
        raise FactError("a refutation needs at least one ref")
    out: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            raise FactError(f"ref {ref!r} is not a string")
        if ref.startswith("event:"):
            event_id = ref[len("event:"):]
            if event_id not in event_ids:
                raise FactError(f"{ref}: no such event in this run")
            out.append({"kind": "event", "event_id": event_id})
            continue
        if ref.startswith("test:"):
            node = ref[len("test:"):]
            result = final_nodes.get(node)
            if not isinstance(result, str):
                raise FactError(f"{ref}: no result for this test on the current code tree")
            out.append({"kind": "test", "node": node, "result": result})
            continue
        match = _PATH_REF.fullmatch(ref)
        if not match or match["path"].startswith("/") or ".." in Path(match["path"]).parts:
            raise FactError(f"{ref}: not path:line, event:<id> or test:<nodeid>")
        start, end = int(match["start"]), int(match["end"] or match["start"])
        blob = _git(root, "rev-parse", "--verify", f"{code_tree}:{match['path']}")
        text = _git(root, "cat-file", "blob", blob) if blob else None
        if text is None:
            raise FactError(f"{ref}: file is not in the current code tree")
        if not 1 <= start <= end <= max(1, len(text.splitlines())):
            raise FactError(f"{ref}: line range is outside the file")
        out.append({"kind": "path", "path": match["path"], "lines": [start, end], "blob": blob})
    return out


def decision_values(events: Sequence[Mapping[str, Any]], blobs: Path) -> dict[str, dict[str, Any]]:
    """request_id -> the decision its validated event recorded (the blob it names)."""
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("kind") != "decision":
            continue
        ref = event["payload"].get("decision_json")
        if not isinstance(ref, Mapping) or not isinstance(ref.get("blob"), str):
            continue
        try:
            raw = (blobs / ref["blob"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != ref["blob"]:
                raise ValueError("blob content does not match its digest")
            value = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise FactError(f"decision {event['payload']['request_id']}: blob unreadable: {exc}") from exc
        out[event["payload"]["request_id"]] = value
    return out


def stale_closures(rows: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
                   code_trees: Mapping[str, str | None]) -> set[str]:
    """Canonical ids whose closing CLI proof (refuted / diff_assertion) was judged on
    a code tree that is not ITS repo's current tree (tree ids can coincide across
    repos, so the proof's repo decides). A proof recorded without a repo binds only
    in a one-repo run. Test proofs are not here: a later final re-runs the tests on
    the tree that lands."""
    by_id = {event.get("event_id"): event for event in events}
    only = next(iter(code_trees)) if len(code_trees) == 1 else None
    out: set[str] = set()
    for row in rows:
        if row.get("status") not in ("refuted", "closed_asserted"):
            continue
        payload = (by_id.get(row.get("evidence_event_id")) or {}).get("payload", {})
        repo = payload.get("repo") or only
        tree = payload.get("code_tree")
        if not repo or not tree or code_trees.get(repo) != tree:
            out.add(str(row.get("canonical_id")))
    return out
