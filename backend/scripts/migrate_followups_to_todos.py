"""One-time move of open follow-ups into ToDos (design
docs/design/2026-10-31-continue-lookback-todo.md §3).

Before the design, work left for later was written in four places: the DailyActivity
rows' Next lines, CDF ``follow_up`` gaps in a project's IMPROVEMENT, the
``follow_up`` line of a TIMELINE event, and ToDos. ToDo is now the only one. This
script decides, for every follow-up still open, whether it is done, a release step,
the same as an open ToDo, or still open, and makes each still-open one a ToDo.

Scope:
- **DailyActivity**: the follow-ups open in the 7 newest day files (a follow-up is open
  when it is in its session's latest row of those days and no row resolved it). The
  older unresolved ones fell out of that list long ago: each is recorded as
  ``expired`` (not migrated) with its id.
- **Gaps**: every project's IMPROVEMENT entries of gap kind ``follow_up``. A still-open
  one becomes a ToDo and leaves the document (withdrawn ``done``, reason "moved to
  ToDo <id>"; the vault keeps it); done or release: withdrawn ``closed``.
- **Events**: the ``- follow_up:`` lines of ``.context/TIMELINE.md`` (left in place:
  an event is history).

Per item, one model call with evidence it can cite: the later DailyActivity rows (id
``r<n>`` + text), the commit subjects since the item's day in the repositories the
window's sessions wrote to (the workspace's own auto-commits excluded), and the open
ToDos. Answers: ``done`` (must cite a row id or a commit sha from the evidence),
``release`` (a push / build / restart / deploy step), ``same`` (an open ToDo's id),
``open``. No answer, a failure, an uncited done or an unknown ToDo id: ``open``.
ToDos are created through the daemon's API (``POST /api/todos``), so the daemon stays
the only writer of its database; batches see the ToDos the earlier ones created.

Every decision is one line of the record (JSONL) keyed by the item (``da:<next_id>``,
``gap:<anchor>``, ``event:<hash>``): a second run skips what is recorded, so it changes
nothing. ``--max-items`` bounds one run (each run continues where the last stopped).

After the items: the window's rows are rendered again without follow-ups (no Next
lines in the md, no next_items / resolves in the sidecar), through da_resummarize's
writer. Existing ToDos are not changed.

Dry run by default (counts only, no model call). ``--apply`` first backs up the
DailyActivity directory (each copy checked by sha256), the IMPROVEMENT files and
TIMELINE (a tar.gz, checked), and the ToDo list (JSON from the API).

Usage (from backend/):
    python -m scripts.migrate_followups_to_todos                 # scope only
    python -m scripts.migrate_followups_to_todos --apply --max-items 40 --jobs 6
    python -m scripts.migrate_followups_to_todos --apply --rerender-only
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

WINDOW_DAYS = 7
_EVENT_HEAD = re.compile(r"^### (\d{4}-\d{2}-\d{2})\s+(.*)$")
_EVENT_FOLLOW = re.compile(r"^- follow_up:\s*(.+)$")
_GAP_FOLLOW = "gap kind: follow_up"

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["done", "release", "same", "open"]},
        "cite": {"type": "string"},
        "todo_id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["decision", "cite", "todo_id", "title", "description"],
}

PROMPT = """\
A piece of work was left open earlier. Decide its state now, from the evidence below only.

## The open item ({source}, {day})
{text}
{context}
## Later activity (one row per session and day; cite a row by its id)
{rows}

## Commits since {day} (cite one by its sha)
{commits}

## Open ToDos (id: title)
{todos}

Answer with the decide tool:
- decision "done": the evidence shows this work was finished. cite: the row id or the
  commit sha that shows it. A similar topic is not enough; a plan is not done.
- decision "release": it is only a release step (push, build, restart, deploy, watch CI).
- decision "same": it is the same work as an open ToDo above. todo_id: that ToDo's id.
- decision "open": otherwise. title: the work as one complete action. description: what
  is to be done, why, and where it stood, so someone can pick it up without any other
  context. Write them in the language of the item.
Use an empty string for a field that does not apply.
"""


@dataclass
class Item:
    key: str
    source: str                 # da | gap | event
    text: str
    day: str = ""
    session_id: str = ""
    context: str = ""
    after: tuple = ("", "")     # (day, timestamp) the evidence rows must come after
    anchor: str = ""            # a gap's unit anchor
    files: list = field(default_factory=list)


# ── reading ───────────────────────────────────────────────────────────────────

def _rows(path: Path) -> list[dict]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def window_files(da_dir: Path, today: date) -> list[Path]:
    files = sorted((p for p in da_dir.glob("20*.jsonl") if p.stem[:10] <= today.isoformat()),
                   reverse=True)
    return sorted(files[:WINDOW_DAYS])


def window_rows(da_dir: Path, today: date) -> list[tuple[str, dict]]:
    """``(day, row)`` of the window, oldest first."""
    return [(p.stem[:10], row) for p in window_files(da_dir, today) for row in _rows(p)]


def da_items(da_dir: Path, today: date) -> tuple[list[Item], list[dict]]:
    """The open follow-ups of the window, and a record row for every other follow-up.

    Open: in its session's latest row of the window (a newer row carried forward what
    was still open, so it replaces the older list; a row the model did not write never
    does), and no row of the window resolved it. Every other id of the window is
    ``closed`` (resolved, or not carried forward); an unresolved id found only before
    the window is ``expired``; a resolved one is ``closed`` too. All are recorded on the
    first run: once the window is rendered without follow-ups (and without the rows'
    resolves), its ids can no longer be told apart from older ones."""
    records = window_rows(da_dir, today)
    resolved = {i for _d, r in records for i in r.get("resolves") or [] if isinstance(i, str)}
    latest: dict[str, tuple[str, dict]] = {}
    for n, (day, row) in enumerate(records):
        sid = str(row.get("session_id") or f"\0{n}")
        if row.get("enriched") is not False or sid not in latest:
            latest[sid] = (day, row)
    items: dict[str, Item] = {}
    for day, row in latest.values():
        for it in row.get("next_items") or []:
            if not isinstance(it, dict):
                continue
            nid, text = it.get("next_id"), it.get("text")
            if not isinstance(nid, str) or not isinstance(text, str) or nid in resolved:
                continue
            items.setdefault(nid, Item(
                key=f"da:{nid}", source="da", text=text, day=day,
                session_id=str(row.get("session_id") or ""),
                context=f"Recorded by the session \"{row.get('title') or ''}\": "
                        f"{row.get('summary_sentence') or ''}",
                after=(day, str(row.get("timestamp") or "")),
                files=list(row.get("files_modified") or [])))
    open_ids = set(items)
    every_resolved = set(resolved)
    older: dict[str, dict] = {}
    for path in sorted(da_dir.glob("20*.jsonl")):
        for row in _rows(path):
            every_resolved |= {i for i in row.get("resolves") or [] if isinstance(i, str)}
            for it in row.get("next_items") or []:
                if isinstance(it, dict) and isinstance(it.get("next_id"), str):
                    older.setdefault(it["next_id"], {"day": path.stem[:10],
                                                     "text": str(it.get("text") or "")})
    window_ids = {it.get("next_id") for _d, r in records for it in r.get("next_items") or []
                  if isinstance(it, dict)}
    # every id ever written gets one record line: open (decided), closed (resolved by
    # some row, or not carried forward in the window) or expired (neither)
    rest = []
    for nid, v in older.items():
        if nid in open_ids:
            continue
        gone = nid in every_resolved or nid in window_ids
        rest.append({"key": f"da:{nid}", "source": "da",
                     "decision": "closed" if gone else "expired", **v})
    return list(items.values()), rest


def gap_items(workspace: Path) -> list[Item]:
    """Every IMPROVEMENT entry of gap kind follow_up, in every project."""
    from core.ingestion.gaps import main_cognition_files
    from core.ingestion.units import parse_units
    out = []
    for path in main_cognition_files(workspace):
        if path.name != "IMPROVEMENT.md":
            continue
        for unit in parse_units(path, root=workspace):
            if _GAP_FOLLOW in unit.text:
                out.append(Item(key=f"gap:{unit.anchor}", source="gap", text=unit.text.strip(),
                                context=f"A follow-up gap in {path.relative_to(workspace)}.",
                                anchor=unit.anchor))
    return out


def event_items(workspace: Path) -> list[Item]:
    """The follow_up line of every TIMELINE event."""
    path = workspace / ".context" / "TIMELINE.md"
    if not path.is_file():
        return []
    out, head = [], ("", "")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _EVENT_HEAD.match(line)
        if m:
            head = (m.group(1), m.group(2).strip())
            continue
        f = _EVENT_FOLLOW.match(line)
        if f and head[0]:
            text = f.group(1).strip()
            key = hashlib.sha256(f"{head[1]}\n{text}".encode()).hexdigest()[:12]
            out.append(Item(key=f"event:{key}", source="event", text=text, day=head[0],
                            context=f"The follow-up of the event \"{head[1]}\".",
                            after=(head[0], "")))
    return out


# ── evidence ──────────────────────────────────────────────────────────────────

def row_text(row: dict) -> str:
    parts = [f"{row.get('title') or ''}: {row.get('summary_sentence') or ''}"]
    if row.get("deliverables"):
        parts.append("delivered: " + "; ".join(map(str, row["deliverables"])))
    if row.get("validation_status"):
        parts.append("verified: " + str(row["validation_status"]))
    if row.get("git_commits"):
        parts.append("commits: " + "; ".join(map(str, row["git_commits"])))
    return " | ".join(" ".join(p.split()) for p in parts)


def evidence_rows(records: list[tuple[str, dict]]) -> list[tuple[str, str, str, str]]:
    """``(id, day, timestamp, text)`` of every window row."""
    return [(f"r{n}", day, str(row.get("timestamp") or ""), row_text(row))
            for n, (day, row) in enumerate(records, 1)]


def _git(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def repositories(records: list[tuple[str, dict]], workspace: Path) -> list[Path]:
    """The main checkouts of the repositories the window's sessions wrote files in
    (a worktree counts as its main repository), the workspace itself excluded."""
    found: set[Path] = set()
    ws = workspace.resolve()
    tried: set[Path] = set()
    for _day, row in records:
        for f in row.get("files_modified") or []:
            p = Path(str(f)).expanduser()
            if not p.is_absolute():
                continue
            d = p.parent
            while not d.exists() and d != d.parent:
                d = d.parent
            if d in tried:
                continue
            tried.add(d)
            common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], d).strip()
            if not common:
                continue
            root = Path(common).parent.resolve()
            if root != ws:
                found.add(root)
    return sorted(found)


def commits_since(repos: list[Path], since: str) -> list[tuple[str, str, str]]:
    """``(sha, day, subject)`` of every commit on each repository's checked-out branch
    since *since*."""
    out = []
    for repo in repos:
        log = _git(["log", f"--since={since}T00:00:00", "--format=%h%x09%ad%x09%s",
                    "--date=short"], repo)
        for line in log.splitlines():
            sha, _, rest = line.partition("\t")
            day, _, subject = rest.partition("\t")
            if sha:
                out.append((sha, day, subject))
    return out


# ── the ToDo API ─────────────────────────────────────────────────────────────

class TodoApi:
    """The daemon's ToDo endpoints (the daemon is the only writer of its database)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def all(self) -> list[dict]:
        out = []
        for status in ("pending", "overdue", "in_discussion", "handled", "cancelled"):
            out += self._call("GET", f"/api/todos?status={status}&limit=1000")
        return out

    def open(self) -> list[dict]:
        return [t for t in self.all() if t.get("status") in ("pending", "overdue", "in_discussion")]

    def create(self, title: str, description: str, linked: dict) -> dict:
        return self._call("POST", "/api/todos", {
            "workspace_id": "", "title": title[:500], "description": description[:10000],
            "source": None, "source_type": "ai_detected",
            "linked_context": json.dumps(linked, ensure_ascii=False)})


# ── deciding ─────────────────────────────────────────────────────────────────

def prompt_for(item: Item, rows, commits, todos) -> str:
    later = [r for r in rows if (r[1], r[2]) > item.after]
    since = item.day or (rows[0][1] if rows else "")
    shown_commits = [c for c in commits if c[1] >= since]
    return PROMPT.format(
        source={"da": "a DailyActivity follow-up", "gap": "a project gap",
                "event": "a TIMELINE event follow-up"}[item.source],
        day=item.day or "unknown day", text=item.text,
        context=(item.context + "\n") if item.context else "",
        rows="\n".join(f"[{i}] {d} {t}" for i, d, _ts, t in later) or "(none)",
        commits="\n".join(f"{s} {d} {subj}" for s, d, subj in shown_commits) or "(none)",
        todos="\n".join(f"{t['id']}: {' '.join(str(t.get('title') or '').split())}"
                        for t in todos) or "(none)")


def checked(answer: dict, item: Item, rows, commits, todos) -> dict:
    """The decision that stands: a done must cite evidence that was shown, a same must
    name a shown ToDo; anything else is open."""
    decision = answer.get("decision") if isinstance(answer, dict) else None
    later = {r[0] for r in rows if (r[1], r[2]) > item.after}
    shas = {c[0] for c in commits if c[1] >= (item.day or "")}
    cite = str((answer or {}).get("cite") or "").strip()
    tid = str((answer or {}).get("todo_id") or "").strip()
    if decision == "done" and (cite in later or cite in shas):
        return {"decision": "done", "cite": cite}
    if decision == "release":
        return {"decision": "release"}
    if decision == "same" and tid in {t["id"] for t in todos}:
        return {"decision": "same", "todo_id": tid}
    title = " ".join(str((answer or {}).get("title") or "").split()) or " ".join(item.text.split())
    description = str((answer or {}).get("description") or "").strip() or item.text
    note = "" if decision in ("open", None) else f" (model said {decision}"
    if note:
        note += f", cite {cite!r} not in the evidence)" if decision == "done" else ")"
    return {"decision": "open", "title": title, "description": description, "note": note.strip()}


def _default_llm(prompt: str) -> dict:
    from core.summarization import SummarizationPipeline
    return SummarizationPipeline._call_enrichment_llm(
        prompt, schema=DECISION_SCHEMA, name="decide",
        description="Record the state of the open item.")


# ── applying ─────────────────────────────────────────────────────────────────

def recorded(record: Path) -> set[str]:
    if not record.exists():
        return set()
    return {row.get("key") for row in _rows(record)}


def _append(record: Path, row: dict) -> None:
    record.parent.mkdir(parents=True, exist_ok=True)
    with record.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**row, "at": datetime.now(timezone.utc).isoformat()},
                            ensure_ascii=False) + "\n")


def apply_decision(item: Item, decision: dict, api, withdraw, record: Path) -> dict:
    row = {"key": item.key, "source": item.source, "day": item.day, "text": item.text,
           **decision}
    if decision["decision"] == "open":
        linked = {"type": "migration", "migrated_from": item.key, "session_id": item.session_id,
                  "day": item.day, "detection_reason": f"open {item.source} follow-up, migrated",
                  "files": item.files[:20], "next_step": decision["title"]}
        origin = f"Moved from {item.key} ({item.day or 'no day'})."
        todo = api.create(decision["title"], f"{decision['description']}\n\n{origin}", linked)
        row["created_todo_id"] = todo["id"]
    if item.source == "gap":
        # the ToDo exists now: a withdrawal that fails (the file changed meanwhile) is
        # recorded, not retried, so a second run never makes a second ToDo; the gap
        # stays in its document and the record says so
        try:
            if decision["decision"] in ("open", "same"):
                target = row.get("created_todo_id") or decision.get("todo_id")
                withdraw(item.anchor, f"moved to ToDo {target}", status="done")
            else:
                withdraw(item.anchor, f"follow-up {decision['decision']}: "
                                      f"{decision.get('cite') or 'release step'}", status="closed")
            row["withdrawn"] = True
        except Exception as exc:  # noqa: BLE001 — recorded with its reason, the run goes on
            row["withdrawn"] = False
            row["withdraw_error"] = f"{type(exc).__name__}: {exc}"
    _append(record, row)
    return row


def _withdraw(anchor: str, reason: str, *, status: str) -> None:
    from core.ingestion.governed_edit import withdraw
    withdraw(anchor, reason[:500], actor="cdf:migration", status=status)


# ── re-render ────────────────────────────────────────────────────────────────

def rerender_window(da_dir: Path, today: date) -> int:
    """Render the window's rows again without follow-ups (md block and sidecar row)."""
    from scripts import da_resummarize as R
    plans: dict[str, dict] = {}
    for path in window_files(da_dir, today):
        md = da_dir / f"{path.stem}.md"
        md_has_next = md.exists() and "**Next:**" in md.read_text(encoding="utf-8")
        for row in _rows(path):
            sid = row.get("session_id")
            if sid and ("next_items" in row or "resolves" in row or md_has_next):
                plans.setdefault(path.stem, {})[str(sid)] = (row, None)
    if plans:
        R._rewrite_rows(da_dir, plans, lambda summary, _v: None)
    return sum(len(v) for v in plans.values())


# ── backups ──────────────────────────────────────────────────────────────────

def _tar_backup(files: list[Path], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f))
    want = {str(f).lstrip("/"): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    with tarfile.open(dest, "r:gz") as tar:
        got = {m.name.lstrip("/"): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
               for m in tar.getmembers() if m.isfile()}
    if want != got:
        raise SystemExit(f"backup {dest} does not hold the files; nothing changed")
    return dest


def backups(workspace: Path, root: Path, api) -> list[Path]:
    from core.ingestion.gaps import main_cognition_files
    from scripts import da_resummarize as R
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = [R.backup(workspace / "Knowledge" / "DailyActivity", root)]
    files = [p for p in main_cognition_files(workspace) if p.name == "IMPROVEMENT.md"]
    timeline = workspace / ".context" / "TIMELINE.md"
    if timeline.is_file():
        files.append(timeline)
    out.append(_tar_backup(files, root / f"followup-migration-docs-{stamp}.tar.gz"))
    todos = root / f"followup-migration-todos-{stamp}.json"
    data = api.all()
    todos.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if len(json.loads(todos.read_text(encoding="utf-8"))) != len(data):
        raise SystemExit(f"ToDo backup {todos} does not read back; nothing changed")
    out.append(todos)
    return out


# ── run ──────────────────────────────────────────────────────────────────────

async def run(workspace: Path, *, record: Path, api, apply: bool, max_items: int, jobs: int,
              today: date | None = None, llm=None, withdraw=None, backup_root: Path | None = None,
              rerender_only: bool = False) -> dict:
    today = today or date.today()
    llm = llm or _default_llm
    withdraw = withdraw or _withdraw
    da_dir = workspace / "Knowledge" / "DailyActivity"
    done_keys = recorded(record)
    items, expired = da_items(da_dir, today)
    items += gap_items(workspace) + event_items(workspace)
    todo_items = [i for i in items if i.key not in done_keys]
    expired = [e for e in expired if e["key"] not in done_keys]
    counts = {"da": sum(i.source == "da" for i in items),
              "gap": sum(i.source == "gap" for i in items),
              "event": sum(i.source == "event" for i in items),
              "already_recorded": len(items) - len(todo_items), "expired_to_record": len(expired),
              "left": len(todo_items)}
    if not apply:
        return {"dry_run": True, **counts}
    if backup_root is not None:
        counts["backups"] = [str(p) for p in backups(workspace, backup_root, api)]
    if not rerender_only:
        for e in expired:
            _append(record, e)
        records = window_rows(da_dir, today)
        rows = evidence_rows(records)
        commits = commits_since(repositories(records, workspace), rows[0][1] if rows else today.isoformat())
        batch_items = todo_items[:max_items]
        decisions: dict[str, int] = {}
        for k in range(0, len(batch_items), max(1, jobs)):
            batch = batch_items[k:k + max(1, jobs)]
            todos = api.open()
            answers = await asyncio.gather(*(asyncio.to_thread(
                llm, prompt_for(i, rows, commits, todos)) for i in batch),
                return_exceptions=True)
            for item, answer in zip(batch, answers):
                if isinstance(answer, BaseException):
                    answer = {}
                row = apply_decision(item, checked(answer, item, rows, commits, todos),
                                     api, withdraw, record)
                decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
        counts["decided"] = decisions
        counts["left"] = len(todo_items) - len(batch_items)
    if counts["left"] == 0 or rerender_only:
        counts["rerendered_rows"] = rerender_window(da_dir, today)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", default="~/.swarm-ai/SwarmWS")
    parser.add_argument("--backup-root", default="~/.swarm-ai/backups")
    parser.add_argument("--record", default="~/.swarm-ai/backups/followup-migration.jsonl")
    parser.add_argument("--api", default=f"http://127.0.0.1:{os.environ.get('SWARMAI_PORT', '18321')}")
    parser.add_argument("--max-items", type=int, default=40)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rerender-only", action="store_true")
    args = parser.parse_args(argv)
    ws = Path(args.workspace).expanduser()
    os.environ.setdefault("SWARM_WORKSPACE", str(ws))
    out = asyncio.run(run(ws, record=Path(args.record).expanduser(), api=TodoApi(args.api),
                          apply=args.apply, max_items=args.max_items, jobs=args.jobs,
                          backup_root=Path(args.backup_root).expanduser() if args.apply else None,
                          rerender_only=args.rerender_only))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
