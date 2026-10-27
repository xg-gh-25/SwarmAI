"""DDD drift auto-fix — the OS side of "a certain drift finding is fixed, not reported".

Design: docs/design/2026-10-27-ddd-drift-auto-fix.md (r2). The weekly self-audit agent
(jobs/handlers/ddd_self_audit.py) is read-only; it may attach a ``fix`` to a finding:

    {"doc": "PROJECT.md", "old": "<verbatim text on one line>", "new": "<replacement>",
     "evidence": [{"kind": "done|superseded|exists|code", "file": "<path>", "quote": "<verbatim>"}]}

This module decides, by checks a reader can reproduce, whether that fix is CERTAIN, and
writes it. "Certain" does not mean "another sentence disagrees" (the other sentence may be
the stale one); it means the evidence settles the answer:

- ``done``       the quote is a ticked ``- [x]`` checklist line in that file;
- ``superseded`` the quote marks a document as superseded;
- ``exists``     a path the fix puts in really is a file;
- ``code``       the quote is in the project's bound source repo, outside test dirs.

Plus: every identifier, number or path the fix ADDS appears verbatim in some evidence; the
edit stays inside one line of a canonical DDD doc, outside headings, code fences, metadata
comments and lifecycle entries (dated records are not rewritten); evidence never comes from
past audit reports or ``.artifacts/``; and a fix never undoes an earlier auto-fix. Anything
else is returned as open with its reason.

The model never decides and never writes: ``check_fix`` is pure over the files it reads,
``apply_fix`` re-checks under the shared DDD lock, records the fix (for one-fix undo) and
backs the doc up before it changes a byte. Projects other than SwarmAI are not in git, so
the fix log is the only way back.

Not ``ingestion_gate.self_adversarial_judge``: that judge is a noise filter ("not a
fact-checker", it never sees ``old``); a fix carries evidence that can be checked
verbatim, which is a harder gate than a judge (design §3.2).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.ddd_cultivation import _confined_doc_path
from core.ddd_entry_lifecycle import parse_entries
from core.project_registry import DDD_CANONICAL_DOCS

FIX_LOG = "ddd-drift-fixes.jsonl"
BACKUP_DIR = "drift-fix-backups"
BACKUPS_KEPT = 10
EVIDENCE_KINDS = ("done", "superseded", "exists", "code")
_MIN_OLD = 12
_MAX_EVIDENCE_BYTES = 2_000_000
_SUPERSEDED_MARKERS = ("superseded", "已被取代", "已取代")
_TEST_DIR_PARTS = {"test", "tests", "__tests__", "fixture", "fixtures", "spec", "specs", "testdata"}
_DONE_LINE = re.compile(r"^\s*[-*]\s+\[[xX]\]")
# Tokens that carry a fact: a number, a path or identifier, a code span, a CamelCase /
# ALL-CAPS name. Plain words ("now", "that") may be added freely; facts may not.
_FACT_TOKEN = re.compile(r"`[^`]+`|[A-Za-z0-9_./\-]*\d[A-Za-z0-9_./\-]*|[A-Za-z0-9_\-]*[._/][A-Za-z0-9_./\-]+"
                         r"|\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b|\b[A-Z]{2,}[A-Z0-9_]*\b")


class Outcome(dict):
    """{"ok": bool, "reason": str, ...}. A dict so it serializes straight into the report."""


def _open(reason: str, **extra) -> Outcome:
    return Outcome(ok=False, reason=reason, **extra)


def parse_fix(finding: object) -> dict | None:
    """The ``fix`` of a finding when it has the expected shape, else None."""
    if not isinstance(finding, dict):
        return None
    fix = finding.get("fix")
    if not isinstance(fix, dict):
        return None
    if not all(isinstance(fix.get(k), str) for k in ("doc", "old", "new")):
        return None
    ev = fix.get("evidence")
    if not isinstance(ev, list) or not ev:
        return None
    items = [e for e in ev if isinstance(e, dict) and isinstance(e.get("kind"), str)
             and isinstance(e.get("file"), str)]
    if len(items) != len(ev):
        return None
    return {"doc": fix["doc"], "old": fix["old"], "new": fix["new"], "evidence": items}


def _fact_tokens(text: str) -> set[str]:
    return {t.strip("`").rstrip(".,;:") for t in _FACT_TOKEN.findall(text or "")} - {""}


def _read(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_EVIDENCE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _evidence_path(file: str, project_dir: Path, workspace: Path, source_repo: Path | None,
                   *, code: bool) -> Path | None:
    """Where an evidence ``file`` points, or None when it is not an allowed source:
    a ``code`` quote only from the bound source repo (outside test dirs); anything else
    from the project's DDD docs or the workspace — never past reports or ``.artifacts``."""
    if not file or "\x00" in file or Path(file).is_absolute():
        return None
    parts = set(Path(file).parts)
    if ".." in parts or ".artifacts" in parts:
        return None
    if code:
        if source_repo is None or parts & _TEST_DIR_PARTS:
            return None
        cand = source_repo / file
        return cand if _inside(cand, source_repo) and cand.is_file() else None
    if Path(file).name in DDD_CANONICAL_DOCS and len(Path(file).parts) == 1:
        doc = _confined_doc_path(project_dir, file)
        if doc is not None:
            return doc
    for root in ([workspace] + ([source_repo] if source_repo else [])):
        cand = root / file
        if _inside(cand, root) and cand.is_file():
            rel = cand.resolve().relative_to(root.resolve()).as_posix()
            if rel.startswith("Knowledge/JobResults/") or "/.artifacts/" in f"/{rel}":
                return None
            return cand
    return None


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index)


def _in_code_fence(lines: list[str], line_no: int) -> bool:
    fences = sum(1 for ln in lines[:line_no] if ln.lstrip().startswith("```"))
    return fences % 2 == 1


def past_fixes(project_dir: Path) -> list[dict]:
    log = project_dir / ".artifacts" / FIX_LOG
    out = []
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


def check_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None) -> Outcome:
    """Is this fix certain? Pure over the files it reads; never writes."""
    doc_name, old, new = fix["doc"], fix["old"], fix["new"]
    if doc_name not in DDD_CANONICAL_DOCS:
        return _open(f"not a DDD document: {doc_name}")
    doc = _confined_doc_path(project_dir, doc_name)
    if doc is None:
        return _open(f"{doc_name} not found in the project")
    text = _read(doc)
    if text is None:
        return _open(f"{doc_name} could not be read")

    # the edit: one line, found exactly once, outside structure and dated records
    if len(old) < _MIN_OLD or "\n" in old:
        return _open("the text to replace is shorter than a phrase or spans lines")
    if text.count(old) != 1:
        return _open(f"the text to replace appears {text.count(old)} times in {doc_name}")
    if not new.strip() or new == old:
        return _open("the replacement is empty or the same")
    if "\n" in new or "<!--" in new or "-->" in new or new.lstrip().startswith(("#", "- ", "* ")):
        return _open("the replacement would change the document's structure")
    if len(new) > max(2 * len(old), len(old) + 300):
        return _open("the replacement is much longer than what it replaces")
    if "<!--" in old or "-->" in old:
        return _open("the text to replace is inside entry metadata")
    lines = text.splitlines()
    line_no = _line_of(text, text.index(old))
    if line_no < len(lines) and lines[line_no].lstrip().startswith("#"):
        return _open("the text to replace is a heading")
    line_start = text.rfind("\n", 0, text.index(old)) + 1
    col = text.index(old) - line_start
    line = lines[line_no] if line_no < len(lines) else ""
    for m in re.finditer(r"<!--.*?(?:-->|$)", line):
        if m.start() <= col < m.end():
            return _open("the text to replace is inside entry metadata")
    if _in_code_fence(lines, line_no):
        return _open("the text to replace is inside a code block")
    for e in parse_entries(text):
        if e.line_number <= line_no <= max(e.end_line, e.line_number):
            return _open("this is a dated record, true when written: correct it by superseding, not by rewriting")

    # never undo an earlier auto-fix of this doc
    for row in past_fixes(project_dir):
        if row.get("target_doc") == doc_name and (row.get("old") == new or row.get("new") == old):
            return _open("this would undo an earlier automatic fix")

    # the evidence decides the answer
    quotes: list[str] = []
    for ev in fix["evidence"]:
        kind, file, quote = ev.get("kind"), ev.get("file", ""), ev.get("quote") or ""
        if kind not in EVIDENCE_KINDS:
            return _open("the evidence is another statement, not one that settles it (done / superseded / exists / code)")
        if kind == "exists":
            path = _evidence_path(file, project_dir, workspace, source_repo, code=False)
            if path is None or file not in new:
                return _open(f"the path {file} does not exist or is not what the fix puts in")
            quotes.append(file)
            continue
        if source_repo is None and kind == "code":
            return _open("code evidence for a project without a bound source repo")
        path = _evidence_path(file, project_dir, workspace, source_repo, code=kind == "code")
        body = _read(path) if path else None
        if body is None:
            return _open(f"evidence file {file} is not an allowed source or does not exist")
        if not quote or "\n" in quote or quote not in body:
            return _open(f"the evidence quote is not in {file}")
        if kind == "done":
            qline = body.splitlines()[_line_of(body, body.index(quote))]
            if not _DONE_LINE.match(qline):
                return _open("the 'done' evidence is not a ticked checklist line")
        if kind == "superseded" and not any(m in quote.lower() for m in _SUPERSEDED_MARKERS):
            return _open("the 'superseded' evidence does not say superseded")
        quotes.append(quote)

    added = _fact_tokens(new) - _fact_tokens(old)
    unsupported = sorted(t for t in added if not any(t in q for q in quotes))
    if unsupported:
        return _open(f"the fix adds facts the evidence does not show: {', '.join(unsupported[:5])}")
    return Outcome(ok=True, reason="certain", doc_path=str(doc))


def _backup(project_dir: Path, doc: Path, run_id: str) -> None:
    """One whole-doc copy per doc per run (the first fix); keep the newest BACKUPS_KEPT."""
    d = project_dir / ".artifacts" / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{doc.stem}.{run_id}.md"
    if not target.exists():
        target.write_bytes(doc.read_bytes())
    mine = sorted(d.glob(f"{doc.stem}.*.md"), key=lambda p: p.stat().st_mtime)
    for old in mine[:-BACKUPS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None,
              *, run_id: str, finding_title: str = "", dry_run: bool = False) -> Outcome:
    """Check and, when certain, write the fix. Order: lock → re-read and re-check → log the
    fix → back the doc up → replace atomically. A failure before the replace changes nothing."""
    from utils.file_lock import md_lock

    first = check_fix(fix, project_dir, workspace, source_repo)
    if not first.get("ok"):
        return first
    if dry_run:
        return Outcome(ok=True, reason="would fix (dry run)", dry_run=True)
    doc = Path(first["doc_path"])
    with md_lock(doc, blocking=False) as got:
        if not got:
            return _open("the document is being written; next run will retry")
        again = check_fix(fix, project_dir, workspace, source_repo)  # the doc may have changed
        if not again.get("ok"):
            return again
        text = doc.read_text(encoding="utf-8")
        log = project_dir / ".artifacts" / FIX_LOG
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "run": run_id, "target_doc": fix["doc"], "old": fix["old"], "new": fix["new"],
                    "evidence": fix["evidence"], "finding": finding_title,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + "\n")
            _backup(project_dir, doc, run_id)
        except OSError as e:
            return _open(f"could not record the fix before writing ({e.__class__.__name__}); nothing changed")
        _atomic_write(doc, text.replace(fix["old"], fix["new"], 1))
    return Outcome(ok=True, reason="fixed")


def undo_drift_fix(project_dir: Path, index: int) -> Outcome:
    """Put one fix back (``index`` into the fix log): ``new`` → ``old``, only when ``new``
    still appears exactly once — a later edit of that text means a person owns it now."""
    from utils.file_lock import md_lock

    rows = past_fixes(project_dir)
    if not 0 <= index < len(rows):
        return _open("no such fix")
    row = rows[index]
    doc = _confined_doc_path(project_dir, str(row.get("target_doc", "")))
    if doc is None:
        return _open("the document is gone")
    with md_lock(doc, blocking=True):
        text = doc.read_text(encoding="utf-8")
        new, old = str(row.get("new", "")), str(row.get("old", ""))
        if not new or text.count(new) != 1:
            return _open("the fixed text was edited since; not undone")
        _atomic_write(doc, text.replace(new, old, 1))
    return Outcome(ok=True, reason="undone")


def first_seen(needle: str, reports_dir: Path, *, before: str | None = None) -> str | None:
    """The date of the earliest weekly audit report that contains ``needle`` verbatim
    (dry-run reports are a different file name and never match)."""
    if not needle or len(needle) < 8:
        return None
    for path in sorted(reports_dir.glob("*-ddd-self-audit.md")):
        date = path.name[:10]
        if before and date >= before:
            continue
        body = _read(path)
        if body and needle in body:
            return date
    return None


__all__ = ["EVIDENCE_KINDS", "FIX_LOG", "Outcome", "apply_fix", "check_fix", "first_seen",
           "parse_fix", "past_fixes", "undo_drift_fix"]
