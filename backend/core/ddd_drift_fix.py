"""DDD drift auto-fix — three narrow fixes the OS can prove, nothing else.

Design: docs/design/2026-10-27-ddd-drift-auto-fix.md (r4). The weekly self-audit agent
(jobs/handlers/ddd_self_audit.py) can only read. For a finding it may name one of three
fixes; it never writes the new text — this module computes it from the document and
the evidence, and only when a fixed grammar matches:

``drop_condition``   {"doc", "line", "span", "done"}
    Delete a waiting condition ("once `X` exists", "after `X` ships", "等 `X` 完成后" …,
    a fixed grammar) from the one line that contains ``line``, when ``done`` is a ticked
    ``- [x]`` line of this project's DDD docs that names the same `X`.
``swap_superseded_path``   {"doc", "path_old", "path_new"}
    Replace the one occurrence of a path with the path that the file at ``path_old``
    declares in its header ("SUPERSEDED … `path_new`", "status: superseded …"), when
    ``path_new`` exists.
``swap_code_value``   {"doc", "line", "identifier", "value_old", "value_new", "code_file"}
    Replace a number / version that the line states for ``identifier`` with the value a
    git-tracked source file of the bound repo assigns to it (``identifier = value``).

Every fix also stays inside one plain line of a canonical DDD doc (not a heading, code
block, front matter, metadata comment or dated lifecycle entry; the doc has no unusual
line separators), never re-applies a fix the user undid, and never undoes an earlier
fix. Anything else stays open with its reason. No free text from the model reaches a
document: free-text fixes could not be made safe by checks (two review rounds found
meaning-flipping deletions and borrowed words each time).

``apply_fix`` re-checks under the shared DDD lock, backs the doc up, records the fix,
then replaces the line only if the file is unchanged since the re-check;
``undo_drift_fix`` records the undo first, then puts the line back. Projects other than
SwarmAI are not in git: the log (``.artifacts/ddd-drift-fixes.jsonl``) and the backups are
the only way back.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
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
FIX_TYPES = ("drop_condition", "swap_superseded_path", "swap_code_value")
_FIELDS = {
    "drop_condition": ("doc", "line", "span", "done"),
    "swap_superseded_path": ("doc", "path_old", "path_new"),
    "swap_code_value": ("doc", "line", "identifier", "value_old", "value_new", "code_file"),
}
_MIN_LINE = 12
_MAX_BYTES = 2_000_000
_HEADER_LINES = 10

_IDENT = r"`(?P<x>[^`\s]{3,120})`"
_IDENT2 = r"`(?P<y>[^`\s]{3,120})`"
_DONE_VERB = r"(?:exists|lands|ships|is\s+(?:built|done|ready|available|implemented|shipped|merged))"
_CONDITION = re.compile(
    rf"(?:once|after|until|when)\s+{_IDENT}\s+{_DONE_VERB}"
    rf"|(?:等|待)\s*{_IDENT2}\s*(?:存在|上线|完成|就绪|落地|建好)(?:之?后)?",
    re.IGNORECASE)
_DONE_LINE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s")
_SUPERSEDED_HEAD = re.compile(
    r"^[\s>*_#]*(?:⚠️?\s*)?(?:\*\*)?\s*(?:superseded\b|status\s*:\s*\**\s*superseded\b|已被取代|已取代|已废弃)",
    re.IGNORECASE)
_PATH = re.compile(r"^[A-Za-z0-9_./\-]+$")
_PATH_CHAR = r"A-Za-z0-9_./\-"
_VALUE = re.compile(r"^v?\d+(?:\.\d+){0,3}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{1,80}$")
_TEST_PARTS = {"test", "tests", "__tests__", "fixture", "fixtures", "spec", "specs", "testdata", "mocks",
               "__mocks__", "node_modules", "vendor", "dist", "build", ".venv", "venv", "site-packages",
               "third_party", "external"}
_TEST_NAME = re.compile(r"^test_|_test\.|\.test\.|\.spec\.", re.IGNORECASE)
_CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".java", ".kt", ".go", ".rs", ".rb", ".swift",
             ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".scala", ".php", ".smithy", ".sql", ".proto"}


class Outcome(dict):
    """{"ok": bool, "reason": str, ...}. A dict so it serializes straight into the report."""


def _open(reason: str, **extra) -> Outcome:
    return Outcome(ok=False, reason=reason, **extra)


def parse_fix(finding: object) -> dict | None:
    """The ``fix`` of a finding when it is one of the three types with every field a
    non-empty string, else None."""
    if not isinstance(finding, dict) or not isinstance(finding.get("fix"), dict):
        return None
    fix = finding["fix"]
    kind = fix.get("type")
    if kind not in _FIELDS or not all(isinstance(fix.get(k), str) and fix.get(k) for k in _FIELDS[kind]):
        return None
    return {"type": kind, **{k: fix[k] for k in _FIELDS[kind]}}


# ─── reading ───────────────────────────────────────────────────────────────

def _read(path: Path | None) -> str | None:
    try:
        if path is None or not path.is_file():
            return None
        st = path.stat()
        if st.st_size > _MAX_BYTES or st.st_nlink != 1:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _odd_separators(text: str) -> bool:
    """A line break other than ``\\n`` / ``\\r\\n``, or an invisible format character:
    line numbers would not agree with the markdown readers, or the text would not read
    as it looks. Such a doc is not edited automatically."""
    body = text.replace("\r\n", "\n")
    return any(c not in "\n\t" and unicodedata.category(c) in ("Cc", "Zl", "Zp", "Cf") for c in body)


def _under(root: Path, rel: str) -> Path | None:
    """``root / rel`` when it stays inside ``root`` (both resolved), is a file, and is not
    under an ``.artifacts`` dir (logs, backups) — compared case-insensitively."""
    if not rel or "\x00" in rel or Path(rel).is_absolute():
        return None
    if {p.casefold() for p in Path(rel).parts} & {"..", ".artifacts"}:
        return None
    try:
        cand = (root / rel).resolve()
        cand.relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        return None
    return cand if cand.is_file() else None


def _resolve_path(rel: str, roots: list[Path]) -> Path | None:
    for root in roots:
        hit = _under(root, rel)
        if hit is not None:
            return hit
    return None


# ─── the line being edited ─────────────────────────────────────────────────

def _target_line(text: str, needle: str) -> tuple[int, str] | Outcome:
    """The index and text of the one line containing ``needle`` (which must occur once
    in the whole doc), when that line may be edited."""
    if len(needle) < _MIN_LINE or "\n" in needle:
        return _open("the quoted line is shorter than a phrase or spans lines")
    if text.count(needle) != 1:
        return _open(f"the quoted text appears {text.count(needle)} times in the document")
    lines = text.split("\n")
    idx = text.count("\n", 0, text.index(needle))
    line = lines[idx]
    if lines and lines[0].strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        if idx <= closing:
            return _open("the line is in the front matter")
    nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
    if line.lstrip().startswith("#") or (line.strip() and re.fullmatch(r"\s*(=+|-+)\s*", nxt)):
        return _open("the line is a heading")
    if sum(1 for ln in lines[:idx] if ln.lstrip().startswith(("```", "~~~"))) % 2 == 1:
        return _open("the line is inside a code block")
    if "<!--" in line or "-->" in line:
        return _open("the line carries entry metadata")
    for e in parse_entries(text):
        if e.line_number <= idx <= max(e.end_line, e.line_number):
            return _open("this is a dated record, true when written; not rewritten")
    return idx, line


def _tidy(line: str) -> str:
    """Spacing after a deletion: no doubled spaces, no space before punctuation, no
    empty parentheses — the line keeps its leading indentation."""
    lead = line[: len(line) - len(line.lstrip())]
    body = line[len(lead):]
    body = re.sub(r"\(\s*\)|（\s*）", "", body)
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\s+([,.;:)，。；：）])", r"\1", body)
    return lead + body.rstrip()


def _names_same(x: str, y: str) -> bool:
    """Do two identifiers name the same thing: equal, or one is the other qualified by a
    prefix (``pkg/x`` · ``s_x`` · ``a.x`` — the separator is ``/ _ - . :``)?"""
    if x == y:
        return True
    short, long_ = sorted((x, y), key=len)
    return len(short) >= 6 and long_.endswith(short) and long_[-len(short) - 1] in "/_-.:"


# ─── the three fixes ───────────────────────────────────────────────────────

def _check_drop_condition(fix: dict, text: str, project_dir: Path) -> tuple[str, str] | Outcome:
    hit = _target_line(text, fix["line"])
    if isinstance(hit, Outcome):
        return hit
    _idx, line = hit
    span = fix["span"]
    m = _CONDITION.fullmatch(span.strip())
    if not m:
        return _open("the span is not a waiting condition the system knows (once / after / until / 等 `X` …)")
    if line.count(span) != 1:
        return _open("the span is not in the quoted line exactly once")
    x = m.group("x") or m.group("y") or ""
    done_line = None
    if "\n" not in fix["done"]:
        for name in DDD_CANONICAL_DOCS:
            body = _read(_confined_doc_path(project_dir, name))
            if body is None or fix["done"] not in body:
                continue
            cand = body.split("\n")[body.count("\n", 0, body.index(fix["done"]))]
            if _DONE_LINE.match(cand):
                done_line = cand
                break
    if done_line is None:
        return _open("the 'done' quote is not a ticked line of this project's DDD docs")
    if done_line.strip() == line.strip():
        return _open("a line cannot be its own evidence")
    if not any(_names_same(x, y) for y in re.findall(r"`([^`\s]+)`", done_line)):
        return _open(f"the ticked line does not name `{x}`")
    return line, _tidy(line.replace(span, "", 1))


def _check_swap_path(fix: dict, text: str, project_dir: Path, workspace: Path,
                     repo: Path | None) -> tuple[str, str] | Outcome:
    old, new = fix["path_old"], fix["path_new"]
    if not (_PATH.match(old) and _PATH.match(new)) or old == new or ("/" not in new and "." not in new):
        return _open("not two paths")
    bounded = re.compile(rf"(?<![{_PATH_CHAR}]){re.escape(old)}(?![{_PATH_CHAR}])")
    found = bounded.findall(text)
    if len(found) != 1:
        return _open(f"the path appears {len(found)} times in the document")
    lines = text.split("\n")
    line_with = next(ln for ln in lines if bounded.search(ln))
    hit = _target_line(text, line_with if len(line_with) >= _MIN_LINE else old)
    if isinstance(hit, Outcome):
        return hit
    _idx, line = hit
    roots = [project_dir, workspace] + ([repo] if repo else [])
    head = _read(_resolve_path(old, roots))
    if head is None:
        return _open(f"{old} is not a readable file in the project, the workspace or the repo")
    if _resolve_path(new, roots) is None:
        return _open(f"{new} does not exist")
    if not any(_SUPERSEDED_HEAD.match(ln) and new in ln for ln in head.split("\n")[:_HEADER_LINES]):
        return _open(f"{old} does not declare itself superseded by {new} in its header")
    return line, bounded.sub(new, line, count=1)


def _git_tracked(repo: Path, rel: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", rel],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _check_swap_value(fix: dict, text: str, repo: Path | None) -> tuple[str, str] | Outcome:
    ident, vold, vnew, file = fix["identifier"], fix["value_old"], fix["value_new"], fix["code_file"]
    if not _IDENTIFIER.match(ident) or not _VALUE.match(vold) or not _VALUE.match(vnew) or vold == vnew:
        return _open("not an identifier with two numeric values")
    if repo is None:
        return _open("the project has no bound source repo")
    parts = {p.casefold() for p in Path(file).parts}
    if parts & _TEST_PARTS or _TEST_NAME.search(Path(file).name) or Path(file).suffix.lower() not in _CODE_EXT:
        return _open(f"{file} is not a source file (tests, vendored and built files do not count)")
    src = _under(repo, file)
    if src is None or not _git_tracked(repo, file):
        return _open(f"{file} is not a tracked file of the bound repo")
    bare = vnew[1:] if vnew.startswith("v") else vnew
    assign = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])\s*[:=]\s*[\"']?v?"
                        rf"{re.escape(bare)}[\"']?(?![0-9.])")
    if not assign.search(_read(src) or ""):
        return _open(f"{file} does not assign {vnew} to {ident}")
    hit = _target_line(text, fix["line"])
    if isinstance(hit, Outcome):
        return hit
    _idx, line = hit
    if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])", line):
        return _open(f"the line does not name {ident}")
    val = re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(vold)}(?![0-9]|\.[0-9])")
    if len(val.findall(line)) != 1:
        return _open(f"{vold} is not in the line exactly once")
    shown = ("v" + bare) if vold.startswith("v") else bare
    return line, val.sub(shown, line, count=1)


# ─── the log ───────────────────────────────────────────────────────────────

def _log_rows(project_dir: Path) -> list[dict]:
    out = []
    try:
        for line in (project_dir / ".artifacts" / FIX_LOG).read_text(encoding="utf-8").splitlines():
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
    """Every fix that was written (a ``failed`` write is dropped), in log order, each
    with ``index`` (its stable position among applied rows) and ``undone`` (an undo was
    recorded and not followed by a failed undo)."""
    rows = _log_rows(project_dir)
    failed = {r.get("fix_id") for r in rows if r.get("status") == "failed"}
    undone: set = set()
    for r in rows:
        if r.get("status") == "undone":
            undone.add(r.get("fix_id"))
        elif r.get("status") == "undo_failed":
            undone.discard(r.get("fix_id"))
    applied = [r for r in rows if r.get("status") == "applied"]
    return [{**r, "index": i, "undone": r.get("fix_id") in undone}
            for i, r in enumerate(applied) if r.get("fix_id") not in failed]


def _ws(s: str) -> str:
    return " ".join((s or "").split())


def check_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None) -> Outcome:
    """Is this fix one of the three provable fixes? Pure over what it reads; never
    writes. On success carries the line as it is and as it will be."""
    if fix.get("type") not in FIX_TYPES:
        return _open("not one of the three fixes the system can prove")
    if fix["doc"] not in DDD_CANONICAL_DOCS:
        return _open(f"not a DDD document: {fix['doc']}")
    doc = _confined_doc_path(project_dir, fix["doc"])
    if doc is None:
        return _open(f"{fix['doc']} not found in the project")
    try:
        st = doc.stat()  # before the read, so any later change is caught by apply_fix
    except OSError:
        return _open(f"{fix['doc']} could not be read")
    text = _read(doc)
    if text is None:
        return _open(f"{fix['doc']} could not be read")
    if _odd_separators(text):
        return _open(f"{fix['doc']} has unusual line separators or invisible characters; not edited automatically")

    if fix["type"] == "drop_condition":
        res = _check_drop_condition(fix, text, project_dir)
    elif fix["type"] == "swap_superseded_path":
        res = _check_swap_path(fix, text, project_dir, workspace, source_repo)
    else:
        res = _check_swap_value(fix, text, source_repo)
    if isinstance(res, Outcome):
        return res
    old_line, new_line = res
    if _ws(old_line) == _ws(new_line) or not new_line.strip():
        return _open("the fix would leave the line empty or unchanged")

    for row in past_fixes(project_dir):
        if row.get("target_doc") != fix["doc"]:
            continue
        if row["undone"] and _ws(row.get("old_line", "")) == _ws(old_line) and _ws(row.get("new_line", "")) == _ws(new_line):
            return _open("you undid this fix before; not applied again")
        if not row["undone"] and _ws(row.get("old_line", "")) == _ws(new_line):
            return _open("this would undo an earlier automatic fix")
    return Outcome(ok=True, reason="certain", doc_path=str(doc), mtime_ns=st.st_mtime_ns, size=st.st_size,
                   old_line=old_line, new_line=new_line)


# ─── writing ───────────────────────────────────────────────────────────────

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None,
              *, run_id: str, finding_title: str = "", dry_run: bool = False) -> Outcome:
    """Check and, when certain, write the fix. Under the lock: re-check, back up, log,
    then replace the line only if the file is unchanged since the re-check. A failure
    before the replace changes nothing; a failed replace is logged as failed."""
    from utils.file_lock import md_lock

    first = check_fix(fix, project_dir, workspace, source_repo)
    if not first.get("ok"):
        return first
    if dry_run:
        return Outcome(ok=True, reason="would fix (dry run)", dry_run=True,
                       old_line=first["old_line"], new_line=first["new_line"])
    doc = Path(first["doc_path"])
    with md_lock(doc, blocking=False) as got:
        if not got:
            return _open("the document is being written; next run will retry")
        again = check_fix(fix, project_dir, workspace, source_repo)
        if not again.get("ok"):
            return again
        old_line, new_line = again["old_line"], again["new_line"]
        digest = hashlib.sha1(f"{old_line}\x00{new_line}".encode("utf-8")).hexdigest()[:12]
        fix_id = f"{run_id}:{fix['doc']}:{digest}"
        try:
            _backup(project_dir, doc, run_id)
            _append_log(project_dir, {"fix_id": fix_id, "status": "applied", "type": fix["type"], "run": run_id,
                                      "target_doc": fix["doc"], "old_line": old_line, "new_line": new_line,
                                      "evidence": {k: v for k, v in fix.items() if k not in ("type", "doc")},
                                      "finding": finding_title, "timestamp": _now()})
        except OSError as e:
            return _open(f"could not back up or record the fix ({e.__class__.__name__}); nothing changed")
        try:
            st = doc.stat()
            lines = doc.read_text(encoding="utf-8").split("\n")
            if (st.st_mtime_ns, st.st_size) != (again["mtime_ns"], again["size"]) or lines.count(old_line) != 1:
                raise RuntimeError("the document changed while the fix was being written")
            lines[lines.index(old_line)] = new_line
            _atomic_write(doc, "\n".join(lines))
        except (OSError, RuntimeError) as e:
            try:
                _append_log(project_dir, {"fix_id": fix_id, "status": "failed", "reason": str(e)[:200], "timestamp": _now()})
            except OSError:
                pass
            return _open(f"the fix was not written: {str(e)[:120]}; nothing changed")
    return Outcome(ok=True, reason="fixed", fix_id=fix_id, old_line=old_line, new_line=new_line)


def undo_drift_fix(project_dir: Path, index: int) -> Outcome:
    """Put one fix back (``index`` as listed by ``past_fixes``), only when its new line
    is still in the doc exactly once (a later edit means a person owns that line now).
    The undo is recorded BEFORE the doc changes, so the self-audit never redoes it."""
    from utils.file_lock import md_lock

    row = {r["index"]: r for r in past_fixes(project_dir)}.get(index)
    if row is None:
        return _open("no such fix")
    if row["undone"]:
        return _open("already undone")
    doc = _confined_doc_path(project_dir, str(row.get("target_doc", "")))
    if doc is None:
        return _open("the document is gone")
    with md_lock(doc, blocking=True):
        try:
            lines = doc.read_text(encoding="utf-8").split("\n")
        except OSError:
            return _open("the document could not be read")
        new, old = str(row.get("new_line", "")), str(row.get("old_line", ""))
        if not new or lines.count(new) != 1:
            return _open("the fixed line was edited since; not undone")
        try:
            _append_log(project_dir, {"fix_id": row.get("fix_id"), "status": "undone", "timestamp": _now()})
        except OSError as e:
            return _open(f"could not record the undo ({e.__class__.__name__}); nothing changed")
        try:
            lines[lines.index(new)] = old
            _atomic_write(doc, "\n".join(lines))
        except OSError as e:
            try:
                _append_log(project_dir, {"fix_id": row.get("fix_id"), "status": "undo_failed", "timestamp": _now()})
            except OSError:
                pass
            return _open(f"the undo was not written ({e.__class__.__name__})")
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
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if needle in body:
            return date
    return None


__all__ = ["FIX_LOG", "FIX_TYPES", "Outcome", "apply_fix", "check_fix", "first_seen", "parse_fix",
           "past_fixes", "undo_drift_fix"]
