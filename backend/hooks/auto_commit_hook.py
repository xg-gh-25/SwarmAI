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
import time

from core import executors
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

# CODE file extensions — the R1-governed surface. A tracked file with one of
# these extensions is source code that must not be auto-committed un-reviewed
# (bypassing the Bash adversarial-commit gate). Extension-based (NOT a path
# prefix) so a code file ANYWHERE is caught — incl. the in-SwarmWS pipeline
# engine (Projects/.../engine/*.py) and pollinate scripts under Knowledge/ —
# while auto-generated sediment (DailyActivity/Signals/.context — .md/.json,
# and .html/.yaml which SwarmWS tracks as report OUTPUT + skill manifest DATA,
# not R1 source) is never withheld.
#
# Why an extension set (not "withhold anything not in `covered`"): auto_commit
# does `git add -A`, which stages post-review auto-sediment (DailyActivity,
# run.json, index files) that is legitimately NOT in the reviewer's `covered`
# diff — inverting the axis would withhold that sediment and break the hook's
# core job. So we withhold only the CODE class. The set is kept aligned to
# what SwarmWS actually tracks (verified via `git ls-files`): .js/.mjs/.cjs
# are all present-or-plausible JS variants; the .py/.ts/.tsx bulk is the
# real engine/service surface. (Gate-2 MED: .mjs was a live tracked miss.)
_CODE_EXTENSIONS = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
     ".go", ".rs", ".java", ".rb", ".sh"}
)


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

    # A .git/index.lock older than this is DEFINITELY stale: every live hook
    # commit self-bounds at GIT_TIMEOUT=10s and holds the lock for milliseconds,
    # so a lock this old cannot belong to a live commit — it is a zombie left by
    # a crashed/wedged git. Matches context_health_hook.py's staleness threshold
    # (keep them in sync). Age is the PRIMARY staleness judge; pgrep is only a
    # secondary guard for YOUNG locks (see _cleanup_stale_git_lock).
    STALE_LOCK_AGE_SECONDS = 300

    def __init__(self, git_lock: asyncio.Lock | None = None) -> None:
        self._git_lock = git_lock

    async def execute(self, context: HookContext) -> None:
        """Analyze changes and commit with a smart message."""
        ws_path = initialization_manager.get_cached_workspace_path()
        # R29 fix: files another LIVE session is mid-edit must NOT be swept into
        # THIS session's commit (DEC30). Computed here (async ctx) so _smart_commit
        # stays a pure thread body.
        exclude = self._other_live_sessions_touched(context.session_id)
        # R1 (auto-commit door): the subprocess commit below BYPASSES the Bash
        # adversarial-commit gate. The un-reviewed-code withhold is computed
        # INSIDE _smart_commit AFTER `git add -A` (that's when the staged set
        # exists), so we thread the session_id through — not a precomputed set.
        session_id = context.session_id
        if self._git_lock:
            async with self._git_lock:
                await executors.run_in("subprocess", self._smart_commit, ws_path, exclude, session_id)
        else:
            await executors.run_in("subprocess", self._smart_commit, ws_path, exclude, session_id)

    @staticmethod
    def _uncovered_code_paths(ws_path: str, session_id: str) -> set[str]:
        """Absolute paths of STAGED CODE files this session's adversarial review
        did NOT cover — to be unstaged before the auto-commit (R1 door parity).

        MUST be called AFTER `git add -A` — it reads the staged set (`git diff
        --cached`), which is empty pre-stage.

        Mirrors the Bash adversarial-commit gate's coverage semantics EXACTLY
        (P8 — one brain, many doors): reads the SAME session markers via
        ``_session_adversarial_coverage`` and treats the SAME cases as
        "commit all" (withhold NOTHING):
          - no marker (has_marker=False) → non-pipeline session → not gated here
            (this door deliberately does not gate marker-less sessions, so
            auto_commit keeps sedimenting Knowledge/.context every session);
          - unbounded marker (path-less) → back-compat, matches the gate's approve;
          - coverage error / empty session_id → ``_session_adversarial_coverage``
            returns has_unbounded=True → fail-open, matches gate.
        Otherwise withhold every staged CODE path (by ``_CODE_EXTENSIONS``) whose
        realpath is NOT in the reviewed ``covered`` set. Non-code (sediment,
        .md/.json/output) is NEVER withheld. Fail-safe: ANY error → empty set
        (fall back to prior sweep behavior — never crash the commit).
        """
        if not session_id:
            return set()
        try:
            from core.security_hooks import _session_adversarial_coverage

            has_marker, covered, has_unbounded = _session_adversarial_coverage(session_id)
            if not has_marker or has_unbounded:
                return set()  # not gated / unbounded / fail-open → withhold nothing

            # `git diff --cached --name-only` emits paths relative to the REPO
            # ROOT, and `covered` holds realpaths resolved against that root — so
            # resolve staged paths against the root, NOT ws_path (defense-in-depth
            # vs a future where the cached workspace path is a repo SUBDIR, which
            # would otherwise make every code path look uncovered → over-withhold).
            root = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=WorkspaceAutoCommitHook.GIT_TIMEOUT,
            )
            base = root.stdout.strip() if root.returncode == 0 and root.stdout.strip() else ws_path

            # Staged paths (name-only, NUL-safe), resolved to absolute realpaths to
            # match `covered` (which holds realpath'd absolutes from the marker).
            staged = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "-z"],
                cwd=ws_path, capture_output=True, text=True,
                timeout=WorkspaceAutoCommitHook.GIT_TIMEOUT,
            )
            if staged.returncode != 0:
                return set()
            withhold: set[str] = set()
            for rel in staged.stdout.split("\0"):
                rel = rel.strip()
                if not rel:
                    continue
                if os.path.splitext(rel)[1].lower() not in _CODE_EXTENSIONS:
                    continue  # only CODE is gated here; sediment always commits
                ap = os.path.realpath(os.path.join(base, rel))
                if ap not in covered:
                    withhold.add(ap)
            if withhold:
                logger.warning(
                    "auto_commit: withholding %d un-reviewed code path(s) from "
                    "auto-commit (R1 door): %s",
                    len(withhold), ", ".join(sorted(withhold)[:5]),
                )
            return withhold
        except Exception as e:  # never crash the commit path
            logger.debug("auto_commit: uncovered-code computation failed: %s", e)
            return set()

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

    @classmethod
    def _cleanup_stale_git_lock(cls, ws_path: str) -> None:
        """Remove a stale .git/index.lock. Lock AGE is the primary judge.

        Prevents cascading failures when the backend was killed mid-commit AND
        the WEDGED-GIT failure mode: a git process stuck for hours (crashed
        parent, hung FS) matches ``pgrep -f "git.*ws"`` just like a live one, so
        a pure process-presence check would skip cleanup forever — the lock
        accumulates and concurrent auto-commit hooks saturate the thread pool
        (observed 2026-07-24: 3 zombie git procs held the lock, froze all tabs).

        Decision:
        - age > STALE_LOCK_AGE_SECONDS → delete UNCONDITIONALLY. A live hook
          commit self-bounds at GIT_TIMEOUT=10s, so a 300s lock is provably a
          zombie; a matching git process is itself wedged and must not block us.
        - young lock (<= threshold) → keep the ``pgrep`` guard: delete only if
          NO git process matches (an orphan), else skip (a live commit may hold
          it legitimately).
        """
        lock_file = os.path.join(ws_path, ".git", "index.lock")
        if not os.path.exists(lock_file):
            return
        try:
            # TOCTOU-safe: the lock may vanish between checks (another process
            # cleaned it). getmtime/remove raising FileNotFoundError is benign.
            age = time.time() - os.path.getmtime(lock_file)

            if age > cls.STALE_LOCK_AGE_SECONDS:
                os.remove(lock_file)
                logger.warning(
                    "Removed stale .git/index.lock (age=%.0fs > %ds — a wedged "
                    "git cannot block cleanup)", age, cls.STALE_LOCK_AGE_SECONDS,
                )
                return

            # Young lock — a live commit may hold it; only remove a true orphan.
            result = subprocess.run(
                ["pgrep", "-f", f"git.*{ws_path}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:  # No matching git process
                os.remove(lock_file)
                logger.warning("Removed orphan .git/index.lock (young, no git process)")
            else:
                logger.info("Git index.lock is young and git is running — skipping cleanup")
        except FileNotFoundError:
            # Lock removed by another process between check and use — fine.
            pass
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

    def _smart_commit(
        self,
        ws_path: str,
        exclude: set[str] | None = None,
        session_id: str = "",
    ) -> None:
        """Run git operations in a background thread.

        All subprocess calls use ``GIT_TIMEOUT`` to fail fast on lock
        contention rather than hanging.  A ``TimeoutExpired`` aborts the
        commit attempt — the changes will be picked up next time.

        ``exclude``: absolute paths another live session is mid-edit — unstaged
        after the bulk add so a sibling's in-flight work is never swept into this
        session's commit (R29/DEC30). Empty/None → prior ``git add -A`` behavior.

        ``session_id``: this session's id, used AFTER staging to unstage any
        un-reviewed CODE path (R1 auto-commit door — see ``_uncovered_code_paths``).
        Empty → that check is skipped (prior behavior).
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
            #
            # 2c. R1 auto-commit door: also unstage any staged CODE path this
            # session's adversarial review did NOT cover (computed AFTER `git add
            # -A`, when the staged set exists). Un-reviewed code stays on disk for
            # a later gated commit; sediment (.md/.json) is never withheld. Union
            # into the same unstage set so both are reset in one pass.
            to_unstage = set(exclude) if exclude else set()
            if session_id:
                to_unstage |= self._uncovered_code_paths(ws_path, session_id)
            if to_unstage:
                self._unstage_paths(ws_path, to_unstage)

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
