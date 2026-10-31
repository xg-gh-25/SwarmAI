"""One-time repair of DailyActivity history (design
docs/design/2026-10-31-daily-activity-useful.md §4).

Rows written before the fix carry the user's words instead of what was done (the
model's answer was cut at 2048 tokens), repeat a session on every later day, and sit
beside mid-session checkpoint and day-summary blocks. This rebuilds every (session,
local day) that still has its messages in data.db through the hook's own
``summarize_session_day``, days in order, and keeps whatever it cannot rebuild.

Per day file:
- a session with messages on that day gets a new row and block, but only when the
  model wrote it; a failed, unanswered or noise rebuild keeps the old row and block;
- a session whose messages exist but none on that day loses its row there (it was a
  re-summary of another day) only when every day it has messages on is in this run;
  a session with no messages left keeps its row;
- checkpoint blocks and day-summary blocks are dropped; captures and other text stay;
- the frontmatter keys stay (``distilled`` drives core.coe_threads);
- the day's .md and .jsonl are built in memory and written once each, under the day
  lock, after every model call of that day is done.

Days are rebuilt in order and, within a day, sessions in the order they started, in
batches of ``--jobs``. A rebuilt row is history: it is shown no ToDos and its ToDo
actions are not applied (work left open lives in the ToDos, design
2026-10-31-continue-lookback-todo D2). Only sessions that already have a row or block
somewhere are rebuilt. A row that is already complete (written by the model, same
message count) is not asked again, so a second run redoes only what failed.

``--restore-archives`` puts back the archived day files a 90-day prune deleted from
``Knowledge/Archives/`` (their last version in the workspace git history; read-only
git, the files are written, nothing is staged).

Dry run by default. ``--apply`` first copies the whole DailyActivity directory to
``~/.swarm-ai/backups/daily-activity-<ts>/`` and checks every copy by sha256; no
file is written unless that check passes.

Usage (from backend/):
    python -m scripts.da_resummarize                       # plan only
    python -m scripts.da_resummarize --apply --jobs 6
    python -m scripts.da_resummarize --apply --until 2026-10-31
    python -m scripts.da_resummarize --restore-archives [--apply]
    python -m scripts.da_resummarize --commits [--apply]   # commits only, no model
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

_SID_LINE = re.compile(r"^<!-- da:(summary|checkpoint):(.+) -->$")
_LEGACY_HEAD = re.compile(r"^## \d{2}:\d{2} \| ([^|]+?) \|")
_DAY_SUMMARY = ("<!-- daily-summary", "## 日汇总")
_CAPTURE = "**🔔 "


# ── reading data.db (read-only) ───────────────────────────────────────────────

def _connect_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _parse(value):
    """The messages repository's own JSON rule (database.sqlite._row_to_dict)."""
    if isinstance(value, str) and (value.startswith("[") or value.startswith("{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def session_days(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """``{session_id: {day: messages}}`` of every persisted, sent message."""
    out: dict[str, dict[str, int]] = {}
    for sid, day, n in conn.execute(
        "SELECT session_id, substr(created_at, 1, 10), COUNT(*) FROM messages "
        "WHERE sent IS NULL OR sent != 0 GROUP BY 1, 2"
    ):
        out.setdefault(sid, {})[day] = n
    return out


def day_messages(conn: sqlite3.Connection, session_id: str, day: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, metadata, created_at FROM messages "
        "WHERE session_id = ? AND substr(created_at, 1, 10) = ? "
        "AND (sent IS NULL OR sent != 0) ORDER BY created_at, rowid",
        (session_id, day),
    ).fetchall()
    return [{"id": i, "role": r, "content": _parse(c), "metadata": _parse(m), "created_at": at}
            for i, r, c, m, at in rows]


def session_info(conn: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    """``{id: (title, created_at, agent_id)}``."""
    return {sid: (title or "", created or "", agent or "default")
            for sid, title, created, agent in conn.execute(
                "SELECT id, title, created_at, agent_id FROM sessions")}


# ── splitting a day file ──────────────────────────────────────────────────────

@dataclass
class Segment:
    kind: str          # text | summary | checkpoint | legacy | day_summary
    session: str       # full id (marked blocks), id prefix (legacy), "" otherwise
    text: str


def split_day(body: str) -> list[Segment]:
    """The day file body as segments, in order; concatenating them gives the body.

    A start marker without its end marker is text (a hand edit is never removed).
    """
    lines = body.split("\n")
    segments: list[Segment] = []
    text: list[str] = []

    def flush() -> None:
        if text:
            segments.append(Segment("text", "", "\n".join(text)))
            text.clear()

    def is_boundary(line: str) -> bool:
        # a capture (append_daily_text) is free text even right after a block
        return (line.startswith("## ") or _SID_LINE.match(line) is not None
                or line.startswith("<!-- daily-summary") or line.startswith(_CAPTURE))

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        marked = _SID_LINE.match(line)
        if marked:
            end = next((j for j in range(i + 1, n) if lines[j] == "<!-- /da -->"), None)
            if end is not None:
                flush()
                segments.append(Segment(marked.group(1), marked.group(2),
                                        "\n".join(lines[i:end + 1])))
                i = end + 1
                continue
        if line.startswith(_DAY_SUMMARY):
            flush()
            j = i + 1
            if line.startswith("<!-- daily-summary") and j < n and lines[j].startswith("## 日汇总"):
                j += 1
            while j < n and not is_boundary(lines[j]):
                j += 1
            segments.append(Segment("day_summary", "", "\n".join(lines[i:j])))
            i = j
            continue
        legacy = _LEGACY_HEAD.match(line)
        if legacy:
            flush()
            j = i + 1
            while j < n and not is_boundary(lines[j]):
                j += 1
            segments.append(Segment("legacy", legacy.group(1).strip(), "\n".join(lines[i:j])))
            i = j
            continue
        text.append(line)
        i += 1
    flush()
    return segments


def _matches(prefix_or_id: str, sessions) -> str | None:
    """The full session id a block names (legacy blocks carry an 8-char prefix)."""
    if prefix_or_id in sessions:
        return prefix_or_id
    if len(prefix_or_id) >= 8:
        hits = [s for s in sessions if s.startswith(prefix_or_id)]
        if len(hits) == 1:
            return hits[0]
    return None


# ── planning ──────────────────────────────────────────────────────────────────

@dataclass
class Scope:
    """What the run knows about every session: its message days and whether it may
    be rebuilt, plus the run's range."""
    days: dict[str, dict[str, int]]
    eligible: set[str]
    since: str
    until: str

    def first_day(self, sid: str) -> str:
        return min(self.days[sid])

    def all_in_run(self, sid: str) -> bool:
        """Every day *sid* has messages on is inside this run (its real rows are
        rebuilt or complete here), so a row of it elsewhere is a re-summary."""
        return all(self.since <= d <= self.until for d in self.days[sid])


@dataclass
class DayPlan:
    day: str
    rebuild: list[str] = field(default_factory=list)     # sessions to summarize
    complete: list[str] = field(default_factory=list)    # rows already complete
    kept: set[str] = field(default_factory=set)          # rebuild failed / noise (counted)
    drop_rows: list[str] = field(default_factory=list)   # re-summaries of other days
    keep_rows: list[str] = field(default_factory=list)   # rows it cannot rebuild
    dropped_blocks: dict[str, int] = field(default_factory=dict)


def _rows(path: Path) -> list[dict]:
    from core.daily_activity_writer import read_jsonl_sidecar
    return read_jsonl_sidecar(path)


def recorded_sessions(da_dir: Path) -> set[str]:
    """Sessions that have a row or a block on some day (full ids or legacy prefixes)."""
    seen: set[str] = set()
    for path in da_dir.glob("*.jsonl"):
        seen.update(str(r.get("session_id")) for r in _rows(path) if r.get("session_id"))
    for path in da_dir.glob("*.md"):
        for seg in split_day(path.read_text(encoding="utf-8")):
            if seg.session:
                seen.add(seg.session)
    return seen


def make_scope(da_dir: Path, days_by_session: dict[str, dict[str, int]],
               since: str, until: str) -> Scope:
    recorded = recorded_sessions(da_dir)
    eligible = {s for s in days_by_session
                if s in recorded or any(len(r) >= 8 and s.startswith(r) for r in recorded)}
    return Scope(days_by_session, eligible, since, until)


def _is_resummary(full: str, scope: Scope, day: str) -> bool:
    """*full* has no messages to rebuild on *day* (a session rebuilt or kept on *day*
    has them, so its row there is never taken for a re-summary)."""
    return (full in scope.eligible and scope.days[full].get(day, 0) < 2
            and day >= scope.first_day(full) and scope.all_in_run(full))


def drop_row(sid: str, plan: DayPlan, scope: Scope) -> bool:
    if sid in plan.complete:
        return False
    if sid in plan.rebuild:
        return True
    full = _matches(sid, scope.days)
    return full is not None and _is_resummary(full, scope, plan.day)


def drop_segment(seg: Segment, plan: DayPlan, scope: Scope) -> bool:
    if seg.kind in ("checkpoint", "day_summary"):
        return True
    if seg.kind not in ("summary", "legacy"):
        return False
    full = _matches(seg.session, scope.days)
    if full is None or full not in scope.eligible:
        return False
    if full in plan.rebuild:
        return True
    if full in plan.complete:
        return seg.kind == "legacy"   # the complete row's own marked block stays
    return _is_resummary(full, scope, plan.day)


def plan_days(da_dir: Path, scope: Scope) -> list[DayPlan]:
    from core.daily_activity_writer import parse_frontmatter

    all_days = {d for s in scope.eligible for d in scope.days[s]
                if scope.since <= d <= scope.until}
    file_days = {p.stem for p in da_dir.glob("20*.md") if scope.since <= p.stem <= scope.until}
    plans = []
    for day in sorted(all_days | file_days):
        plan = DayPlan(day)
        sidecar = {str(r.get("session_id")): r for r in _rows(da_dir / f"{day}.jsonl")
                   if r.get("session_id")}
        for sid in sorted(scope.eligible):
            n = scope.days[sid].get(day, 0)
            if n < 2:
                continue
            row = sidecar.get(sid)
            if row is not None and row.get("enriched") and row.get("message_count") == n:
                plan.complete.append(sid)
            else:
                plan.rebuild.append(sid)
        for sid in sidecar:
            if sid in plan.rebuild or sid in plan.complete:
                continue
            (plan.drop_rows if drop_row(sid, plan, scope) else plan.keep_rows).append(sid)
        md = da_dir / f"{day}.md"
        if md.exists():
            _, body = parse_frontmatter(md.read_text(encoding="utf-8"))
            for seg in split_day(body):
                if drop_segment(seg, plan, scope):
                    plan.dropped_blocks[seg.kind] = plan.dropped_blocks.get(seg.kind, 0) + 1
        if plan.rebuild or plan.drop_rows or plan.dropped_blocks:
            plans.append(plan)
    return plans


# ── backup ────────────────────────────────────────────────────────────────────

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(da_dir: Path, root: Path) -> Path:
    """Copy every file of *da_dir* to a new directory under *root*; verify each copy."""
    dest = root / f"daily-activity-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    dest.mkdir(parents=True, exist_ok=False)
    sources = sorted(p for p in da_dir.iterdir() if p.is_file())
    for src in sources:
        shutil.copy2(src, dest / src.name)
    bad = [p.name for p in sources if _sha(p) != _sha(dest / p.name)]
    if bad or len(list(dest.iterdir())) != len(sources):
        raise SystemExit(f"backup check failed ({len(bad)} files differ) — nothing written: {dest}")
    return dest


# ── rebuilding ────────────────────────────────────────────────────────────────

async def _rebuild_one(pipeline, conn, info, ws: Path, day: date, sid: str,
                       problems: list[str]):
    """The new summary of *sid* on *day*, part by part in memory, or None when the
    model did not write all of it (the old row stays)."""
    from core.daily_activity_writer import _summary_to_jsonl_record
    from core.session_hooks import HookContext
    from hooks.daily_activity_hook import DailyActivityExtractionHook, summarize_session_day

    title, created, agent = info.get(sid, ("", "", "default"))
    msgs = day_messages(conn, sid, day.isoformat())
    ctx = HookContext(session_id=sid, agent_id=agent, message_count=len(msgs),
                      session_start_time=created, session_title=title)
    previous, start, summary, day_ctx = None, 0, None, None
    for part in pipeline.parts(msgs):
        end = start + len(part)
        for _attempt in range(2):     # one retry of a failed model call
            summary, day_ctx = await summarize_session_day(
                pipeline, ctx, msgs, day=day, workspace=ws, open_todos=[],
                open_problems=problems, previous=previous, start=start, end=end)
            if summary.enriched:
                break
        if not summary.enriched:
            return None
        previous, start = _summary_to_jsonl_record(summary, day_ctx), end
    if summary is None or DailyActivityExtractionHook._is_noise_summary(summary):
        return None
    return summary, day_ctx


async def rebuild_day(plan: DayPlan, conn, info, ws: Path, scope: Scope, jobs: int) -> dict:
    """Summarize the plan's sessions (in start order, in batches), then write the day
    once. Returns counts."""
    from core.daily_activity_writer import (
        _atomic_write, _day_lock, parse_frontmatter, render_session, write_frontmatter,
    )
    from core.summarization import SummarizationPipeline
    from hooks.daily_activity_hook import _open_problem_titles

    day = date.fromisoformat(plan.day)
    da_dir = ws / "Knowledge" / "DailyActivity"
    problems = _open_problem_titles(ws)
    pipeline = SummarizationPipeline()
    started = {sid: (day_messages(conn, sid, plan.day) or [{}])[0].get("created_at", "")
               for sid in plan.rebuild}
    order = sorted(plan.rebuild, key=lambda sid: started[sid])
    new: dict[str, tuple] = {}
    errors = 0
    for k in range(0, len(order), max(jobs, 1)):
        batch = order[k:k + max(jobs, 1)]
        results = await asyncio.gather(
            *(_rebuild_one(pipeline, conn, info, ws, day, sid, problems)
              for sid in batch), return_exceptions=True)
        for sid, result in zip(batch, results):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                print(f"    {plan.day} {sid}: {type(result).__name__}: {result}")
                errors += 1
                plan.kept.add(sid)
            elif result is None:
                plan.kept.add(sid)
            else:
                summary, ctx = result
                block, record = render_session(summary, ctx)
                new[sid] = (summary, block, record)
    plan.rebuild = [sid for sid in plan.rebuild if sid in new]

    md, jsonl = da_dir / f"{plan.day}.md", da_dir / f"{plan.day}.jsonl"
    with _day_lock(md):
        fm, body = (parse_frontmatter(md.read_text(encoding="utf-8")) if md.exists()
                    else ({}, ""))
        kept = [seg.text for seg in split_day(body) if not drop_segment(seg, plan, scope)]
        blocks = [new[sid][1] for sid in sorted(new, key=lambda s: new[s][0].timestamp)]
        text = "\n".join(kept).strip("\n")
        text = re.sub(r"\n{3,}", "\n\n", "\n\n".join(p for p in [text, *blocks] if p)) + "\n"
        lines: list[str] = []
        if jsonl.exists():
            for raw in jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    sid = str(json.loads(raw).get("session_id"))
                except (ValueError, AttributeError):
                    lines.append(raw)
                    continue
                if drop_row(sid, plan, scope):
                    continue
                lines.append(raw)
        lines += [json.dumps(new[sid][2], ensure_ascii=False, separators=(",", ":"))
                  for sid in sorted(new, key=lambda s: new[s][0].timestamp)]
        fm = dict(fm or {})
        fm["date"] = plan.day
        fm.setdefault("distilled", False)
        fm["sessions_count"] = len({json.loads(l).get("session_id") for l in lines
                                    if l.strip() and l.lstrip().startswith("{")})
        if any(new[sid][0].coe_signal for sid in new):
            fm["has_coe"] = True
        # md first: an interruption between the two leaves the rebuilt rows not yet
        # complete in the sidecar, so the next run rebuilds them again
        _atomic_write(md, write_frontmatter(fm, text))
        _atomic_write(jsonl, "\n".join(lines) + ("\n" if lines else ""))
    return {"written": len(new), "kept": len(plan.kept), "errors": errors}


async def repair(args) -> int:
    from core import executors

    ws = Path(args.workspace).expanduser()
    da_dir = ws / "Knowledge" / "DailyActivity"
    conn = _connect_ro(Path(args.db).expanduser())
    days_by_session = session_days(conn)
    since = args.since or min((min(d) for d in days_by_session.values()), default="9999")
    until = args.until or (date.today() - timedelta(days=1)).isoformat()
    scope = make_scope(da_dir, days_by_session, since, until)
    plans = plan_days(da_dir, scope)

    calls = sum(len(p.rebuild) for p in plans)
    print(f"days {since}..{until}: {len(plans)} day files to change, {calls} session-days "
          f"to summarize; {len(days_by_session) - len(scope.eligible)} sessions with "
          "messages but no row anywhere are left alone")
    for p in plans:
        print(f"  {p.day}: rebuild {len(p.rebuild)}, complete {len(p.complete)}, "
              f"drop rows {len(p.drop_rows)}, keep rows {len(p.keep_rows)}, "
              f"drop blocks {p.dropped_blocks or '{}'}")
    if not args.apply:
        print("dry run: nothing written (add --apply)")
        return 0
    if not plans:
        print("nothing to change")
        return 0

    dest = backup(da_dir, Path(args.backup_root).expanduser())
    print(f"backup verified: {dest}")
    # this one-off process may use more parallel model calls than the daemon's pool
    executors._POOL_SPECS["llm"] = max(args.jobs, 1)
    info = session_info(conn)
    kept = 0
    for plan in plans:
        try:
            counts = await rebuild_day(plan, conn, info, ws, scope, args.jobs)
        except BaseException:
            print(f"stopped at {plan.day}; every file is in the backup: {dest}")
            raise
        kept += counts["kept"]
        print(f"  {plan.day}: written {counts['written']}, kept {counts['kept']} "
              f"({counts['errors']} errors)")
    print(f"done; {kept} session-days kept their old row (run again to retry them)")
    return 0


# ── commits only ──────────────────────────────────────────────────────────────

_SUMMARY_FIELDS = ("topics", "decisions", "files_modified", "open_questions", "deliverables",
                   "key_outputs", "validation_status", "coe_signal", "coe_topic",
                   "summary_sentence", "git_commits")


def summary_of(row: dict):
    """The ``StructuredSummary`` a sidecar row was written from."""
    from core.summarization import StructuredSummary
    values = {name: row.get(name) for name in _SUMMARY_FIELDS if row.get(name) is not None}
    return StructuredSummary(**values, session_title=str(row.get("title") or ""),
                             timestamp=str(row.get("timestamp") or ""),
                             enriched=bool(row.get("enriched")))


def _rewrite_rows(da_dir: Path, plans: dict, update) -> None:
    """Re-render the rows of *plans* ({day: {session: (row, value)}}) after
    ``update(summary, value)``, block and sidecar row together, under the day lock;
    every other row and block is kept byte for byte. The caller backed up first."""
    from core.daily_activity_writer import (
        _atomic_write, _day_lock, parse_frontmatter, render_session, write_frontmatter,
    )
    from core.session_hooks import HookContext
    for day, rows in sorted(plans.items()):
        md, jsonl = da_dir / f"{day}.md", da_dir / f"{day}.jsonl"
        rendered = {}
        for sid, (row, value) in rows.items():
            summary = summary_of(row)
            update(summary, value)
            ctx = HookContext(session_id=sid, agent_id="default",
                              message_count=int(row.get("message_count") or 0),
                              session_start_time=str(row.get("session_start") or ""),
                              session_title=str(row.get("title") or ""))
            block, record = render_session(summary, ctx)
            record["enriched"] = bool(row.get("enriched"))
            rendered[sid] = (block, record)
        with _day_lock(md):
            fm, body = parse_frontmatter(md.read_text(encoding="utf-8")) if md.exists() else ({}, "")
            parts = []
            for seg in split_day(body):
                if seg.kind == "summary" and seg.session in rendered:
                    parts.append(rendered[seg.session][0])
                else:
                    parts.append(seg.text)
            lines = []
            for raw in jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    sid = str(json.loads(raw).get("session_id"))
                except (ValueError, AttributeError):
                    lines.append(raw)
                    continue
                lines.append(json.dumps(rendered[sid][1], ensure_ascii=False, separators=(",", ":"))
                             if sid in rendered else raw)
            _atomic_write(md, write_frontmatter(dict(fm or {}), "\n".join(parts)))
            _atomic_write(jsonl, "\n".join(lines) + "\n")
        print(f"  {day}: {len(rows)} rows")


async def shorten_sentences(args) -> int:
    """Write again, within the limit, every model-written summary sentence that is over
    it (core.summarization.sentence_length): one short model call per row, from the
    sentence and its deliverables (no transcript). A rewrite still over the limit is
    not used; nothing is cut."""
    from core import summarization as S
    ws = Path(args.workspace).expanduser()
    da_dir = ws / "Knowledge" / "DailyActivity"
    until = args.until or date.today().isoformat()
    since = args.since or (date.fromisoformat(until) - timedelta(days=6)).isoformat()
    todo: list[tuple[str, str, dict, int, int]] = []
    for jsonl in sorted(da_dir.glob("20*.jsonl")):
        day = jsonl.stem
        if not since <= day <= until:
            continue
        for row in _rows(jsonl):
            sentence = str(row.get("summary_sentence") or "")
            length, limit = S.sentence_length(sentence)
            if row.get("enriched") and row.get("session_id") and length > limit:
                todo.append((day, str(row["session_id"]), row, length, limit))
    print(f"days {since}..{until}: {len(todo)} sentences over the limit")
    if not args.apply or not todo:
        print("dry run: nothing written (add --apply)" if not args.apply else "nothing to change")
        return 0
    sem = asyncio.Semaphore(max(1, args.jobs))

    async def one(item):
        day, sid, row, length, limit = item
        async with sem:
            new = await asyncio.to_thread(S.SummarizationPipeline._shorten_sentence,
                                          str(row["summary_sentence"]),
                                          list(row.get("deliverables") or []), length, limit)
        return item, new
    plans: dict = {}
    kept = 0
    for (day, sid, row, length, limit), new in await asyncio.gather(*(one(i) for i in todo)):
        if new and S.sentence_length(new)[0] <= limit:
            plans.setdefault(day, {})[sid] = (row, new)
        else:
            kept += 1
    print(f"rewritten within the limit: {sum(len(v) for v in plans.values())}; kept as they are: {kept}")
    if not plans:
        return 0
    dest = backup(da_dir, Path(args.backup_root).expanduser())
    print(f"backup verified: {dest}")
    _rewrite_rows(da_dir, plans, lambda summary, sentence: setattr(summary, "summary_sentence", sentence))
    return 0


async def recount_commits(args) -> int:
    """Recompute every row's commits by the current attribution (no model call) and
    re-render the day's block and row when they changed (design
    2026-10-31-extraction-at-task-end E7)."""
    from hooks.daily_activity_hook import (
        DailyActivityExtractionHook, _bash_call_windows, commit_subjects,
    )

    ws = Path(args.workspace).expanduser()
    da_dir = ws / "Knowledge" / "DailyActivity"
    conn = _connect_ro(Path(args.db).expanduser())
    days_by_session = session_days(conn)
    since = args.since or min((min(d) for d in days_by_session.values()), default="9999")
    until = args.until or date.today().isoformat()
    plans: dict[str, dict[str, tuple]] = {}
    for jsonl in sorted(da_dir.glob("20*.jsonl")):
        day = jsonl.stem
        if not since <= day <= until:
            continue
        for row in _rows(jsonl):
            sid = str(row.get("session_id") or "")
            if days_by_session.get(sid, {}).get(day, 0) < 1:
                continue
            msgs = day_messages(conn, sid, day)
            stamps = [m["created_at"] for m in msgs if m.get("created_at")]
            if not stamps:
                continue
            commits = await asyncio.to_thread(
                DailyActivityExtractionHook._capture_git_activity, min(stamps),
                list(row.get("files_modified") or []), sid, _bash_call_windows(msgs),
                datetime.fromisoformat(max(stamps)), ws, commit_subjects(msgs))
            # a recount fills in what the old attribution missed; it never removes a
            # commit a row already lists (the repo list and the cap have changed since,
            # neither is evidence that the commit was not the session's)
            before = list(row.get("git_commits") or [])
            listed = {c.split(" ", 1)[0] for c in before}
            commits = before + [c for c in commits if c.split(" ", 1)[0] not in listed]
            if commits != before:
                plans.setdefault(day, {})[sid] = (row, commits)
    changed = sum(len(v) for v in plans.values())
    for day, rows in sorted(plans.items()):
        for sid, (row, commits) in rows.items():
            before = list(row.get("git_commits") or [])
            print(f"  {day} {sid[:12]} {len(before)} -> {len(commits)}")
    print(f"days {since}..{until}: {changed} rows whose commits change in {len(plans)} day files")
    if not args.apply or not plans:
        print("dry run: nothing written (add --apply)" if not args.apply else "nothing to change")
        return 0
    dest = backup(da_dir, Path(args.backup_root).expanduser())
    print(f"backup verified: {dest}")
    _rewrite_rows(da_dir, plans, lambda summary, commits: setattr(summary, "git_commits", commits))
    return 0


# ── archives ──────────────────────────────────────────────────────────────────

def restore_archives(ws: Path, apply: bool) -> int:
    """Put back archived day files deleted from Knowledge/Archives (last git version)."""
    log = subprocess.run(
        ["git", "log", "--diff-filter=D", "--name-only", "--format=%H", "--",
         "Knowledge/Archives/20*.md"],
        cwd=ws, capture_output=True, text=True, timeout=60, check=True).stdout
    deleted: dict[str, str] = {}
    commit = ""
    for line in log.splitlines():
        if re.fullmatch(r"[0-9a-f]{40}", line):
            commit = line
        elif line.startswith("Knowledge/Archives/") and line not in deleted:
            deleted[line] = commit            # newest deletion first
    todo = {p: c for p, c in deleted.items() if not (ws / p).exists()
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", Path(p).stem)}
    print(f"{len(todo)} archived day files to restore")
    if not apply:
        print("dry run: nothing written (add --apply)")
        return 0
    for path, commit in sorted(todo.items()):
        text = subprocess.run(["git", "show", f"{commit}^:{path}"], cwd=ws,
                              capture_output=True, timeout=30, check=True).stdout
        target = ws / path
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".md.restore")
        tmp.write_bytes(text)
        tmp.replace(target)
    print(f"restored {len(todo)} files under Knowledge/Archives/")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", default="~/.swarm-ai/SwarmWS")
    parser.add_argument("--db", default="~/.swarm-ai/data.db")
    parser.add_argument("--backup-root", default="~/.swarm-ai/backups")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-archives", action="store_true")
    parser.add_argument("--sentences", action="store_true",
                        help="write again every summary sentence over its limit (one short model call each)")
    parser.add_argument("--commits", action="store_true",
                        help="only recompute each row's commits (no model call)")
    args = parser.parse_args(argv)
    if args.sentences:
        return asyncio.run(shorten_sentences(args))
    if args.commits:
        return asyncio.run(recount_commits(args))
    if args.restore_archives:
        return restore_archives(Path(args.workspace).expanduser(), args.apply)
    return asyncio.run(repair(args))


if __name__ == "__main__":
    sys.exit(main())
