"""Daily todo-resolution job — auto-resolves stale Radar Todos.

Three resolution layers, run sequentially:

1. **Pipeline completion** — Todos created by pipeline pause (source starts
   with ``escalation:``) are resolved when the associated pipeline run
   reaches ``completed`` status.

2. **Git keyword match** — Active todos (pending / in_discussion) are matched
   against recent git commits.  Keywords from the title that appear in
   ``git log --oneline`` trigger a state transition:
   ``pending → in_discussion`` (first match), ``in_discussion → handled``
   (subsequent run match).

3. **Staleness cancellation** — Pending todos with ``updated_at`` older than
   ``stale_days`` (default 21) are auto-cancelled.

Key public symbols:

- ``run_todo_resolution()`` — Entry point, returns a result dict.
- ``_get_recent_commits()`` — Git log helper (patchable in tests).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_DEFAULT_DB = Path.home() / ".swarm-ai" / "data.db"

# Words too short or too generic to be meaningful keywords
_STOPWORDS = frozenset({
    # English stopwords
    "the", "and", "for", "with", "from", "into", "that", "this",
    "have", "has", "was", "are", "not", "but", "all", "can", "will",
    "new", "add", "fix", "use", "get", "set", "run", "bug", "old",
    # Common pipeline/todo noise
    "pipeline", "paused", "implement", "design", "feature", "mode",
    "todo", "task",
})

# Minimum keyword length (skip very short tokens)
_MIN_KEYWORD_LEN = 4

# ── Git Helper ───────────────────────────────────────────────────────


def _get_recent_commits(
    codebase_path: Path | None = None,
    days: int = 7,
) -> str:
    """Return ``git log --oneline`` for the last N days.

    Returns a multi-line string of commit summaries.  Returns empty
    string on any error (no git, no repo, timeout).

    This function is the patch point for tests — mock it to inject
    fake commit history.
    """
    if codebase_path is None:
        codebase_path = _find_codebase_path()
    if codebase_path is None or not codebase_path.is_dir():
        return ""

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={since}",
                "--oneline",
                "--no-decorate",
                "--max-count=200",
            ],
            cwd=str(codebase_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("Git log failed: %s", exc)
        return ""


def _find_codebase_path() -> Path | None:
    """Discover the SwarmAI codebase path (same logic as todo_lifecycle_hook)."""
    import os

    env_path = os.environ.get("SWARMAI_CODEBASE_DIR")
    if env_path:
        p = Path(env_path)
        if p.is_dir() and (p / ".git").exists():
            return p

    candidates = [
        Path.home() / "Desktop" / "SwarmAI-Workspace" / "swarmai",
        Path.home() / "swarmai",
        Path.home() / "Projects" / "swarmai",
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / ".git").exists():
            return candidate
    return None


# ── Keyword Extraction ───────────────────────────────────────────────


def _extract_keywords(title: str) -> list[str]:
    """Extract meaningful keywords from a todo title.

    Filters out stopwords, short tokens, and common noise.
    Handles mixed English/Chinese text.
    """
    # Split on whitespace, punctuation, CJK boundaries
    tokens = re.findall(r"[a-zA-Z0-9_-]{2,}|[一-鿿]+", title)

    keywords = []
    for token in tokens:
        lower = token.lower()
        # Skip stopwords and short English tokens
        if lower in _STOPWORDS:
            continue
        # For ASCII tokens, require minimum length
        if token.isascii() and len(token) < _MIN_KEYWORD_LEN:
            continue
        keywords.append(lower)

    return keywords


# ── Layer 1: Pipeline Completion ─────────────────────────────────────


def _resolve_completed_pipelines(
    conn: sqlite3.Connection,
    artifacts_root: Path | None,
) -> int:
    """Mark pipeline-pause todos as handled when their pipeline completed."""
    if artifacts_root is None:
        return 0

    rows = conn.execute(
        """SELECT id, source, linked_context FROM todos
           WHERE status IN ('pending', 'in_discussion')
             AND source LIKE 'escalation:%'"""
    ).fetchall()

    resolved = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        todo_id, source, linked_raw = row
        # Extract pipeline_id from linked_context
        pipeline_id = None
        if linked_raw:
            try:
                ctx = json.loads(linked_raw)
                pipeline_id = ctx.get("pipeline_id")
            except (json.JSONDecodeError, TypeError):
                logger.debug("Corrupt linked_context for todo %s", todo_id[:8])
                continue

        if not pipeline_id:
            continue

        # Check run.json status
        run_json = artifacts_root / "runs" / pipeline_id / "run.json"
        if not run_json.exists():
            continue

        try:
            run_data = json.loads(run_json.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Corrupt run.json for %s: %s", pipeline_id, exc)
            continue

        if run_data.get("status") == "completed":
            conn.execute(
                "UPDATE todos SET status = 'handled', updated_at = ? WHERE id = ?",
                (now, todo_id),
            )
            resolved += 1
            logger.info(
                "todo-resolution: %s → handled (pipeline %s completed)",
                todo_id[:8], pipeline_id[:12],
            )

    if resolved:
        conn.commit()
    return resolved


# ── Layer 2: Git Keyword Match ───────────────────────────────────────


def _resolve_by_git_keywords(
    conn: sqlite3.Connection,
    git_log: str,
) -> dict:
    """Match active todos against recent git commits by keyword overlap."""
    result = {"to_discussion": 0, "to_handled": 0}

    if not git_log.strip():
        return result

    git_log_lower = git_log.lower()

    rows = conn.execute(
        """SELECT id, title, status FROM todos
           WHERE status IN ('pending', 'in_discussion')"""
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()

    for todo_id, title, status in rows:
        keywords = _extract_keywords(title)
        if not keywords:
            continue

        # Require at least 2 keyword matches to reduce false positives
        match_count = sum(1 for kw in keywords if kw in git_log_lower)

        if match_count < 2:
            continue

        if status == "pending":
            conn.execute(
                "UPDATE todos SET status = 'in_discussion', updated_at = ? WHERE id = ?",
                (now, todo_id),
            )
            result["to_discussion"] += 1
            logger.info(
                "todo-resolution: %s → in_discussion (git keywords: %d/%d matched)",
                todo_id[:8], match_count, len(keywords),
            )
        elif status == "in_discussion":
            conn.execute(
                "UPDATE todos SET status = 'handled', updated_at = ? WHERE id = ?",
                (now, todo_id),
            )
            result["to_handled"] += 1
            logger.info(
                "todo-resolution: %s → handled (git keywords: %d/%d matched)",
                todo_id[:8], match_count, len(keywords),
            )

    if result["to_discussion"] or result["to_handled"]:
        conn.commit()
    return result


# ── Layer 3: Staleness Cancellation ──────────────────────────────────


def _cancel_stale_todos(
    conn: sqlite3.Connection,
    stale_days: int = 21,
    working_stale_days: int = 14,
) -> int:
    """Cancel stale todos based on status-specific thresholds.

    - ``pending`` todos: cancelled after ``stale_days`` (default 21d).
      These are backlog items — longer grace period is appropriate.
    - ``in_discussion`` (WORKING section): cancelled after
      ``working_stale_days`` (default 14d).  If nobody touched it in
      14 days, it's not actually being worked on.
    """
    now = datetime.now(timezone.utc).isoformat()
    total = 0

    # Pending: 21-day threshold
    pending_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=stale_days)
    ).isoformat()
    cursor = conn.execute(
        """UPDATE todos SET status = 'cancelled', updated_at = ?
           WHERE status = 'pending' AND updated_at < ?""",
        (now, pending_cutoff),
    )
    pending_count = cursor.rowcount
    total += pending_count

    # Working (in_discussion): 5-day threshold
    working_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=working_stale_days)
    ).isoformat()
    cursor = conn.execute(
        """UPDATE todos SET status = 'cancelled', updated_at = ?
           WHERE status = 'in_discussion' AND updated_at < ?""",
        (now, working_cutoff),
    )
    working_count = cursor.rowcount
    total += working_count

    if total:
        conn.commit()
        parts = []
        if pending_count:
            parts.append(f"{pending_count} pending (>{stale_days}d)")
        if working_count:
            parts.append(f"{working_count} working (>{working_stale_days}d)")
        logger.info("todo-resolution: cancelled %d stale (%s)", total, ", ".join(parts))

    return total


# ── Entry Point ──────────────────────────────────────────────────────


def run_todo_resolution(
    *,
    db_path: Path | None = None,
    artifacts_root: Path | None = None,
    codebase_path: Path | None = None,
    stale_days: int = 21,
    working_stale_days: int = 14,
    git_days: int = 7,
) -> dict:
    """Run all 3 todo-resolution layers.

    Args:
        db_path: Override DB path (for testing). Defaults to ~/.swarm-ai/data.db.
        artifacts_root: Override artifacts root (for testing).
            Defaults to Projects/SwarmAI/.artifacts/ in SwarmWS.
        codebase_path: Override codebase path (for testing).
        stale_days: Days of inactivity before pending todos are cancelled.
        working_stale_days: Days before in_discussion (WORKING) todos are cancelled.
        git_days: Days of git history to search for keyword matches.

    Returns:
        Dict with counts: pipeline_resolved, git_resolved, stale_cancelled, errors.
    """
    _db = db_path or _DEFAULT_DB
    result = {
        "pipeline_resolved": 0,
        "git_resolved": 0,
        "stale_cancelled": 0,
        "errors": [],
    }

    if not _db.exists():
        logger.debug("todo-resolution: DB not found at %s", _db)
        return result

    # Default artifacts root
    if artifacts_root is None:
        ws = Path.home() / ".swarm-ai" / "SwarmWS"
        artifacts_root = ws / "Projects" / "SwarmAI" / ".artifacts"

    try:
        conn = sqlite3.connect(str(_db), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error as exc:
        logger.warning("todo-resolution: DB connect failed: %s", exc)
        result["errors"].append(str(exc))
        return result

    try:
        # Layer 1: Pipeline completion
        try:
            result["pipeline_resolved"] = _resolve_completed_pipelines(
                conn, artifacts_root,
            )
        except Exception as exc:
            logger.warning("todo-resolution: pipeline layer failed: %s", exc)
            result["errors"].append(f"pipeline: {exc}")

        # Layer 2: Git keyword match
        try:
            git_log = _get_recent_commits(codebase_path, days=git_days)
            git_result = _resolve_by_git_keywords(conn, git_log)
            result["git_resolved"] = git_result["to_discussion"] + git_result["to_handled"]
        except Exception as exc:
            logger.warning("todo-resolution: git layer failed: %s", exc)
            result["errors"].append(f"git: {exc}")

        # Layer 3: Staleness
        try:
            result["stale_cancelled"] = _cancel_stale_todos(
                conn, stale_days, working_stale_days,
            )
        except Exception as exc:
            logger.warning("todo-resolution: staleness layer failed: %s", exc)
            result["errors"].append(f"staleness: {exc}")

    finally:
        conn.close()

    total = result["pipeline_resolved"] + result["git_resolved"] + result["stale_cancelled"]
    if total:
        logger.info(
            "todo-resolution: %d transitions (pipeline=%d, git=%d, stale=%d)",
            total, result["pipeline_resolved"], result["git_resolved"], result["stale_cancelled"],
        )

    return result
