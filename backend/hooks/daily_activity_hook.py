"""Post-session DailyActivity extraction hook.

Retrieves the conversation log from the database, passes it through
the ``SummarizationPipeline``, and appends the result to the
DailyActivity file.  Records success/failure in ``ComplianceTracker``.

Key public symbols:

- ``DailyActivityExtractionHook``  — Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from core import executors
from core.session_hooks import HookContext
from core.summarization import SummarizationPipeline
from core.daily_activity_writer import write_daily_activity
from core.compliance import ComplianceTracker
from core.ddd_paths import ddd_path
from database import db
from jobs.paths import STATE_DIR, SWARMWS

logger = logging.getLogger(__name__)


_SUBJECT_MAX = 120


def _clean_subject(subject: str) -> str:
    """A commit subject safe to put inside a markdown code span on one line."""
    subject = " ".join(subject.replace("`", "'").split())
    return subject if len(subject) <= _SUBJECT_MAX else subject[:_SUBJECT_MAX - 1] + "…"


def _touches_session_file(commit_files: list[str], session_files: list[str],
                          repo_path: Path | None = None) -> bool:
    """True when a commit changed a file this session wrote.

    Commit paths are repo-relative (``backend/core/x.py``). Session paths count
    only when absolute: a relative Bash target (``> out.txt``) has no known
    directory, so it cannot be tied to a repo. An absolute path matches when it
    ends with the commit path on a component boundary -- which also covers a
    worktree of the repo (``.../wt-topic/backend/core/x.py``). A commit path with
    no directory (``README.md``, ``VERSION``) is too common to match by suffix,
    so for it the session path must lie in ``repo_path`` itself.
    """
    root = str(repo_path).rstrip("/") + "/" if repo_path else None
    abs_files = [sf.strip() for sf in session_files if sf and sf.strip().startswith("/")]
    for cf in commit_files:
        cf = cf.strip()
        if cf.startswith("./"):
            cf = cf[2:]
        if not cf:
            continue
        top_level = "/" not in cf
        for sf in abs_files:
            if top_level:
                if root and sf == root + cf:
                    return True
            elif sf.endswith("/" + cf):
                return True
    return False


# A command that writes commits: a git write subcommand (options like -C/-c
# allowed in between), or a pipeline landing. `git log | grep commit` is not one.
_GIT_WRITE_RE = re.compile(
    r"\bgit(?:\s+-[Cc]\s+\S+)*\s+(?:commit|merge|rebase|cherry-pick|revert|am|pull)\b"
    r"|\bpipeline_v1\.py\b.*\bland\b"
    r"|\bartifact_cli\.py\b.*\b(?:land|deliver|run-commit|stage-complete)\b")
_RUN_ID_RE = re.compile(r"\brun_[0-9a-f]{8}\b")
_SLACK_S = 3.0  # seconds of clock slack around a tool call


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _bash_call_windows(messages: list[dict]) -> "list[tuple[float, float, bool]]":
    """(start, end, writes_git) for each Bash call in the transcript.

    start = when the tool_use was persisted, end = when its tool_result was;
    writes_git = the command text names a git write (commit/merge/rebase/...)
    or a pipeline landing. A commit whose committer time falls inside such a
    window was made by this session's own command.
    """
    import json as _json

    calls: dict[str, list] = {}
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            try:
                content = _json.loads(content)
            except ValueError:
                continue
        if not isinstance(content, list):
            continue
        ts = _parse_ts(m.get("created_at"))
        if ts is None:
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and (b.get("category") == "bash" or b.get("name") == "Bash"):
                text = str(b.get("summary") or (b.get("input") or {}).get("command") or "")
                calls[str(b.get("id"))] = [ts, ts, bool(_GIT_WRITE_RE.search(text))]
            elif b.get("type") == "tool_result" and str(b.get("tool_use_id")) in calls:
                calls[str(b.get("tool_use_id"))][1] = ts
    return [tuple(v) for v in calls.values()]


def _get_session_git_commits(
    repo_path: Path,
    since: datetime,
    max_commits: int = 15,
    session_files: list[str] | None = None,
    session_shas: "set[str] | None" = None,
    call_windows: "list[tuple[float, float, bool]] | None" = None,
    session_run_ids: "set[str] | None" = None,
) -> list[str]:
    """Get git commits from a repo since a given time.

    Returns a list of ``"<short_hash> <subject>"`` strings, newest first.
    Gracefully returns an empty list if the path is not a git repo or
    git is unavailable.

    Args:
        repo_path: Path to a git repository (or any directory — non-repos
            return empty).
        since: Only include commits after this timestamp.
        max_commits: Maximum number of commits to return.
        session_files / session_shas / session_run_ids / call_windows: when
            any is given, attribution mode. A commit is this session's when
            (a) a pipeline run of the session landed it (its SHA is in
            ``session_shas``, or its message names a run in
            ``session_run_ids``), or (b) its committer time falls inside one
            of the session's own git-writing Bash calls. Changed files are
            NOT evidence when the transcript is known: concurrent sessions edit
            the same hot files in the same minutes (measured: that rule
            attributed another session's run commit). Without call windows (no
            transcript) it falls back to (a) plus touched files. Without any of
            them every commit in the window is returned -- the old leak.
    """
    if not repo_path.is_dir():
        return []

    # Verify this directory IS a git repo root (contains .git/).
    # Without this check, git traverses upward and returns commits from
    # a parent repo — producing false positives for arbitrary directories.
    if not (repo_path / ".git").exists():
        return []

    since_str = since.strftime("%Y-%m-%dT%H:%M:%S")
    attributing = (session_files is not None or session_shas is not None
                   or call_windows is not None or session_run_ids is not None)
    files = list(session_files or [])
    shas = set(session_shas or ())
    run_ids = set(session_run_ids or ())
    windows = [w for w in (call_windows or []) if w[2]]
    if attributing and not shas and not run_ids and not windows \
            and not (files and call_windows is None):
        return []  # no evidence that this session made any commit
    try:
        if not attributing:
            cmd = ["git", "log", f"--since={since_str}", f"--max-count={max_commits}",
                   "--oneline", "--no-decorate"]
        else:
            # Bounded scan (300 commits, ~0.1-0.3 s here), then attribute.
            cmd = ["git", "log", f"--since={since_str}", "--max-count=300",
                   "--no-decorate", "--no-merges", "--name-only",
                   "--format=\x1e%H %h %ct %s\x1f%b\x1f"]
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        if not attributing:
            return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        out: list[str] = []
        for rec in result.stdout.split("\x1e"):
            parts = rec.split("\x1f")
            if len(parts) < 3:
                continue
            head = parts[0].strip().split(" ", 3)
            if len(head) < 3 or not head[2].isdigit():
                continue
            full, short, ct = head[0], head[1], int(head[2])
            subject = head[3] if len(head) > 3 else ""
            names_run = bool(run_ids) and bool(set(_RUN_ID_RE.findall(parts[0] + parts[1])) & run_ids)
            mine = full in shas or names_run
            if not mine and windows:
                mine = any(a - _SLACK_S <= ct <= b + _SLACK_S for (a, b, _g) in windows)
            if not mine and call_windows is None:
                changed = [ln for ln in parts[2].splitlines() if ln.strip()]
                mine = _touches_session_file(changed, files, repo_path)
            if mine:
                out.append(f"{short} {_clean_subject(subject)}")
                if len(out) >= max_commits:
                    break
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Git log failed for %s: %s", repo_path, exc)
        return []


def _session_run_evidence(session_id: str) -> "tuple[set[str], list[str], set[str]]":
    """(landed SHAs, touched files, run ids) of the pipeline runs this session owns.

    A run's edits are made by its own agents, not by tool calls in this
    session's transcript, so without this a session that drove a run gets none
    of the run's commits. v1 runs record ``commits[].sha`` in run.json; legacy
    runs record ``files_touched`` and name the run id in the landing commit
    message. Both carry ``session_id``. Reads the shared
    incremental run index (one stat per run, parse only on change).
    """
    shas: set[str] = set()
    files: list[str] = []
    ids: set[str] = set()
    if not session_id:
        return shas, files, ids
    try:
        from core.run_index import runs
        for rf in runs(SWARMWS):
            data = rf.data
            if data.get("session_id") != session_id:
                continue
            if isinstance(data.get("id"), str):
                ids.add(data["id"])
            for c in data.get("commits") or []:
                if isinstance(c, dict) and isinstance(c.get("sha"), str) and len(c["sha"]) == 40:
                    shas.add(c["sha"])
            for f in data.get("files_touched") or []:
                if isinstance(f, str):
                    files.append(f)
    except Exception:  # noqa: BLE001 -- evidence is best-effort, never blocks DA
        logger.debug("run evidence lookup failed for %s", session_id, exc_info=True)
    return shas, files, ids


def recover_crash_checkpoint(workspace_dir: Path | None = None) -> bool:
    """Recover session data from an orphaned checkpoint file.

    Called at startup (before any session hook runs).  If a checkpoint
    file exists from a prior crashed session, writes its data as that
    session's checkpoint block in today's DailyActivity — replacing the
    block the live checkpoint hook already wrote, never adding a second one —
    and deletes the checkpoint.

    Returns True if a checkpoint was recovered, False otherwise.
    """
    checkpoint_path = STATE_DIR / "session_checkpoint.json"
    if not checkpoint_path.exists():
        return False

    try:
        import json
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        session_id = data.get("session_id", "unknown")
        ts = data.get("ts", 0)
        tool_count = data.get("tool_count", 0)
        files_touched = data.get("files_touched", [])

        if not ts or not session_id:
            checkpoint_path.unlink(missing_ok=True)
            return False

        # Build recovery entry with enriched content (git_commits if available)
        from datetime import datetime
        crash_time = datetime.fromtimestamp(ts).strftime("%H:%M")
        git_commits = data.get("git_commits", [])
        corrections_count = data.get("corrections_count", 0)

        entry = (
            f"## {crash_time} | {session_id[:8]} | ⚠️ Recovered from crash checkpoint\n"
            f"**What happened:** Session crashed or was evicted after {tool_count} tool calls.\n"
        )
        if files_touched:
            from pathlib import PurePosixPath
            file_names = [PurePosixPath(f).name for f in files_touched[:10]]
            entry += f"**Files:** {', '.join(f'`{f}`' for f in file_names)}\n"
        if git_commits:
            entry += "**Git activity:**\n"
            for c in git_commits[:3]:
                entry += f"- `{c[:72]}`\n"
        if corrections_count:
            entry += f"**Corrections:** {corrections_count}\n"

        # Upsert the session's checkpoint block in today's DailyActivity
        from core.daily_activity_writer import upsert_session_block
        ws = workspace_dir or SWARMWS
        today = datetime.now().strftime("%Y-%m-%d")
        da_file = ws / "Knowledge" / "DailyActivity" / f"{today}.md"
        upsert_session_block(da_file, session_id, "checkpoint", entry)

        checkpoint_path.unlink(missing_ok=True)
        logger.info(
            "Recovered crash checkpoint for session %s (%d tool calls)",
            session_id[:8], tool_count,
        )
        return True

    except Exception:
        logger.exception("Failed to recover crash checkpoint")
        # Delete corrupt checkpoint to prevent repeated failures
        checkpoint_path.unlink(missing_ok=True)
        return False


class DailyActivityExtractionHook:
    """Extracts conversation summaries into DailyActivity files.

    Registered as the first post-session-close hook so that
    DailyActivity is written before workspace auto-commit captures it.
    """

    name = "daily_activity_extraction"

    def __init__(
        self,
        summarization_pipeline: SummarizationPipeline,
        compliance_tracker: ComplianceTracker,
    ) -> None:
        self._pipeline = summarization_pipeline
        self._tracker = compliance_tracker
        self._lock = asyncio.Lock()

    async def execute(self, context: HookContext) -> None:
        """Extract DailyActivity from the closed session's conversation."""
        # Acquire lock with 10s timeout to prevent deadlock if holder crashes
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(
                "DailyActivity lock acquisition timed out after 10s — "
                "skipping extraction for session %s",
                context.session_id,
            )
            return

        try:
            await self._execute_locked(context)
        finally:
            self._lock.release()

    async def _execute_locked(self, context: HookContext) -> None:
        """Core extraction logic, called while holding ``_lock``."""
        # 0. Clean up session checkpoint — normal session end means no crash.
        # If the file is not deleted, recover_crash_checkpoint() would
        # incorrectly treat it as a crash on next startup.
        _checkpoint_path = STATE_DIR / "session_checkpoint.json"
        _checkpoint_path.unlink(missing_ok=True)

        # 1. Retrieve conversation log (capped for memory safety)
        messages = await db.messages.list_by_session_paginated(
            context.session_id, limit=500
        )

        if not messages:
            logger.info(
                "No messages for session %s, skipping extraction",
                context.session_id,
            )
            return

        # 2. Summarize — minimal for short conversations
        if len(messages) < 3:
            summary = self._pipeline.minimal_summary(messages)
        else:
            summary = await self._pipeline.summarize(messages)

        # 2b. Capture git ground truth — actual commits during session
        # This prevents COE C005: DailyActivity text claims vs git reality diverging.
        # Window = the conversation being summarized (a resumed session's
        # created_at can be days old; other sessions' commits fill that span).
        _stamps = [m.get("created_at") for m in messages if m.get("created_at")]
        _window_start = min(_stamps) if _stamps else context.session_start_time
        summary.git_commits = await executors.run_in(
            "subprocess", self._capture_git_activity, _window_start,
            list(summary.files_modified or []), context.session_id,
            _bash_call_windows(messages),
        )

        # 2c. Quality filter — skip noise entries that add no insight
        # Prevents: pure "read file X" logs, empty summaries, trivial sessions
        if self._is_noise_summary(summary):
            logger.info(
                "DailyActivity skipped for session %s — noise (no insight content)",
                context.session_id,
            )
            # PE-2: record gate trigger for noise_filter
            try:
                from core.gate_promotion import GateManager
                from core.initialization_manager import initialization_manager
                ws = initialization_manager.get_cached_workspace_path()
                if ws:
                    from pathlib import Path
                    artifacts = Path(ws) / "Projects" / "SwarmAI" / ".artifacts"
                    if artifacts.is_dir():
                        GateManager(artifacts).record_trigger("noise_filter")
            except Exception:
                pass
            return

        # 2d. Mine observation patterns (if ring available for this session)
        try:
            from core.observation_miner import ObservationMiner, write_patterns
            from core.observation_hooks import get_session_ring, _cleanup_session_ring
            ring = get_session_ring(context.session_id)
            if ring is not None:
                miner = ObservationMiner()
                patterns = miner.mine(ring.all_completed())
                if patterns:
                    write_patterns(patterns, context.session_id)
                    logger.debug(
                        "ObservationMiner: %d patterns extracted for session %s",
                        len(patterns), context.session_id,
                    )
                # Clean up ring from module registry (prevent memory leak)
                _cleanup_session_ring(context.session_id)
        except Exception:
            pass  # Pattern mining is best-effort, never blocks DA extraction

        # 3. Write to DailyActivity file
        try:
            path = await write_daily_activity(summary, context)
            self._tracker.record_success(context.session_id)
            logger.info(
                "DailyActivity extracted for session %s → %s",
                context.session_id,
                path,
            )

            # 3b. Emit DAILY_ACTIVITY event for DDD cultivation v2
            try:
                from core.cultivation_dispatcher import (
                    EventType, emit_cultivation_event,
                )
                await emit_cultivation_event(
                    EventType.DAILY_ACTIVITY,
                    source="daily_activity_hook",
                    payload={"path": str(path), "session_id": context.session_id},
                    priority=2,
                )
            except Exception:
                pass  # Non-blocking: cultivation emit never breaks extraction

        except Exception as exc:
            self._tracker.record_failure(context.session_id, str(exc))
            raise  # Re-raise so hook manager logs it

    @staticmethod
    def _is_noise_summary(summary) -> bool:
        """Check if a summary is pure noise with no insight value.

        Handles StructuredSummary dataclass (has .decisions, .topics, etc).

        Returns True (skip) if:
        - No decisions, lessons, or deliverables recorded
        - Topics are empty or single-item trivial
        - Files modified is empty (no actual work)

        Returns False (keep) if ANY substantive content exists.
        """
        # PE-2 fix: access actual StructuredSummary fields safely
        decisions = getattr(summary, "decisions", None) or []
        lessons = getattr(summary, "lessons", None) or []
        deliverables = getattr(summary, "deliverables", None) or []
        topics = getattr(summary, "topics", None) or []
        files_modified = getattr(summary, "files_modified", None) or []
        open_questions = getattr(summary, "open_questions", None) or []

        # If there are decisions, lessons, or deliverables — always keep
        if decisions or lessons or deliverables:
            return False

        # If there are open questions — keep (continuity value)
        if open_questions:
            return False

        # If files were modified — keep (work evidence)
        if files_modified:
            return False

        # Check topics for substance
        if not topics:
            return True  # Nothing happened

        # Single trivial topic with no other content = noise
        total_topic_chars = sum(len(t) for t in topics)
        if total_topic_chars < 30:
            return True

        return False

    @staticmethod
    def _capture_git_activity(session_start_iso: str,
                              session_files: list[str] | None = None,
                              session_id: str = "",
                              call_windows: "list[tuple[float, float, bool]] | None" = None,
                              ) -> list[str]:
        """Capture this session's own git commits from source repos.

        Scans ALL projects' TECH.md for repo paths — no hardcoded paths.
        Attribution (other sessions commit to the same repos): see
        ``_get_session_git_commits``. Commits from every distinct repo, at
        most 15. Empty list on any failure.
        """
        run_shas, run_files, run_ids = _session_run_evidence(session_id)
        session_files = list(session_files or []) + run_files
        try:
            since = datetime.fromisoformat(session_start_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since = datetime.now() - timedelta(hours=2)

        # Discover repos from all projects' TECH.md files
        candidates: list[Path] = []
        try:
            from core.initialization_manager import initialization_manager
            ws = Path(initialization_manager.get_cached_workspace_path())
            projects_dir = ws / "Projects"
            if projects_dir.is_dir():
                for project_dir in sorted(projects_dir.iterdir()):
                    tech_md = ddd_path(project_dir, "TECH.md")
                    if not tech_md.is_file():
                        continue
                    try:
                        content = tech_md.read_text(encoding="utf-8")
                        for line in content.splitlines():
                            if any(kw in line for kw in (
                                "Clone:", "local:", "Local:",
                                "Codebase", "codebase", "repo",
                                "source", "Source",
                            )):
                                import re
                                paths = re.findall(r"(/[^\s`\"']+)", line)
                                for p in paths:
                                    p = p.rstrip("/),;.")
                                    if len(p) > 5:
                                        candidates.append(Path(p))
                    except (OSError, UnicodeDecodeError):
                        continue
        except Exception:
            pass

        # Every distinct repo once (TECH.md files repeat the same paths), and
        # commits from all of them: a session can land work in several repos.
        commits: list[str] = []
        seen: set[str] = set()
        for repo_path in candidates:
            try:
                key = str(repo_path.resolve())
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            if repo_path.is_dir() and (repo_path / ".git").exists():
                commits += _get_session_git_commits(
                    repo_path, since, session_files=session_files,
                    session_shas=run_shas, call_windows=call_windows,
                    session_run_ids=run_ids)
                if len(commits) >= 15:
                    break
        return commits[:15]
