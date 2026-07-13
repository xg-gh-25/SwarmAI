"""Smart workspace auto-commit hook with conventional commit messages.

Replaces the per-turn ``_auto_commit_workspace()`` with an intelligent
session-close commit that analyzes ``git diff --stat``, categorizes
changes by file path, and generates meaningful commit messages.

Uses a shared ``asyncio.Lock`` (provided by ``BackgroundHookExecutor``)
to serialize git operations across concurrent session hooks, preventing
``.git/index.lock`` contention.

Key public symbols:

- ``WorkspaceAutoCommitHook``  — Implements ``SessionLifecycleHook``.
- ``COMMIT_CATEGORIES``        — Path prefix → commit prefix mapping.
- ``EXTENSION_CATEGORIES``     — File extension → commit prefix mapping.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

from core.session_hooks import HookContext
from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

# Path prefix → conventional commit prefix
COMMIT_CATEGORIES: dict[str, str] = {
    ".context/": "framework",
    ".claude/skills/": "skills",
    ".claude/settings/": "config",
    ".claude/mcps/": "config",
    "Knowledge/": "content",
    "Projects/": "project",
}

# File extension → conventional commit prefix
EXTENSION_CATEGORIES: dict[str, str] = {
    ".pdf": "output",
    ".pptx": "output",
    ".docx": "output",
    ".png": "output",
    ".jpg": "output",
}

DEFAULT_CATEGORY = "chore"


class WorkspaceAutoCommitHook:
    """Smart git commit at session close with conventional commit messages.

    Analyzes changed files via ``git diff --stat``, categorizes them by
    path pattern, generates a meaningful commit message, and skips
    trivial changes.

    Accepts an optional shared ``asyncio.Lock`` to serialize git
    operations across concurrent hook executions (multiple sessions
    closing at the same time).

    All git subprocesses have a 10-second timeout to prevent hanging on
    ``.git/index.lock`` contention with live agent sessions.
    """

    name = "workspace_auto_commit"

    # Timeout for individual git subprocess calls (seconds).
    # Must be short — a hanging git command should fail fast,
    # not block the background hook for 30s.
    GIT_TIMEOUT = 10

    def __init__(self, git_lock: asyncio.Lock | None = None) -> None:
        self._git_lock = git_lock

    async def execute(self, context: HookContext) -> None:
        """Analyze changes and commit with a smart message."""
        ws_path = initialization_manager.get_cached_workspace_path()
        # R29 fix: files another LIVE session is mid-edit must NOT be swept into
        # THIS session's commit (DEC30). Computed here (async ctx) so _smart_commit
        # stays a pure thread body.
        exclude = self._other_live_sessions_touched(context.session_id)
        if self._git_lock:
            async with self._git_lock:
                await asyncio.to_thread(self._smart_commit, ws_path, exclude)
        else:
            await asyncio.to_thread(self._smart_commit, ws_path, exclude)

    @staticmethod
    def _other_live_sessions_touched(current_session_id: str) -> set[str]:
        """Absolute paths currently being edited by OTHER live sessions.

        Reads each live SessionUnit's stable hook-context dict
        (``_hook_session_context['_files_touched']`` — populated by the
        file_tracker PostToolUse hook, session_router.py:1993). These are the
        paths a sibling session has Read/Edit/Written this turn; committing them
        from THIS session would sweep a sibling's in-flight work (R29/DEC30).

        FAIL-SAFE: any error (registry not ready, attr missing) → empty set, so
        the caller falls back to plain ``git add -A`` — never crashes the commit.
        """
        try:
            from core import session_registry
            router = session_registry.session_router
            if router is None:
                return set()
            others: set[str] = set()
            mine: set[str] = set()
            for sid, unit in list(getattr(router, "_units", {}).items()):
                ctx = getattr(unit, "_hook_session_context", None)
                if not ctx:
                    continue
                touched = ctx.get("_files_touched")
                if not touched:
                    continue
                bucket = mine if sid == current_session_id else others
                bucket.update(str(p) for p in touched)
            # Gate-2 finding B: subtract MY OWN touched paths. A file BOTH this
            # session and a sibling edited must stay committed here (dropping it
            # would silently lose my own legitimate change to a shared file).
            return others - mine
        except Exception as e:
            logger.debug("auto_commit: could not compute sibling-touched set: %s", e)
            return set()

    @staticmethod
    def _cleanup_stale_git_lock(ws_path: str) -> None:
        """Remove stale .git/index.lock if no git process is using it.

        Prevents cascading failures when the backend was killed mid-commit.
        Uses ``pgrep`` to verify no live git process targets this repo.
        """
        lock_file = os.path.join(ws_path, ".git", "index.lock")
        if not os.path.exists(lock_file):
            return
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"git.*{ws_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:  # No matching git process
                os.remove(lock_file)
                logger.warning("Removed stale .git/index.lock before auto-commit")
            else:
                logger.info("Git index.lock exists and git is running — skipping cleanup")
        except Exception as e:
            logger.warning("Failed to check/clean stale git lock: %s", e)

    def _unstage_paths(self, ws_path: str, exclude: set[str]) -> None:
        """`git reset` the excluded paths (unstage only — working tree untouched).

        Paths are made relative to ``ws_path``; ones outside the repo are dropped.
        Batched to respect argv limits. Fail-safe: errors are logged, not raised.
        """
        rels: list[str] = []
        ws = os.path.realpath(ws_path)
        for p in exclude:
            try:
                rp = os.path.relpath(os.path.realpath(p), ws)
            except (ValueError, OSError):
                continue
            if rp.startswith(".."):  # outside the workspace repo — ignore
                continue
            rels.append(rp)
        if not rels:
            return
        try:
            for i in range(0, len(rels), 100):  # batch to stay under argv limits
                subprocess.run(
                    ["git", "reset", "-q", "--", *rels[i:i + 100]],
                    cwd=ws_path, capture_output=True, timeout=self.GIT_TIMEOUT,
                )
            logger.info("auto_commit: unstaged %d sibling-session path(s) (R29)", len(rels))
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("auto_commit: unstage of sibling paths failed (non-fatal): %s", e)

    def _smart_commit(self, ws_path: str, exclude: set[str] | None = None) -> None:
        """Run git operations in a background thread.

        All subprocess calls use ``GIT_TIMEOUT`` to fail fast on lock
        contention rather than hanging.  A ``TimeoutExpired`` aborts the
        commit attempt — the changes will be picked up next time.

        ``exclude``: absolute paths another live session is mid-edit — unstaged
        after the bulk add so a sibling's in-flight work is never swept into this
        session's commit (R29/DEC30). Empty/None → prior ``git add -A`` behavior.
        """
        # 0. Clean stale lock from previous crash
        self._cleanup_stale_git_lock(ws_path)

        try:
            # 1. Check for changes
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=self.GIT_TIMEOUT,
            )
            if not status.stdout.strip():
                return  # No changes

            # 2. Stage all changes
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=ws_path, capture_output=True,
                timeout=self.GIT_TIMEOUT,
            )
            if add_result.returncode != 0:
                logger.warning("git add failed: %s", add_result.stderr)
                return

            # 2b. R29: unstage paths a sibling session is actively editing, so this
            # commit carries THIS session's work + auto-generated files (DailyActivity/
            # EVOLUTION/index — never in any _files_touched set) but NOT a sibling's
            # in-flight edits. `git reset` only unstages (working tree untouched), so
            # the sibling's changes stay on disk for ITS own commit. Fail-safe: a reset
            # error is logged, not fatal — worst case is the prior sweep behavior.
            if exclude:
                self._unstage_paths(ws_path, exclude)

            # 3. Analyze staged changes
            diff_stat = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=self.GIT_TIMEOUT,
            )
            changed_files = self._parse_diff_stat(diff_stat.stdout)

            # 4. Generate commit message
            if not changed_files:
                message = "chore: session changes"
            elif self._is_trivial(changed_files):
                message = f"chore: session sync ({len(changed_files)} files)"
            else:
                message = self._generate_commit_message(changed_files)

            # 5. Commit
            commit_result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=ws_path, capture_output=True,
                timeout=self.GIT_TIMEOUT,
            )
            logger.info("Auto-committed workspace: %s", message)

            # 5b. Emit GIT_COMMIT event for DDD cultivation v2
            if commit_result.returncode == 0:
                try:
                    from core.cultivation_dispatcher import (
                        EventType, emit_cultivation_event_threadsafe,
                    )
                    emit_cultivation_event_threadsafe(
                        EventType.GIT_COMMIT,
                        source="auto_commit_hook",
                        payload={"files": changed_files, "message": message},
                        priority=2,
                    )
                except Exception:
                    pass  # Non-blocking: cultivation emit failure never breaks commit

                # 5c. Emit git_commit event for job scheduler (code_intel reindex)
                # Uses emit_event_atomic to avoid race condition: hook loading
                # stale state would overwrite scheduler's successful job updates.
                try:
                    from jobs.scheduler import emit_event_atomic
                    emit_event_atomic("git_commit", data={
                        "files": changed_files,
                        "message": message,
                    })
                except Exception as e:
                    logger.debug("Failed to emit git_commit event: %s", e)

        except subprocess.TimeoutExpired:
            logger.warning(
                "Git operation timed out after %ds (likely index.lock contention) — "
                "skipping auto-commit, changes will be picked up next time",
                self.GIT_TIMEOUT,
            )
            return

        # NOTE: This hook is LOCAL-COMMIT ONLY. It deliberately does NOT push.
        # Auto-push was removed (run_76932250): pushing the SwarmWS workspace
        # (MEMORY/USER/Knowledge/Projects — personal + business data) to the
        # PUBLIC origin is exactly the leak STEERING #5 forbids ("绝不 auto-push
        # 到 GitHub"). The workspace is persisted on local disk via these commits;
        # any push to a remote is a deliberate, user-initiated action.

    @staticmethod
    def _parse_diff_stat(diff_output: str) -> list[str]:
        """Extract file paths from ``git diff --stat`` output."""
        files = []
        for line in diff_output.strip().splitlines():
            if "|" in line:
                file_path = line.split("|")[0].strip()
                if file_path:
                    files.append(file_path)
        return files

    @staticmethod
    def _categorize_file(file_path: str) -> str:
        """Map a file path to a conventional commit category."""
        for prefix, category in COMMIT_CATEGORIES.items():
            if file_path.startswith(prefix):
                return category
        for ext, category in EXTENSION_CATEGORIES.items():
            if file_path.endswith(ext):
                return category
        return DEFAULT_CATEGORY

    def _is_trivial(self, files: list[str]) -> bool:
        """Check if all changes are trivial (only skill config syncs)."""
        return all(
            self._categorize_file(f) in ("skills", "chore")
            for f in files
        )

    def _generate_commit_message(self, files: list[str]) -> str:
        """Generate a conventional commit message from changed files."""
        categories: dict[str, int] = {}
        for f in files:
            cat = self._categorize_file(f)
            categories[cat] = categories.get(cat, 0) + 1

        if not categories:
            return "chore: session changes"

        dominant = max(categories, key=lambda k: (categories[k], k))
        total = sum(categories.values())

        if total == 1:
            return f"{dominant}: update {files[0]}"
        elif len(categories) == 1:
            return f"{dominant}: update {total} files"
        else:
            parts = [
                f"{cat} ({n})"
                for cat, n in sorted(categories.items(), key=lambda x: -x[1])
            ]
            return f"{dominant}: {', '.join(parts)}"
