"""Post-session ToDo lifecycle hook.

Automatically transitions Radar ToDo items based on session activity:

1. **Explicit binding** — If a session has a todo_id in its metadata
   (set when user drags a todo into chat), checks for git commits
   during the session and marks the todo as ``handled`` if work was done.

2. **Implicit file matching** — If no explicit binding exists, compares
   files changed during the session (from git diff) against pending
   todos' ``linked_context.files``.  Matching todos transition to
   ``in_discussion`` (not directly to ``handled`` — the user gets to
   confirm via frontend buttons or next session).

Key public symbols:

- ``TodoLifecycleHook``  — Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.session_hooks import HookContext
from database import db

logger = logging.getLogger(__name__)

# Must match swarm_workspace_manager.DEFAULT_WORKSPACE_CONFIG["id"]
_WORKSPACE_ID = "swarmws"


def _get_session_changed_files(
    repo_path: Path,
    since: datetime,
    max_commits: int = 50,
) -> list[str]:
    """Get files changed in commits since a given time.

    Returns a list of relative file paths that were modified in commits
    after ``since``.  ``max_commits`` caps the number of commits scanned
    (not the number of files returned).  Gracefully returns empty list on errors.
    """
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return []

    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={since_str}",
                "--name-only",
                "--pretty=format:",
                f"--max-count={max_commits}",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        files = [
            line.strip()
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
        return list(set(files))  # deduplicate
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Git changed-files failed for %s: %s", repo_path, exc)
        return []


def _get_session_commit_count(
    repo_path: Path,
    since: datetime,
) -> int:
    """Count git commits since a given time."""
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return 0

    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"--since={since_str}", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 0
        return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return 0


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


def _find_codebase_path() -> Optional[Path]:
    """Discover the SwarmAI codebase path from known locations.

    Checks common locations rather than hardcoding a user-specific path.
    Returns None if no codebase is found (e.g., on end-user machines).
    """
    candidates = [
        Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai",
        Path.home() / "swarmai",
        Path.home() / "Projects" / "swarmai",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / ".git").exists():
            return candidate
    return None


class TodoLifecycleHook:
    """Transitions Radar ToDo items based on session outcomes.

    Fires after session close.  Two modes:

    1. **Explicit binding** — session.metadata contains ``todo_id``
       (set by drag-to-chat).  If commits exist, mark ``handled``.

    2. **Implicit file matching** — no binding, but session changed
       files matching a pending todo's ``linked_context.files``.
       Transition to ``in_discussion`` only (not auto-complete).
    """

    @property
    def name(self) -> str:
        return "todo_lifecycle"

    async def execute(self, context: HookContext) -> None:
        """Run todo lifecycle checks for the closing session."""
        try:
            await self._process(context)
        except Exception as exc:
            # Error-isolated: log and continue
            logger.error(
                "TodoLifecycleHook failed for session %s: %s",
                context.session_id, exc, exc_info=True,
            )

    async def _process(self, context: HookContext) -> None:
        session_id = context.session_id

        # Get the session record
        session = await db.sessions.get(session_id)
        if not session:
            logger.debug("TodoLifecycleHook: session %s not found", session_id)
            return

        # Parse session start time for git queries
        start_str = context.session_start_time or session.get("created_at", "")
        try:
            since = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since = datetime.now() - timedelta(hours=2)

        # Determine git repo paths for commit queries
        from config import get_app_data_dir
        workspace_path = get_app_data_dir() / "SwarmWS"
        # Discover codebase path from STEERING.md or common locations
        codebase_path = _find_codebase_path()

        # --- Mode 1: Explicit binding ---
        metadata = session.get("metadata") or "{}"
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        bound_todo_id = metadata.get("todo_id") if isinstance(metadata, dict) else None

        if bound_todo_id:
            await self._handle_explicit_binding(
                bound_todo_id, since, workspace_path, codebase_path,
            )
            return  # Explicit binding handled — skip implicit matching

        # --- Mode 2: Implicit file matching ---
        if context.message_count < 2:
            # Skip trivial sessions (no real work done)
            return

        await self._handle_implicit_matching(
            since, workspace_path, codebase_path,
        )

    async def _handle_explicit_binding(
        self,
        todo_id: str,
        since: datetime,
        workspace_path: Path,
        codebase_path: Optional[Path],
    ) -> None:
        """Handle a session explicitly bound to a todo via drag-to-chat."""
        # Check if commits were made during the session
        commit_count = _get_session_commit_count(workspace_path, since)
        if codebase_path:
            commit_count += _get_session_commit_count(codebase_path, since)

        todo = await db.todos.get(todo_id)
        if not todo:
            logger.debug("Bound todo %s not found — skipping", todo_id)
            return

        current_status = todo.get("status", "pending")
        if current_status in ("handled", "cancelled", "deleted"):
            return  # Already resolved

        if commit_count > 0:
            # Work was done — mark as handled
            await db.todos.update(todo_id, {
                "status": "handled",
                "updated_at": datetime.now().isoformat(),
            })
            logger.info(
                "TodoLifecycleHook: marked todo %s as handled "
                "(%d commits during session)",
                todo_id[:8], commit_count,
            )
        else:
            # Session interacted but no commits — mark as in_discussion
            if current_status == "pending":
                await db.todos.update(todo_id, {
                    "status": "in_discussion",
                    "updated_at": datetime.now().isoformat(),
                })
                logger.info(
                    "TodoLifecycleHook: marked todo %s as in_discussion "
                    "(bound but no commits)",
                    todo_id[:8],
                )

    async def _handle_implicit_matching(
        self,
        since: datetime,
        workspace_path: Path,
        codebase_path: Optional[Path],
    ) -> None:
        """Match session file changes against pending todos' linked files."""
        changed_files = _get_session_changed_files(workspace_path, since)
        if codebase_path:
            changed_files += _get_session_changed_files(codebase_path, since)

        if not changed_files:
            return

        # Fetch pending todos with linked_context.files
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
                todo_id = todo["id"]
                await db.todos.update(todo_id, {
                    "status": "in_discussion",
                    "updated_at": datetime.now().isoformat(),
                })
                matched += 1
                logger.info(
                    "TodoLifecycleHook: implicit match — todo %s → in_discussion "
                    "(files overlap with session changes)",
                    todo_id[:8],
                )

        if matched:
            logger.info(
                "TodoLifecycleHook: %d todos transitioned via implicit file matching",
                matched,
            )
