"""Evidence binding for v1 judgments (conformance fixing spec 2026-10-23, K1).

A check may only conclude from evidence that is about the code here now. Every
check and every write that judges evidence gets it through this module, so that
question is answered in exactly one place:

- ``resolve_base`` — a run repo's base: HEAD for a shared repo (its root IS the main
  checkout); otherwise the fork point from the RECORDED integration branch. It never
  guesses: an isolated repo whose base git cannot name raises ``FactError``.
- ``resolve_refs`` — a refutation's refs, each resolved against the code tree or the
  run's verified events; a ref that does not resolve is refused.
- ``decision_values`` — each decision exactly as its validated event recorded it.
- ``stale_closures`` — findings closed by a refutation or diff assertion whose judged
  files are no longer what the proof saw: the closure does not hold now.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

SHA40 = re.compile(r"[0-9a-f]{40}")
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


def resolve_refs(refs: Any, *, root: str | Path, code_tree: str,
                 event_ids: set[str] | frozenset[str]) -> list[dict[str, Any]]:
    """``path:L[-M]`` in the code tree or ``event:<id>`` of this run."""
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
        match = _PATH_REF.fullmatch(ref)
        if not match or match["path"].startswith("/") or ".." in Path(match["path"]).parts:
            raise FactError(f"{ref}: not path:line or event:<id>")
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
                   code_trees: Mapping[str, str | None], blob_of=None) -> set[str]:
    """Canonical ids whose closing CLI proof (refuted / diff_assertion: ``refuted``,
    ``closed_asserted``, ``closed_unproven``) no longer holds on the current code
    (design 2026-10-26-pipeline-review-closure §2.3 point 5). Fresh when the proof's
    tree is ITS repo's current tree (tree ids can coincide across repos, so the proof's
    repo decides; a proof without a repo binds only in a one-repo run), or, with
    *blob_of(repo, tree, path)* (a blob id, ``"absent"`` or None when unreadable), when
    every path the proof judged is the same blob now: the finding's own file (or, for a
    diff_assertion with ``fix_files``, each of those instead), and for a refutation each
    ``path`` ref too. So a clean rebase that leaves
    those files alone keeps the closure; any doubt (unreadable) is stale.

    ``closed_proven`` is not here: its evidence is a passing test, which the TEST report
    on the landing tree re-establishes (LG-1), and code changed after it goes through
    an incremental review (RV-4)."""
    by_id = {event.get("event_id"): event for event in events}
    only = next(iter(code_trees)) if len(code_trees) == 1 else None
    out: set[str] = set()
    for row in rows:
        if row.get("status") not in ("refuted", "closed_asserted", "closed_unproven"):
            continue
        payload = (by_id.get(row.get("evidence_event_id")) or {}).get("payload", {})
        repo = payload.get("repo") or only
        tree = payload.get("code_tree")
        now = code_trees.get(repo) if repo else None
        if not repo or not tree or not now:
            out.add(str(row.get("canonical_id")))
            continue
        if now == tree:
            continue
        if blob_of is None:
            out.add(str(row.get("canonical_id")))
            continue
        # the finding's own file always counts (a refutation's refs may name only
        # events or other files); a refutation adds each path ref's recorded blob
        judged = [(row.get("file"), blob_of(repo, tree, row.get("file")))]
        if isinstance(payload.get("fix_files"), list) and payload["fix_files"]:
            # a diff_assertion judged on its fix files (pipeline-retro-fixes F3b): those
            # are what must still be as judged, not the finding's own file
            judged = [(f.get("path"), f.get("blob")) if isinstance(f, Mapping) else (None, None)
                      for f in payload["fix_files"]]
        if payload.get("evidence_type") == "refuted":
            judged += [(ref.get("path"), ref.get("blob")) for ref in payload.get("refs") or ()
                       if isinstance(ref, Mapping) and ref.get("kind") == "path"]
        if not all(path and then and then == blob_of(repo, now, path) for path, then in judged):
            out.add(str(row.get("canonical_id")))
    return out
