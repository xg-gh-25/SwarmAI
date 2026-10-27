"""DDD drift auto-fix — the OS side of "a certain drift finding is fixed, not reported".

Design: docs/design/2026-10-27-ddd-drift-auto-fix.md (r3). The weekly self-audit agent
(jobs/handlers/ddd_self_audit.py) can only read; it may attach a ``fix`` to a finding:

    {"doc": "PROJECT.md", "old": "<verbatim text on one line>", "new": "<replacement>",
     "evidence": [{"kind": "done|superseded|exists|code", "file": "<path>", "quote": "<verbatim>"}]}

This module decides, by checks anyone can reproduce, whether the fix is CERTAIN, and
writes it. Certain means the evidence settles the answer AND the edit can only say what
the evidence says:

- the edit is one of two shapes: delete one contiguous span of ``old``, or replace one
  span with text that appears, token for token, in an evidence quote (a path, a number, a
  name copied from the source of truth). No other word may be added;
- each evidence item is about the claim (it shares a run of at least 8 characters with
  ``old``) and is of a kind that settles it: a ticked ``- [x]`` line in this project's own
  DDD docs (``done``); a line in a document that declares that document superseded, where
  ``old`` names that document (``superseded``); a file the fix points to that exists
  (``exists``); a line of source code in the project's bound repo (``code``). Evidence
  comes only from the project directory and its bound repo, never from past reports,
  ``.artifacts``, other projects or notes;
- the edit stays inside one plain line of a canonical DDD doc: not a heading, code
  block, metadata comment, front matter, table cell divider, checkbox or dated lifecycle
  entry; no control or line-separator characters anywhere; the line keeps its markdown
  prefix;
- it never brings back text an earlier fix removed, and never re-applies a fix the
  user undid.

The model never decides and never writes. ``apply_fix`` re-checks under the shared DDD
lock, backs the doc up, records the fix, then replaces the file atomically if it is
unchanged since the check; ``undo_drift_fix`` puts one fix back and records that, so the
next run does not re-apply it. Projects other than SwarmAI are not in git: the fix log
(``.artifacts/ddd-drift-fixes.jsonl``) and the backups are the only way back.

Not ``ingestion_gate.self_adversarial_judge``: that judge is a noise filter ("not a
fact-checker", it never sees ``old``). A fix carries evidence that can be checked
verbatim, which is a harder gate (design §3.2). What remains: someone who can already
write this project's DDD docs can plant a ticked line; the fix still cannot say anything
that line does not.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import unicodedata
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
_MIN_SHARED = 8
_MAX_EVIDENCE_BYTES = 2_000_000
_SUPERSEDED = re.compile(r"\bsuperseded\s+by\b|^\W*superseded\b|已被取代|已取代", re.IGNORECASE)
_TEST_PARTS = {"test", "tests", "__tests__", "fixture", "fixtures", "spec", "specs", "testdata", "mocks", "__mocks__"}
_TEST_NAME = re.compile(r"^test_|_test\.|\.test\.|\.spec\.", re.IGNORECASE)
_CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".java", ".kt", ".go", ".rs", ".rb", ".swift",
             ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".scala", ".php", ".smithy", ".sql", ".proto"}
_DONE_LINE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s")
_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|>\s*|#{1,6}\s+)?")
# a word (a trailing "." is punctuation, not part of it), a CJK character, or a single
# other non-space character (backticks too, so `x` and x carry the same word)
_TOKEN = re.compile(r"[A-Za-z0-9_./\-]*[A-Za-z0-9_/\-]|[㐀-鿿]|\S")


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


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(_norm(text))


def _bad_chars(text: str) -> bool:
    """Control and line/paragraph separators (``str.splitlines`` splits on several of
    them, so they can forge a new line), plus fence markers."""
    if "```" in text or "~~~" in text:
        return True
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp") and c != "\t" for c in text)


def _odd_separators(text: str) -> bool:
    """A doc with a line break other than ``\n`` / ``\r\n``: line numbers would not agree
    between this module and the markdown readers, so it is not edited automatically."""
    body = text.replace("\r\n", "\n")
    return any(c not in "\n\t" and unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in body)


def _read(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_EVIDENCE_BYTES or path.stat().st_nlink != 1:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _rel_inside(path: Path, root: Path) -> str | None:
    """``path`` relative to ``root`` (both resolved), or None when it is outside."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None


def _evidence_path(kind: str, file: str, project_dir: Path, source_repo: Path | None) -> Path | None:
    """Where an evidence ``file`` points, or None when it is not an allowed source.
    ``done``: this project's canonical DDD docs. ``code``: a source file of the bound
    repo outside test dirs. ``superseded`` / ``exists``: anything in the project dir or the
    bound repo except ``.artifacts`` (logs, backups) — compared case-insensitively,
    because macOS paths are."""
    if not file or "\x00" in file or Path(file).is_absolute():
        return None
    parts = {p.casefold() for p in Path(file).parts}
    if ".." in parts or ".artifacts" in parts:
        return None
    if kind == "done":
        if Path(file).name not in DDD_CANONICAL_DOCS:
            return None
        return _confined_doc_path(project_dir, Path(file).name)
    if kind == "code":
        if source_repo is None or parts & _TEST_PARTS or _TEST_NAME.search(Path(file).name):
            return None
        if Path(file).suffix.lower() not in _CODE_EXT:
            return None
        roots = [source_repo]
    else:
        roots = [project_dir] + ([source_repo] if source_repo else [])
    for root in roots:
        cand = root / file
        rel = _rel_inside(cand, root)
        if rel is None or ".artifacts" in {p.casefold() for p in Path(rel).parts}:
            continue
        if cand.is_file():
            return cand
    return None


def _shares_run(a: str, b: str) -> bool:
    """Do ``a`` and ``b`` share a run of at least _MIN_SHARED characters with five or more
    letters or digits in it (the evidence is about this claim, not merely nearby)?"""
    a, b = _norm(a).casefold(), _norm(b).casefold()
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    run = a[m.a:m.a + m.size]
    return m.size >= _MIN_SHARED and sum(ch.isalnum() for ch in run) >= 5


def _edit_shape(old: str, new: str, quotes: list[str]) -> str | None:
    """None when ``new`` is ``old`` with one span deleted, or one span replaced by text
    that appears token for token in a quote; else the reason it is not."""
    a, b = _tokens(old), _tokens(new)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return "the replacement is the same text"
    if len(ops) > 1:
        return "the fix changes more than one place in the line"
    tag, _i1, _i2, j1, j2 = ops[0]
    if tag == "delete":
        return None
    inserted = b[j1:j2]
    for q in quotes:
        qt = _tokens(q)
        n = len(inserted)
        if any(qt[k:k + n] == inserted for k in range(len(qt) - n + 1)):
            return None
    return f"the fix adds words the evidence does not contain: {' '.join(inserted)[:80]}"


def _log_rows(project_dir: Path) -> list[dict]:
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


def past_fixes(project_dir: Path) -> list[dict]:
    """Applied fixes still in force: an ``undone`` row cancels its fix, a ``failed`` row
    never counted. Each carries ``index``, its position among applied rows."""
    rows = _log_rows(project_dir)
    undone = {r.get("fix_id") for r in rows if r.get("status") == "undone"}
    applied = [r for r in rows if r.get("status", "applied") == "applied"]
    failed = {r.get("fix_id") for r in rows if r.get("status") == "failed"}
    out = []
    for i, r in enumerate(applied):
        if r.get("fix_id") in failed:
            continue
        out.append({**r, "index": i, "undone": r.get("fix_id") in undone})
    return out


def check_fix(fix: dict, project_dir: Path, source_repo: Path | None) -> Outcome:
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
    if _odd_separators(text):
        return _open(f"{doc_name} has unusual line separators; not edited automatically")

    # one plain line
    if len(old) < _MIN_OLD or "\n" in old or _bad_chars(old):
        return _open("the text to replace is shorter than a phrase, spans lines or has control characters")
    if text.count(old) != 1:
        return _open(f"the text to replace appears {text.count(old)} times in {doc_name}")
    if not new.strip() or not any(c.isalnum() for c in new) or "\n" in new or _bad_chars(new):
        return _open("the replacement is empty or would change the document's structure")
    if "<!--" in new or "-->" in new or "<!--" in old or "-->" in old:
        return _open("the fix touches entry metadata")
    boxes = lambda t: [b.lower() for b in re.findall(r"\[([ xX])\]", t)]  # noqa: E731
    if new.count("|") != old.count("|") or boxes(new) != boxes(old):
        return _open("the fix changes a table or a checkbox")
    if len(new) > max(2 * len(old), len(old) + 300):
        return _open("the replacement is much longer than what it replaces")
    pos = text.index(old)
    lines = text.split("\n")
    line_no = text.count("\n", 0, pos)
    line = lines[line_no]
    col = pos - (text.rfind("\n", 0, pos) + 1)
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        if line_no <= closing:
            return _open("the text to replace is in the front matter")
    if line.lstrip().startswith("#") or (line_no + 1 < len(lines) and re.fullmatch(r"\s*(=+|-{3,})\s*", lines[line_no + 1])):
        return _open("the text to replace is a heading")
    if col <= len(_LINE_PREFIX.match(line).group(0)) and _LINE_PREFIX.match(old).group(0) != _LINE_PREFIX.match(new).group(0):
        return _open("the fix changes the line's markdown marker")
    fences = sum(1 for ln in lines[:line_no] if ln.lstrip().startswith(("```", "~~~")))
    if fences % 2 == 1:
        return _open("the text to replace is inside a code block")
    for m in re.finditer(r"<!--.*?(?:-->|$)", line):
        if m.start() <= col < m.end():
            return _open("the text to replace is inside entry metadata")
    for e in parse_entries(text):
        if e.line_number <= line_no <= max(e.end_line, e.line_number):
            return _open("this is a dated record, true when written: correct it by superseding, not by rewriting")

    # never bring back what an earlier fix removed, never redo an undone fix
    after = text.replace(old, new, 1)
    for row in past_fixes(project_dir):
        if row.get("target_doc") != doc_name:
            continue
        if row["undone"] and row.get("old") == old and row.get("new") == new:
            return _open("you undid this fix before; not applied again")
        if not row["undone"] and row.get("old") and row["old"] in after and row["old"] not in text:
            return _open("this would undo an earlier automatic fix")

    # the evidence settles it
    quotes: list[str] = []
    for ev in fix["evidence"]:
        kind, file, quote = ev.get("kind"), ev.get("file", ""), ev.get("quote") or ""
        if kind not in EVIDENCE_KINDS:
            return _open("the evidence is another statement, not one that settles it (done / superseded / exists / code)")
        path = _evidence_path(kind, file, project_dir, source_repo)
        if kind == "exists":
            if path is None or _norm(file) not in _norm(new) or _norm(file) in _norm(old):
                return _open(f"the path {file} does not exist in the project or its repo, or is not what the fix puts in")
            if doc is not None and path.resolve() == doc.resolve():
                return _open("a document cannot be its own evidence")
            quotes.append(file)
            continue
        body = _read(path) if path else None
        if body is None:
            return _open(f"evidence file {file} is not an allowed source for '{kind}' or does not exist")
        if not quote or "\n" in quote or quote not in body:
            return _open(f"the evidence quote is not in {file}")
        if not _shares_run(quote, old) and not (kind == "superseded" and Path(file).name in old):
            return _open("the evidence is not about the claim being fixed")
        if kind == "done":
            qline = body.split("\n")[body.count("\n", 0, body.index(quote))]
            if not _DONE_LINE.match(qline):
                return _open("the 'done' evidence is not a ticked checklist line")
        if kind == "superseded":
            if not _SUPERSEDED.search(quote) or (Path(file).name not in old and Path(file).stem not in old):
                return _open("the 'superseded' evidence does not declare the document named in the text superseded")
        quotes.append(quote)

    why = _edit_shape(old, new, quotes)
    if why:
        return _open(why)
    st = doc.stat()
    return Outcome(ok=True, reason="certain", doc_path=str(doc), mtime_ns=st.st_mtime_ns, size=st.st_size)


def _backup(project_dir: Path, doc: Path, run_id: str) -> None:
    """One whole-doc copy per doc per run. Keeps the oldest (before any auto-fix) and the
    newest BACKUPS_KEPT - 1."""
    d = project_dir / ".artifacts" / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{doc.stem}.{run_id}.md"
    if not target.exists():
        target.write_bytes(doc.read_bytes())
    mine = sorted(d.glob(f"{doc.stem}.*.md"), key=lambda p: p.stat().st_mtime)
    for stale in mine[1:-(BACKUPS_KEPT - 1)] if len(mine) > BACKUPS_KEPT else []:
        try:
            stale.unlink()
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_log(project_dir: Path, row: dict) -> None:
    log = project_dir / ".artifacts" / FIX_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def apply_fix(fix: dict, project_dir: Path, source_repo: Path | None,
              *, run_id: str, finding_title: str = "", dry_run: bool = False) -> Outcome:
    """Check and, when certain, write the fix. Under the lock: re-check, back up, log,
    then replace atomically only if the file is unchanged since the re-check. A failure
    before the replace changes nothing; a failed replace is logged as failed."""
    from utils.file_lock import md_lock

    first = check_fix(fix, project_dir, source_repo)
    if not first.get("ok"):
        return first
    if dry_run:
        return Outcome(ok=True, reason="would fix (dry run)", dry_run=True)
    doc = Path(first["doc_path"])
    with md_lock(doc, blocking=False) as got:
        if not got:
            return _open("the document is being written; next run will retry")
        again = check_fix(fix, project_dir, source_repo)
        if not again.get("ok"):
            return again
        text = doc.read_text(encoding="utf-8")
        digest = hashlib.sha1(f"{fix['old']}\x00{fix['new']}".encode("utf-8")).hexdigest()[:12]
        fix_id = f"{run_id}:{fix['doc']}:{digest}"
        row = {"fix_id": fix_id, "status": "applied", "run": run_id, "target_doc": fix["doc"],
               "old": fix["old"], "new": fix["new"], "evidence": fix["evidence"], "finding": finding_title,
               "timestamp": datetime.now(timezone.utc).isoformat()}
        try:
            _backup(project_dir, doc, run_id)
            _append_log(project_dir, row)
        except OSError as e:
            return _open(f"could not back up or record the fix ({e.__class__.__name__}); nothing changed")
        try:
            st = doc.stat()
            if (st.st_mtime_ns, st.st_size) != (again["mtime_ns"], again["size"]):
                raise RuntimeError("the document changed while the fix was being written")
            _atomic_write(doc, text.replace(fix["old"], fix["new"], 1))
        except (OSError, RuntimeError) as e:
            try:
                _append_log(project_dir, {"fix_id": fix_id, "status": "failed", "reason": str(e)[:200],
                                          "timestamp": datetime.now(timezone.utc).isoformat()})
            except OSError:
                pass
            return _open(f"the fix was not written: {str(e)[:120]}; nothing changed")
    return Outcome(ok=True, reason="fixed", fix_id=fix_id)


def undo_drift_fix(project_dir: Path, index: int) -> Outcome:
    """Put one fix back (``index`` as listed by ``past_fixes``): ``new`` → ``old``, only
    when ``new`` still appears exactly once (a later edit means a person owns that text
    now). The undo is logged, so the same fix is never applied again."""
    from utils.file_lock import md_lock

    rows = {r["index"]: r for r in past_fixes(project_dir)}
    row = rows.get(index)
    if row is None:
        return _open("no such fix")
    if row["undone"]:
        return _open("already undone")
    doc = _confined_doc_path(project_dir, str(row.get("target_doc", "")))
    if doc is None:
        return _open("the document is gone")
    with md_lock(doc, blocking=True):
        text = doc.read_text(encoding="utf-8")
        new, old = str(row.get("new", "")), str(row.get("old", ""))
        if not new or text.count(new) != 1:
            return _open("the fixed text was edited since; not undone")
        _atomic_write(doc, text.replace(new, old, 1))
        _append_log(project_dir, {"fix_id": row.get("fix_id"), "status": "undone",
                                  "timestamp": datetime.now(timezone.utc).isoformat()})
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
