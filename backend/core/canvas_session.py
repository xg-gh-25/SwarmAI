"""canvas_session — what ONE chat session produced, for its Canvas tabs.

Never shows another session's work, and is the same live, after a reload or a restart:

- **Artifacts** ← core/session_outputs (the session's attribution record, written as
  its turns run), each row joined with the workspace product catalog
  (ProductRegistry.lookup: role, label, favorite — the same answer the left-nav
  Artifacts overlay gives for that file) and with whether the file is in its repo's
  HEAD (``inHead``: the rail's NEW / UPD badge; one ``git ls-tree`` per repo). A
  session recorded before that store existed is imported once from its
  ``turn_deliverables`` blocks.
- **Changes** ← core/session_changes: what the session's own transcript says it
  changed — each persisted ``turn_changes`` card, and the commits its assistant text
  names (confirmed by git in a bound repo). How a change was made is not asked; no
  run.json is read (docs/design/canvas-changes-from-turns.md).

The two parts are independent requests (``parts``): Artifacts are a file read on the
single-worker ``canvas_session`` pool (behind the session_outputs writes the router
submitted first); Changes run on the ``canvas_changes`` pool (their own read-only
database connection and git), so an Artifacts refresh never waits for them.

Public: ``build``, ``artifact_rows``, ``in_head``, ``deliverable_turns``, ``MAX_FILES``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MAX_FILES = 200          # per session (Artifacts)
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

    async def changes() -> dict:
        return await executors.run_in("canvas_changes", _changes, session_id, str(db.db_path))

    want_a, want_c = parts in ("all", "artifacts"), parts in ("all", "changes")
    a, c = await asyncio.gather(artifacts() if want_a else _none(), changes() if want_c else _none())
    return {"artifacts": a, "changes": c}


def _changes(session_id: str, db_path: str) -> dict:
    """Blocking, on the canvas_changes pool: core/session_changes.read."""
    from core import session_changes
    return session_changes.read(session_id, db_path)


async def _none() -> None:
    return None
