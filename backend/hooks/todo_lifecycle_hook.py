"""Post-session ToDo lifecycle hook.

Moves Radar ToDo items from what the session's OWN records say it did (design
docs/design/2026-10-25-landings-consumers.md D3), never from a git time window
(another session's commit in the same hour is not this session's work):

1. **Explicit binding** — the session's metadata holds a ``todo_id`` (a ToDo dragged
   into the chat). With a recorded outcome the ToDo goes to review
   (``review_state='completed'``, status unchanged, as the dispatch sweeper does):
   only a person's Confirm sets ``handled`` (the ToDo model's locked invariant,
   routers/todos.py). Without one it becomes ``in_discussion``. Only an active ToDo
   (pending / overdue / in_discussion, not yet in review) is changed, by ONE
   conditional write (``db.todos.update_if``), so a Confirm / Reject meanwhile wins.
2. **Implicit file matching** — no binding: the session's changed files are matched
   against pending ToDos' ``linked_context.files`` (``_files_overlap``, unchanged);
   a match moves the ToDo to ``in_discussion``. The pool is SwarmWS outputs plus the
   session's one working repo, as before, so the basename rule is not widened.

The outcome is three records, each on the pool that owns it:

- the turn cards' file rows (core/session_changes window, ``canvas_changes``);
- the session's outputs (core/session_outputs, ``canvas_session``: after the writes
  queued before it);
- the session's landings (core/session_landings, ``canvas_changes``).

A file is one entry per ``(gitRoot, path)`` for card rows and landing files (both
name the main checkout) and per real path for an output; the count is said as "N
recorded files", "at least N" when any part is capped, truncated or unreadable.

A session being deleted (``HookContext.deleting``) changes no ToDo: its records are
deleted while the hooks run.

Key public symbols:

- ``TodoLifecycleHook``  — Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from core.session_hooks import HookContext
from core.todo_manager import ACTIVE_STATUSES as _ACTIVE, todo_manager
from database import db

logger = logging.getLogger(__name__)

# Must match swarm_workspace_manager.DEFAULT_WORKSPACE_CONFIG["id"]
_WORKSPACE_ID = "swarmws"

_LANDINGS_MAX = 20
_NOTE_LINES = 10


def _files_overlap(todo_files: list[str], changed_files: list[str]) -> bool:
    """Check if any todo file paths overlap with changed files.

    Supports partial path matching — a todo file ``session_unit.py``
    matches a changed file ``backend/core/session_unit.py``.
    """
    if not todo_files or not changed_files:
        return False

    for todo_file in todo_files:
        todo_basename = Path(todo_file).name
        for changed in changed_files:
            # Exact match or basename match
            if changed == todo_file or changed.endswith(f"/{todo_file}"):
                return True
            if Path(changed).name == todo_basename:
                return True
    return False


# ── the three records (blocking; each on its own pool) ─────────────────────────

def _card_part(session_id: str, db_path: str) -> dict:
    """The turn cards' file rows and the working repo (canvas_changes pool)."""
    from core import session_changes
    rows, scanned_all = session_changes._window(session_id, db_path)
    files, partial = [], not scanned_all
    for r in rows:
        for c in r["cards"]:
            files += c["files"]
            partial = partial or bool(c.get("omitted"))
    working = session_changes.working_repo(session_id, db_path)
    return {"rows": files, "working": working["path"] if working else None, "partial": partial}


def _output_part(session_id: str) -> dict:
    """The session's outputs, not deleted (canvas_session pool). The store drops its
    oldest rows past MAX_ROWS without a marker: a full store may hold more."""
    from core import session_outputs
    rows = session_outputs.read(session_id)["rows"]
    return {"rows": [r for r in rows if not r.get("deleted")],
            "partial": len(rows) >= session_outputs.MAX_ROWS}


def _landing_part(session_id: str) -> dict:
    """The session's landings (canvas_changes pool)."""
    from core import session_landings as sl
    res = sl.read_session(session_id, deadline=sl.Deadline(10), cap=sl.CANVAS_CAP,
                          limit=_LANDINGS_MAX)
    return {"landings": res["landed"],
            "partial": bool(res["truncated"] or res["unknown"] or res["unverified"])}


def _real(p) -> str:
    return os.path.realpath(p) if isinstance(p, str) and p else ""


async def _outcome(session_id: str) -> dict:
    """``{entries, landings, partial, match}``: what the session's records say it did."""
    from core import executors
    parts: dict = {}
    for name, pool, fn, args in (
            ("cards", "canvas_changes", _card_part, (session_id, str(db.db_path))),
            ("outputs", "canvas_session", _output_part, (session_id,)),
            ("landings", "canvas_changes", _landing_part, (session_id,))):
        try:
            parts[name] = await executors.run_in(pool, fn, *args)
        except Exception as exc:  # noqa: BLE001 — one unreadable record: said, not fatal
            logger.warning("TodoLifecycleHook: %s of %s unreadable: %s", name, session_id, exc)
            parts[name] = None
    cards = parts["cards"] or {"rows": [], "working": None}
    outputs = parts["outputs"] or {"rows": []}
    landings = (parts["landings"] or {"landings": []})["landings"]
    partial = any(p is None or p.get("partial") for p in parts.values())
    repo_rows = [*cards["rows"], *(f for land in landings for f in land.get("files") or [])]
    entries = {("repo", _real(f.get("gitRoot")), f.get("path")) for f in repo_rows}
    entries |= {("file", _real(o.get("absolutePath") or o.get("path"))) for o in outputs["rows"]}
    working = _real(cards["working"])
    match = [o["path"] for o in outputs["rows"] if o.get("path")]
    match += [f["path"] for f in repo_rows
              if working and f.get("path") and _real(f.get("gitRoot")) == working]
    return {"entries": entries, "landings": landings, "partial": partial, "match": match}


def _note(out: dict) -> str:
    n = len(out["entries"])
    count = f"{'at least ' if out['partial'] else ''}{n} recorded file{'' if n == 1 else 's'}"
    lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Session outcome: {count}"]
    for land in out["landings"]:
        subjects = "; ".join(s.get("subject", "") for s in (land.get("subjects") or [])[:3])
        lines.append(f"  - {land.get('branch')} → {land.get('dest')}: {subjects}")
    return "\n".join(lines[:_NOTE_LINES + 1])


class TodoLifecycleHook:
    """Transitions Radar ToDo items from the session's own records (module docstring).

    Fires after session close.
    """

    @property
    def name(self) -> str:
        return "todo_lifecycle"

    async def execute(self, context: HookContext) -> None:
        """Run the hook. Errors are logged and never propagate."""
        try:
            await self._process(context)
        except Exception as exc:
            # Error-isolated: log and continue
            logger.warning("TodoLifecycleHook failed for session %s: %s", context.session_id, exc)

    async def _process(self, context: HookContext) -> None:
        if getattr(context, "deleting", False):
            return          # its records are being deleted under us: change nothing
        session_id = context.session_id
        session = await db.sessions.get(session_id)
        if not session:
            logger.debug("TodoLifecycleHook: session %s not found", session_id)
            return

        metadata = session.get("metadata") or "{}"
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        bound_todo_id = metadata.get("todo_id") if isinstance(metadata, dict) else None

        if bound_todo_id:
            await self._handle_explicit_binding(bound_todo_id, await _outcome(session_id))
            return  # Explicit binding handled — skip implicit matching

        if context.message_count < 2:
            return  # Skip trivial sessions (no real work done)
        await self._handle_implicit_matching((await _outcome(session_id))["match"])

    async def _handle_explicit_binding(self, todo_id: str, out: dict) -> None:
        """A session bound to a ToDo: to review with an outcome, else in_discussion."""
        if not (out["entries"] or out["landings"]):
            if await db.todos.update_if(todo_id, {"status": "in_discussion"},
                                        status_in=_ACTIVE, review_state_null=True):
                logger.info("ToDo %s: -> in_discussion (source=hook_explicit)", todo_id[:8])
            return
        # the review state and the notes in ONE write, only while the row is as read
        await todo_manager.to_review(todo_id, _note(out), source="hook_explicit")

    async def _handle_implicit_matching(self, changed_files: list[str]) -> None:
        """Match the session's changed files against pending todos' linked files."""
        if not changed_files:
            return

        pending_todos = await db.todos.list_by_workspace(_WORKSPACE_ID, "pending")

        matched = 0
        for todo in pending_todos:
            linked_raw = todo.get("linked_context") or "{}"
            if isinstance(linked_raw, str):
                try:
                    linked = json.loads(linked_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                linked = linked_raw

            todo_files = linked.get("files", [])
            if not todo_files:
                continue

            if _files_overlap(todo_files, changed_files):
                transitioned = await todo_manager.transition_status(
                    todo["id"], "in_discussion", source="hook_implicit",
                )
                if transitioned:
                    matched += 1

        if matched:
            logger.info(
                "TodoLifecycleHook: %d todos transitioned via implicit file matching",
                matched,
            )
