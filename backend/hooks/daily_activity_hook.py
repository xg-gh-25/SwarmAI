"""Post-session DailyActivity extraction hook.

Reads the session's persisted messages, summarizes the ones from the current local
day (``summarize_session_day``) and upserts that day's row in DailyActivity. Records
success/failure in ``ComplianceTracker``. Design:
docs/design/2026-10-31-daily-activity-useful.md.

It creates no ToDo (design docs/design/2026-10-31-continue-lookback-todo.md D2 r4: a
ToDo is written by the person, or by the agent in the session, which says so in its
reply). The summary is shown every open ToDo and names the ones the session finished;
``apply_todo_actions`` sends each to review as a suggestion through the guarded write
(``todo_manager.to_review``): only a person closes it. A row holds no follow-up list.

Key public symbols:

- ``DailyActivityExtractionHook`` — Implements ``SessionLifecycleHook``.
- ``summarize_session_day``       — One session-day's summary (also used by
                                    ``scripts/da_resummarize.py``).
- ``messages_on_day``             — The messages of one local day.
- ``open_todos_shown``            — The open ToDos a summary is shown.
- ``apply_todo_actions``          — Send the ToDos a summary says are finished to review.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import dataclasses
from datetime import date, datetime, timedelta
from pathlib import Path

from core import executors
from core.session_hooks import HookContext
from core.summarization import SummarizationPipeline, record_of
from core.daily_activity_writer import write_daily_activity
from core.compliance import ComplianceTracker
from core.ddd_paths import ddd_path
from database import db
from jobs.paths import STATE_DIR, SWARMWS
from core.project_registry import DEFAULT_PROJECT_NAME

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
    r"\bgit(?:\s+-[Cc]\s+\S+)*\s+(?:commit|merge|rebase|cherry-pick|revert|am|pull)\b")
# `git [-C dir] commit <options>` as a command of its own (not `git log | grep commit`)
_COMMIT_CMD_RE = re.compile(r"\bgit(?:\s+-[Cc]\s+\S+)*\s+commit\b(?P<rest>[^\n]*)")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(?P<tag>\w+)\1")
_MESSAGE_RE = re.compile(r"(?:\s-[A-Za-z]*m\s*|\s--message[=\s])(?P<q>['\"])")
_MIN_SUBJECT = 16   # characters of a subject that must match


def _writes_commits(command: str) -> bool:
    from core import pipeline_command
    return bool(_GIT_WRITE_RE.search(command)) or pipeline_command.is_land(command)
_RUN_ID_RE = re.compile(r"\brun_[0-9a-f]{8}\b")
_SLACK_S = 3.0  # seconds of clock slack around a tool call


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _subjects_in(text: str) -> "list[tuple[str, bool]]":
    """``(subject, cut)`` of every ``git commit`` in one stored command: the message's
    first line from ``-F - <<EOF``, ``-m "$(cat <<EOF ...)"`` or ``-m "..."``. ``cut``:
    the stored command ends inside it (the summary is cut short), so only its start is
    known."""
    out: list[tuple[str, bool]] = []
    for match in _COMMIT_CMD_RE.finditer(text):
        rest, after = match.group("rest"), match.end()
        subject, end = None, None
        heredoc = _HEREDOC_RE.search(rest)
        message = _MESSAGE_RE.search(" " + rest)
        if message is not None and not (heredoc and heredoc.start() < message.start() - 1
                                        and "-F" in rest[:heredoc.start()]):
            value_at = match.start("rest") + message.end() - 1
            value = text[value_at:]
            inner = _HEREDOC_RE.search(value.split("\n", 1)[0]) if value.startswith("$(") else None
            if inner is not None:                       # -m "$(cat <<'EOF' ... EOF )"
                body = value.split("\n", 1)[1] if "\n" in value else ""
                subject = body.split("\n", 1)[0]
                end = value_at + len(value.split("\n", 1)[0]) + 1 + len(subject)
            else:                                        # -m "subject ..."
                quote = message.group("q")
                close = value.find(quote)
                raw = value if close < 0 else value[:close]
                subject = raw.split("\n", 1)[0]
                end = value_at + len(subject)
        elif heredoc is not None:                       # -F - <<'EOF'
            body = text[after + 1:] if after < len(text) else ""
            subject = body.split("\n", 1)[0]
            end = after + 1 + len(subject)
        if subject is None:
            continue
        subject = subject.strip()
        cut = end is not None and end >= len(text.rstrip())
        if len(subject) >= _MIN_SUBJECT:
            out.append((subject, cut))
    return out


def commit_subjects(messages: list[dict]) -> "list[tuple[str, bool]]":
    """The commit subjects this session's own ``git commit`` commands wrote, each with
    whether the stored command was cut inside it (then only its start is known). A
    commit with exactly that subject (or, when cut, a subject starting with it) was made
    by this session, whatever its committer time: the time misses a commit whose hooks
    ran past the call's recorded end, and a commit the worktree land rebased."""
    import json as _json

    out: list[tuple[str, bool]] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            try:
                content = _json.loads(content)
            except ValueError:
                continue
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"
                    and (b.get("category") == "bash" or b.get("name") == "Bash")):
                continue
            text = str((b.get("input") or {}).get("command") or b.get("summary") or "")
            for item in _subjects_in(text):
                if item not in out:
                    out.append(item)
    return out


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
                calls[str(b.get("id"))] = [ts, ts, _writes_commits(text)]
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
    until: "datetime | None" = None,
    subjects: "list[tuple[str, bool]] | None" = None,
) -> list[str]:
    """Get git commits from a repo since a given time (and up to *until*).

    Returns a list of ``"<short_hash> <subject>"`` strings, newest first.
    Gracefully returns an empty list if the path is not a git repo or
    git is unavailable.

    Args:
        repo_path: Path to a git repository (or any directory — non-repos
            return empty).
        since: Only include commits after this timestamp.
        until: In attribution mode, drop commits made after this time (a row
            covers one day; a run landed later belongs to that later day).
        subjects: ``(subject, cut)`` this session's git commit commands wrote
            (``commit_subjects``); a commit with that subject (starting with it when
            cut) is its own.
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
    subjects = [(x, cut) for x, cut in (subjects or []) if len(x) >= _MIN_SUBJECT]
    if attributing and not shas and not run_ids and not windows and not subjects \
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
            if until is not None and ct > until.timestamp() + _SLACK_S:
                continue
            subject = head[3] if len(head) > 3 else ""
            names_run = bool(run_ids) and bool(set(_RUN_ID_RE.findall(parts[0] + parts[1])) & run_ids)
            mine = full in shas or names_run
            if not mine and windows:
                mine = any(a - _SLACK_S <= ct <= b + _SLACK_S for (a, b, _g) in windows)
            if not mine and subjects:
                mine = any(subject.startswith(x) if cut else subject.strip() == x
                           for x, cut in subjects)
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


def _session_run_evidence(session_id: str, workspace: "Path | None" = None,
                          ) -> "tuple[set[str], set[str]]":
    """(landed SHAs, run ids) of the Pipeline v1 runs this session owns.

    A run's edits are made by its own agents, not by tool calls in this
    session's transcript, so without this a session that drove a run gets none
    of the run's commits. Both landing
    paths -- the normal DELIVER landing and ``run-land`` -- persist the landed
    SHAs to ``<run_dir>/artifacts/landing-progress-*.json`` and then
    ``RunEngine.write_projection`` publishes them as ``run.json`` ``commits[]``
    beside the contract's ``session_id``; the landing is ``commit-tree`` +
    ``merge --ff-only`` into the contract's repo roots, so the SHA in run.json
    is the SHA on main. The run.json ``session_id`` is the signed owner. Reads the shared
    incremental run index (one stat per run, parse only on change).
    """
    shas: set[str] = set()
    ids: set[str] = set()
    if not session_id:
        return shas, ids
    try:
        from core.run_index import runs
        for rf in runs(workspace or SWARMWS):
            data = rf.data
            if data.get("session_id") != session_id:
                continue
            if isinstance(data.get("id"), str):
                ids.add(data["id"])
            for c in data.get("commits") or []:
                if isinstance(c, dict) and isinstance(c.get("sha"), str) and len(c["sha"]) == 40:
                    shas.add(c["sha"])
    except Exception:  # noqa: BLE001 -- evidence is best-effort, never blocks DA
        logger.debug("run evidence lookup failed for %s", session_id, exc_info=True)
    return shas, ids


def _open_problem_titles(workspace: "Path | None" = None) -> list[str]:
    """Titles of the open COEs (core.coe_threads), for the summariser to reuse
    verbatim when a session reports or resolves one of them, so the COE topic keeps
    its coe_key. Unavailable -> [] (the summary is written as before)."""
    try:
        from core.coe_threads import open_coe_titles
        return open_coe_titles(workspace or SWARMWS)
    except Exception:  # noqa: BLE001 — a hint for the summariser, never a blocker
        logger.debug("open problem titles unavailable", exc_info=True)
        return []


def recover_crash_checkpoint(workspace_dir: Path | None = None) -> bool:
    """Remove the checkpoint JSON a crashed session left behind.

    Called at startup. The day's activity row needs nothing from it: the session's
    messages are persisted, and the startup scan (lifecycle_manager) and the idle hook
    summarize it from them. Returns True when one was removed.
    """
    checkpoint_path = STATE_DIR / "session_checkpoint.json"
    if not checkpoint_path.exists():
        return False
    checkpoint_path.unlink(missing_ok=True)
    logger.info("Removed the crash checkpoint of an earlier session")
    return True


async def _read_complete_transcript(session_id: str, page_size: int = 200,
                                    repo=None) -> list[dict]:
    """Read every persisted message in stable chronological order, one page at a time.
    *repo*: the caller's messages repository (default ``db.messages``)."""
    repo = repo if repo is not None else db.messages
    pages: list[list[dict]] = []
    before_id: str | None = None
    while True:
        page = await repo.list_by_session_paginated(
            session_id, limit=page_size, before_id=before_id
        )
        if not page:
            break
        pages.append(page)
        if len(page) < page_size:
            break
        oldest_id = page[0].get("id")
        if not oldest_id or oldest_id == before_id:
            raise RuntimeError("DailyActivity transcript pagination did not advance")
        before_id = oldest_id
    return [message for page in reversed(pages) for message in page]

def messages_on_day(messages: list[dict], day: date) -> list[dict]:
    """The messages whose ``created_at`` (naive local time) falls on *day*."""
    prefix = day.isoformat()
    return [m for m in messages if str(m.get("created_at") or "").startswith(prefix)]


def _day_rows(workspace: Path, day: date) -> list[dict]:
    from core.daily_activity_writer import read_jsonl_sidecar
    return read_jsonl_sidecar(workspace / "Knowledge" / "DailyActivity" / f"{day.isoformat()}.jsonl")


def current_row(workspace: Path, session_id: str, day: date) -> "dict | None":
    """This session's row on *day*, if any."""
    for row in _day_rows(workspace, day):
        if row.get("session_id") == session_id:
            return row
    return None


def earlier_row(workspace: Path, session_id: str, day: date, *, days: int = 7,
                ) -> "dict[str, str] | None":
    """``{"day", "title", "sentence"}`` of this session's latest row in the *days* before
    *day*, so the summary of *day* records only what is new. Work it left open is in
    the ToDos the summary is shown, not here."""
    for back in range(1, days + 1):
        earlier = day - timedelta(days=back)
        row = current_row(workspace, session_id, earlier)
        if row is not None:
            return {"day": earlier.isoformat(), "title": str(row.get("title") or ""),
                    "sentence": str(row.get("summary_sentence") or "")}
    return None


def _scanner():
    """The injection scanner, or None when it cannot load (then no ToDo title is shown
    to the model)."""
    try:
        from core.injection_patterns import scan_text
    except Exception:  # noqa: BLE001 — fail closed: callers drop what they cannot check
        logger.warning("injection scanner unavailable; no ToDo is shown to the summary")
        return None
    return scan_text


async def open_todos_shown() -> list[dict[str, str]]:
    """The open ToDos a summary is shown (``{"id", "title"}``, oldest first): every
    pending / overdue / in_discussion one, the whole title. A title can come from an
    email or a chat, so one the injection scanner flags is left out of the prompt."""
    from core.todo_manager import todo_manager
    scan = _scanner()
    if scan is None:
        return []
    rows = await todo_manager.list_open()
    return [{"id": str(r["id"]), "title": " ".join(str(r.get("title") or "").split())}
            for r in rows if r.get("id") and r.get("title") and not scan(str(r["title"]))]


async def apply_todo_actions(actions: list[dict[str, str]], *, session_id: str, day: date,
                             session_title: str) -> dict:
    """Apply a summary's ToDo actions (already checked by ``_valid_todos`` against the
    ToDos it was shown): each ``done`` goes to review through ``todo_manager.to_review``
    as a suggestion (only an active ToDo with no review state moves; no auto-confirm; a
    person closes it). Any other action is ignored: the summary creates no ToDo (design
    2026-10-31-continue-lookback-todo D2 r4). One failing action does not stop the
    others. Returns ``{"to_review"}`` (ids)."""
    from core.todo_manager import todo_manager
    done: dict = {"to_review": []}
    where = f"session {session_id}, {day.isoformat()}"
    for action in actions or []:
        if action.get("action") != "done":
            continue
        try:
            note = (f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Reported finished in "
                    f"{where} ({session_title}) by its activity summary; confirm or reject.")
            if await todo_manager.to_review(action["todo_id"], note, source="daily_activity",
                                            suggested=True):
                done["to_review"].append(action["todo_id"])
        except Exception:  # noqa: BLE001 — one action never stops the row or the others
            logger.warning("ToDo action from %s failed", where, exc_info=True)
    if done["to_review"]:
        logger.info("ToDos from %s: %d to review", where, len(done["to_review"]))
    return done


def _merged(previous: "list | None", new: list) -> list:
    """Previous items, then new ones not already there (order kept)."""
    out = list(previous or [])
    out.extend(item for item in new if item not in out)
    return out


async def summarize_session_day(
    pipeline: SummarizationPipeline,
    context: HookContext,
    day_messages: list[dict],
    *,
    day: date,
    workspace: Path,
    open_todos: list[dict[str, str]],
    open_problems: "list[str] | None" = None,
    previous: "dict | None" = None,
    start: int = 0,
    end: "int | None" = None,
):
    """The summary of one session's messages ``day_messages[start:end]`` on one day.

    ``previous``: the day's row (sidecar shape) covering ``day_messages[:start]``; the
    model continues it, so the answer is the record of ``day_messages[:end]``.
    Returns ``(summary, day_context)``: *context* with ``message_count`` set to *end*.
    Without a model answer the sentence is the day's commit subjects (ground truth),
    or empty — never the user's words.
    """
    end = len(day_messages) if end is None else end
    summary = await pipeline.summarize(
        day_messages[start:end],
        session_id=context.session_id,
        open_todos=open_todos,
        open_problems=open_problems if open_problems is not None else _open_problem_titles(workspace),
        earlier=earlier_row(workspace, context.session_id, day),
        day=day.isoformat(),
        so_far=record_of(previous) if previous else None,
    )
    if previous:
        for name in ("topics", "decisions", "files_modified", "open_questions"):
            setattr(summary, name, _merged(previous.get(name), getattr(summary, name)))
    # Git ground truth (COE C005): the session's own commits, inside this day.
    covered = day_messages[:end]
    stamps = [m.get("created_at") for m in covered if m.get("created_at")]
    window_start = min(stamps) if stamps else context.session_start_time
    until = None
    if stamps:
        try:
            until = datetime.fromisoformat(str(max(stamps)))
        except ValueError:
            until = None
    summary.git_commits = await executors.run_in(
        "subprocess", DailyActivityExtractionHook._capture_git_activity, window_start,
        list(summary.files_modified or []), context.session_id,
        _bash_call_windows(covered), until, workspace, commit_subjects(covered),
    )
    if not summary.enriched and not summary.summary_sentence and summary.git_commits:
        summary.summary_sentence = "Commits: " + "; ".join(summary.git_commits)
    return summary, dataclasses.replace(context, message_count=end)


def resume_point(row: "dict | None", day_messages: list[dict]) -> int:
    """How many of the day's messages *row* already covers, when the model wrote it
    (0: summarize the whole day). A count larger than the day means messages went
    away: start again."""
    if not row or not row.get("enriched"):
        return 0
    count = row.get("message_count")
    if isinstance(count, int) and 0 < count <= len(day_messages):
        return count
    return 0


class DailyActivityExtractionHook:
    """Extracts conversation summaries into DailyActivity files.

    Registered as the first post-session-close hook so that
    DailyActivity is written before workspace auto-commit captures it.
    """

    name = "daily_activity_extraction"
    # Hang guard, not a budget: a long day is summarized in several model calls of up
    # to _ENRICHMENT_READ_TIMEOUT_S each (SummarizationPipeline.parts), longer than the
    # 180 s every other hook gets. The row is written after each part, so a cut run
    # keeps its progress and the next idle continues.
    timeout_seconds = 900.0

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

        # 1. Each local day of the last 7 with messages gets its own row, about that
        # day. Looking at every such day, not only today, keeps the tail of a day whose
        # last idle fired after midnight.
        messages = await _read_complete_transcript(context.session_id)
        today = date.today()
        days = [today - timedelta(days=back) for back in range(6, -1, -1)]
        work = [(day, messages_on_day(messages, day)) for day in days]
        work = [(day, msgs) for day, msgs in work if msgs]
        if not work:
            logger.info(
                "No messages in the last 7 days for session %s, skipping extraction",
                context.session_id,
            )
            return

        from core.initialization_manager import initialization_manager
        activity_workspace = Path(initialization_manager.get_cached_workspace_path())
        written = False
        for day, day_messages in work:
            written |= await self._summarize_day(context, day, day_messages, activity_workspace)

        if written:
            self._tracker.record_success(context.session_id)

    async def _summarize_day(self, context: HookContext, day: date,
                             day_messages: list[dict], workspace: Path) -> bool:
        """Bring the session's row of *day* up to date; True when a row was written.

        A complete row is left alone (no model call). Otherwise the messages it does
        not cover are summarized part by part, the row written after each part, so a
        cut run keeps what it did and the next idle continues from there. A part
        without a model answer never replaces a row the model wrote. Each part's ToDo
        actions are applied before its row is written; the next part is shown the open
        ToDos again.
        """
        row = current_row(workspace, context.session_id, day)
        start = resume_point(row, day_messages)
        if start == len(day_messages):
            logger.debug("DailyActivity of %s unchanged for session %s", day, context.session_id)
            return False
        previous = row if start else None
        written = False
        for part in self._pipeline.parts(day_messages[start:]):
            end = start + len(part)
            summary, day_context = await summarize_session_day(
                self._pipeline, context, day_messages, day=day, workspace=workspace,
                open_todos=await open_todos_shown(), previous=previous, start=start, end=end,
            )
            if previous is None and self._is_noise_summary(summary):
                self._record_noise(context)
                return written
            if previous is not None and not summary.enriched:
                return written      # keep the model's row; the next idle tries again
            if summary.enriched:
                # before the row: a row says these messages are done, so actions applied
                # after it would be lost if the write succeeded and this run then stopped;
                # applied first, a retry repeats them harmlessly (to_review moves a ToDo once)
                await apply_todo_actions(
                    summary.todos, session_id=context.session_id, day=day,
                    session_title=summary.session_title or "")
            try:
                path = await write_daily_activity(summary, day_context, day=day)
            except Exception as exc:
                self._tracker.record_failure(context.session_id, str(exc))
                raise  # Re-raise so hook manager logs it
            logger.info("DailyActivity extracted for session %s → %s", context.session_id, path)
            written = True
            if not summary.enriched:
                return written      # model unavailable: the next idle starts again
            previous = current_row(workspace, context.session_id, day)
            start = end
        return written

    @staticmethod
    def _record_noise(context: HookContext) -> None:
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
                artifacts = Path(ws) / "Projects" / DEFAULT_PROJECT_NAME / ".artifacts"
                if artifacts.is_dir():
                    GateManager(artifacts).record_trigger("noise_filter")
        except Exception:
            pass

    @staticmethod
    def _is_noise_summary(summary) -> bool:
        """Check if a summary is pure noise with no insight value.

        Handles StructuredSummary dataclass (has .decisions, .topics, etc).

        Returns True (skip) if:
        - No decisions or deliverables recorded
        - Topics are empty or single-item trivial
        - Files modified is empty (no actual work)

        Returns False (keep) if ANY substantive content exists.
        """
        # A model-written record with a sentence or a ToDo action is kept
        if getattr(summary, "enriched", False) and (
                getattr(summary, "summary_sentence", "") or getattr(summary, "todos", None)):
            return False
        # PE-2 fix: access actual StructuredSummary fields safely
        decisions = getattr(summary, "decisions", None) or []
        deliverables = getattr(summary, "deliverables", None) or []
        topics = getattr(summary, "topics", None) or []
        files_modified = getattr(summary, "files_modified", None) or []
        open_questions = getattr(summary, "open_questions", None) or []

        # If there are decisions or deliverables — always keep
        if decisions or deliverables:
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
                              until: "datetime | None" = None,
                              workspace: "Path | None" = None,
                              subjects: "list[tuple[str, bool]] | None" = None,
                              ) -> list[str]:
        """Capture this session's own git commits from source repos.

        Scans ALL projects' TECH.md for repo paths — no hardcoded paths.
        Attribution (other sessions commit to the same repos): see
        ``_get_session_git_commits``. Commits from every distinct repo, at
        most 15. Empty list on any failure.
        """
        run_shas, run_ids = _session_run_evidence(session_id, workspace)
        session_files = list(session_files or [])
        try:
            since = datetime.fromisoformat(session_start_iso.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            since = datetime.now() - timedelta(hours=2)

        # Discover repos from all projects' TECH.md files
        candidates: list[Path] = []
        try:
            if workspace is not None:
                ws = Path(workspace)
            else:
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
                    session_run_ids=run_ids, until=until, subjects=subjects)
                if len(commits) >= 15:
                    break
        return commits[:15]
