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

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.session_hooks import HookContext
from core.todo_manager import todo_manager
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
    """Discover the SwarmAI codebase path.

    Priority:
    1. ``SWARMAI_CODEBASE_DIR`` env var (explicit override)
    2. Common filesystem locations (dev machines)

    Returns None if no codebase is found (e.g., on end-user machines
    running the packaged app — expected, not an error).
    """
    import os

    # 1. Explicit env var — always wins
    env_path = os.environ.get("SWARMAI_CODEBASE_DIR")
    if env_path:
        p = Path(env_path)
        if p.is_dir() and (p / ".git").exists():
            return p

    # 2. Common dev machine layouts
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
        # Git subprocess calls are blocking (10s timeout each).
        # Run in thread pool to keep event loop responsive.
        loop = asyncio.get_running_loop()
        commit_count = await loop.run_in_executor(
            None, _get_session_commit_count, workspace_path, since
        )
        if codebase_path:
            commit_count += await loop.run_in_executor(
                None, _get_session_commit_count, codebase_path, since
            )

        if commit_count > 0:
            # Work was done — write outcome back, then mark as handled
            await self._write_outcome_notes(
                todo_id, since, workspace_path, codebase_path,
            )
            await todo_manager.transition_status(
                todo_id, "handled", source="hook_explicit",
            )
        else:
            # Session interacted but no commits — mark as in_discussion
            await todo_manager.transition_status(
                todo_id, "in_discussion", source="hook_explicit",
            )

    async def _write_outcome_notes(
        self,
        todo_id: str,
        since: datetime,
        workspace_path: Path,
        codebase_path: Optional[Path],
    ) -> None:
        """Write session outcome (commits + files) back to todo's linked_context.notes.

        This closes the feedback loop: when a todo is marked 'handled',
        the outcome is recorded so anyone reviewing the todo later knows
        what was done.  Non-fatal — failure doesn't block status transition.
        """
        try:
            import sqlite3
            db_path = Path.home() / ".swarm-ai" / "data.db"
            if not db_path.exists():
                return

            # Gather commits from both repos
            loop = asyncio.get_running_loop()
            commits: list[str] = []
            for repo in [workspace_path, codebase_path]:
                if repo and repo.is_dir():
                    repo_commits = await loop.run_in_executor(
                        None, self._get_oneline_commits, repo, since,
                    )
                    commits.extend(repo_commits)

            if not commits:
                return

            # Format outcome note
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            note = f"[{timestamp}] Session outcome: {len(commits)} commit(s)\n"
            for c in commits[:10]:  # Cap to avoid bloat
                note += f"  - {c}\n"

            # Atomic update: read linked_context → append to notes → write back
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                row = conn.execute(
                    "SELECT linked_context FROM todos WHERE id = ?", (todo_id,)
                ).fetchone()
                if not row:
                    return

                ctx = {}
                if row[0]:
                    try:
                        ctx = json.loads(row[0])
                    except (json.JSONDecodeError, TypeError):
                        ctx = {}

                existing_notes = ctx.get("notes", "")
                ctx["notes"] = (existing_notes + "\n" + note).strip() if existing_notes else note.strip()

                conn.execute(
                    "UPDATE todos SET linked_context = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(ctx, ensure_ascii=False), datetime.now().isoformat(), todo_id),
                )
                conn.commit()
                logger.info("Wrote outcome notes to todo %s: %d commits", todo_id[:8], len(commits))
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to write outcome notes for todo %s: %s", todo_id[:8], exc)

    @staticmethod
    def _get_oneline_commits(repo_path: Path, since: datetime, max_commits: int = 10) -> list[str]:
        """Get oneline git commits since a timestamp."""
        if not repo_path.is_dir() or not (repo_path / ".git").exists():
            return []
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            result = subprocess.run(
                ["git", "log", f"--since={since_str}", f"--max-count={max_commits}", "--oneline", "--no-decorate"],
                cwd=str(repo_path), capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return []
            return [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []

    async def _handle_implicit_matching(
        self,
        since: datetime,
        workspace_path: Path,
        codebase_path: Optional[Path],
    ) -> None:
        """Match session file changes against pending todos' linked files."""
        loop = asyncio.get_running_loop()
        changed_files = await loop.run_in_executor(
            None, _get_session_changed_files, workspace_path, since
        )
        if codebase_path:
            changed_files += await loop.run_in_executor(
                None, _get_session_changed_files, codebase_path, since
            )

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
