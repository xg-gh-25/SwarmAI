"""DDD drift auto-fix — three narrow fixes the OS can prove, nothing else.

Design: docs/design/2026-10-27-ddd-drift-auto-fix.md (r5). The weekly self-audit agent
(jobs/handlers/ddd_self_audit.py) can only read. For a finding it may name one of three
fixes; it never writes the new text — this module computes it from the document and the
evidence, and only when a fixed grammar matches:

``drop_condition``   {"doc", "line", "span", "done"}
    Delete a waiting condition — "once `X` exists|lands|ships|is built…", "after `X` …",
    "等/待 `X` 完成后 …" — from one line, when ``done`` is a ticked ``- [x]`` line of this
    project's DDD docs that names `X` (exactly, or as the skill ``s_X``, or qualified by a
    ``/`` or ``.`` prefix) and says nothing about removing it. The words around the span
    may not negate or limit it, and nothing but plain text may touch it.
``swap_superseded_path``   {"doc", "path_old", "path_new"}
    Replace the one occurrence of a path with the FIRST path named on the SUPERSEDED line
    (or the ``superseded_by:`` key) of that file's own header; ``path_old`` must resolve
    in exactly one root, and ``path_new`` in the same one.
``swap_code_value``   {"doc", "line", "identifier", "value_old", "value_new", "code_file"}
    Replace the value the line states for ``identifier`` (it follows the identifier) with
    the value the committed (HEAD) source file declares for it — exactly one declaration.

Every fix also stays inside one plain line of a canonical DDD doc (not front matter, a
heading, a code block, metadata or a dated lifecycle entry; the doc has no unusual
separators), never re-applies a fix the user undid (matched by what it does, not by the
line's text), and never undoes an earlier fix. No free text from the model reaches a
document: free-text fixes could not be made safe by checks.

``apply_fix`` re-checks under the shared DDD lock, backs the doc up, records the fix, then
replaces the line only if the file is unchanged since the re-check; ``undo_drift_fix``
validates the logged row against the run's backup, records the undo first, then puts
the line back. Projects other than SwarmAI are not in git: the log
(``.artifacts/ddd-drift-fixes.jsonl``) and the backups are the only way back.
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

# once / after only: "until" is the opposite wait, "when" is usually a trigger
_DONE_VERB = r"(?:exists|lands|ships|is\s+(?:built|done|ready|available|implemented|shipped|merged))"
_CONDITION = re.compile(
    rf"(?:once|after)\s+`(?P<x>[^`\s]{{3,120}})`\s+{_DONE_VERB}"
    rf"|(?:等|待)\s*`(?P<y>[^`\s]{{3,120}})`\s*(?:存在|上线|完成|就绪|落地|建好)(?:之?后)?",
    re.IGNORECASE)
_NEGATION_BEFORE = re.compile(
    r"(?:\b(?:not|no|never|only|even|but|unless|don't|doesn't|won't|cannot|can't|except)\b|[不勿别未没非仅只])"
    r"[^.;:!?。；：！？]{0,24}$", re.IGNORECASE)
_CONJ_AFTER = re.compile(r"^\s*(?:and|or|but|nor|then)\b|^\s*[且和或并]", re.IGNORECASE)
_DONE_LINE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s")
_REMOVED_WORDS = re.compile(
    r"\b(?:remov|delet|drop|abandon|cancel|revert|deprecat|retir|won't|will not|not)\w*|删除|删掉|放弃|不做|取消|废弃|下线",
    re.IGNORECASE)
_SUPERSEDED_HEAD = re.compile(
    r"^[\s>*_#]*(?:⚠️?\s*)?(?:\*\*)?\s*(?:superseded\b|status\s*:\s*\**\s*superseded\b|已被取代|已取代|已废弃)",
    re.IGNORECASE)
_SUPERSEDED_KEY = re.compile(r"^\s*superseded[_ ]by\s*:\s*(?P<p>\S+)\s*$", re.IGNORECASE)
_PATH = re.compile(r"^[A-Za-z0-9_./\-]+$")
_PATH_IN_LINE = re.compile(r"`([A-Za-z0-9_./\-]+)`|\]\(([A-Za-z0-9_./\-]+)\)")
_VALUE = re.compile(r"^v?\d+(?:\.\d+){0,3}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,80}$")
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
    """A line break other than ``\\n`` / ``\\r\\n``, or an invisible format character (a
    leading BOM and the emoji joiner excepted): line numbers would not agree with the
    markdown readers, or the text would not read as it looks."""
    body = text.lstrip("\ufeff").replace("\r\n", "\n")
    return any(c not in "\n\t\u200d" and unicodedata.category(c) in ("Cc", "Zl", "Zp", "Cf") for c in body)


def _clean(text: str) -> str:
    """Model text going into the log: no control, separator or format characters."""
    return "".join(c for c in str(text) if unicodedata.category(c) not in ("Cc", "Zl", "Zp", "Cf"))[:300]


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


# ─── which lines may be edited or cited ────────────────────────────────────

def _plain_line(text: str, idx: int, *, entries: bool = True) -> str | None:
    """None when line ``idx`` is plain text, else why it is not: front matter, a heading
    (ATX or setext), inside a code block (fenced or indented), metadata, and — for a line
    to be edited (``entries``) — a dated lifecycle entry. A ticked evidence line may look
    like an entry (``- [x] **Title**``); that does not stop it being evidence."""
    lines = text.split("\n")
    line = lines[idx]
    if lines and lines[0].lstrip("\ufeff").strip() == "---":
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), len(lines))
        if idx <= closing:
            return "the line is in the front matter"
    nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
    if line.lstrip().startswith("#") or (line.strip() and re.fullmatch(r"\s*(=+|-+)\s*", nxt)):
        return "the line is a heading"
    if sum(1 for ln in lines[:idx] if ln.lstrip().startswith(("```", "~~~"))) % 2 == 1:
        return "the line is inside a code block"
    prev = lines[idx - 1] if idx else ""
    if line.startswith(("    ", "\t")) and not prev.strip() and not line.lstrip().startswith(("-", "*", "+")):
        return "the line is an indented code block"
    if "<!--" in line or "-->" in line:
        return "the line carries entry metadata"
    if entries:
        for e in parse_entries(text):
            if e.line_number <= idx <= max(e.end_line, e.line_number):
                return "this is a dated record, true when written; not rewritten"
    return None


def _target_line(text: str, needle: str) -> tuple[int, str] | Outcome:
    """The index and text of the one line containing ``needle`` (once in the whole doc),
    when that line may be edited."""
    if len(needle) < _MIN_LINE or "\n" in needle:
        return _open("the quoted line is shorter than a phrase or spans lines")
    if text.count(needle) != 1:
        return _open(f"the quoted text appears {text.count(needle)} times in the document")
    idx = text.count("\n", 0, text.index(needle))
    why = _plain_line(text, idx)
    if why:
        return _open(why)
    return idx, text.split("\n")[idx]


def _names_same(x: str, y: str) -> bool:
    """`x` (in the condition) and `y` (in the ticked line) name the same thing: equal, the
    skill name with its ``s_`` prefix, or a ``/`` / ``.`` qualified form (``pkg/x``,
    ``mod.x``). A longer name merely ending in the other (``legacy_x``) does not count."""
    if x == y:
        return True
    short, long_ = sorted((x, y), key=len)
    if len(short) < 4:
        return False
    return long_ == f"s_{short}" or long_.endswith("/" + short) or long_.endswith("." + short)


def _cjk(c: str) -> bool:
    return bool(c) and ("\u3000" <= c <= "\u9fff" or "\uff00" <= c <= "\uffef")


# ─── the three fixes ───────────────────────────────────────────────────────

def _check_drop_condition(fix: dict, text: str, project_dir: Path) -> tuple[str, str] | Outcome:
    hit = _target_line(text, fix["line"])
    if isinstance(hit, Outcome):
        return hit
    _idx, line = hit
    span = fix["span"]
    m = _CONDITION.fullmatch(span)
    if not m:
        return _open("the span is not a waiting condition the system knows (once / after / 等 / 待 `X` …)")
    if line.count(span) != 1:
        return _open("the span is not in the quoted line exactly once")
    i = line.index(span)
    before, after = line[:i], line[i + len(span):]
    prefix = re.match(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|>\s*)?", line).group(0)
    if len(before) <= len(prefix):
        return _open("the condition starts the sentence; removing it needs rewording")
    if not (before[-1:].isspace() or _cjk(before[-1:])):
        return _open("the condition is joined to other markup or a word")
    if after and not (after[:1].isspace() or after[:1] in ",.;:—–)，。；：）" or _cjk(after[:1])):
        return _open("the condition is joined to other markup or a word")
    if _NEGATION_BEFORE.search(before):
        return _open("a negation or limit comes before the condition; removing it would change the meaning")
    if _CONJ_AFTER.match(after):
        return _open("the condition is joined to another one; removing it needs rewording")
    x = m.group("x") or m.group("y")

    done_line = None
    if "\n" not in fix["done"]:
        for name in DDD_CANONICAL_DOCS:
            body = _read(_confined_doc_path(project_dir, name))
            if body is None or _odd_separators(body) or body.count(fix["done"]) != 1:
                continue
            didx = body.count("\n", 0, body.index(fix["done"]))
            cand = body.split("\n")[didx]
            if _DONE_LINE.match(cand) and _plain_line(body, didx, entries=False) is None:
                done_line = cand
                break
    if done_line is None:
        return _open("the 'done' quote is not a ticked plain line of this project's DDD docs")
    if done_line.strip() == line.strip():
        return _open("a line cannot be its own evidence")
    if _REMOVED_WORDS.search(done_line):
        return _open("the ticked line speaks of removing or not doing it")
    if not any(_names_same(x, y) for y in re.findall(r"`([^`\s]+)`", done_line)):
        return _open(f"the ticked line does not name `{x}`")

    left = before.rstrip(" \t")
    right = after.lstrip(" \t")
    if not right or right[:1] in ",.;:)，。；：）" or _cjk(left[-1:]) or _cjk(right[:1]):
        joined = left + right
    else:
        joined = left + " " + right
    return line, joined


def _check_swap_path(fix: dict, text: str, project_dir: Path, workspace: Path,
                     repo: Path | None) -> tuple[str, str] | Outcome:
    old, new = fix["path_old"], fix["path_new"]
    if not (_PATH.match(old) and _PATH.match(new)) or old == new or ("/" not in new and "." not in new):
        return _open("not two paths")
    bounded = re.compile(rf"(?<![A-Za-z0-9_./\-]){re.escape(old)}(?![A-Za-z0-9_/\-]|\.[A-Za-z0-9_/\-])")
    found = [(i, ln) for i, ln in enumerate(text.split("\n")) if bounded.search(ln)]
    total = sum(len(bounded.findall(ln)) for _i, ln in found)
    if total != 1:
        return _open(f"the path appears {total} times in the document")
    idx, line = found[0]
    why = _plain_line(text, idx)
    if why:
        return _open(why)
    roots = [r for r in (project_dir, workspace, repo) if r is not None]
    homes = [r for r in roots if _under(r, old) is not None]
    if len(homes) != 1:
        return _open(f"{old} is not one file in exactly one of the project, the workspace or the repo")
    root = homes[0]
    head = _read(_under(root, old))
    if head is None:
        return _open(f"{old} could not be read")
    successor = None
    for ln in head.split("\n")[:_HEADER_LINES]:
        key = _SUPERSEDED_KEY.match(ln)
        if key:
            successor = key.group("p").strip("`'\"")
            break
        if _SUPERSEDED_HEAD.match(ln):
            paths = [a or b for a, b in _PATH_IN_LINE.findall(ln)]
            successor = paths[0] if paths else None
            break
    if successor is None:
        return _open(f"{old} does not declare in its header which file supersedes it")
    if successor != new:
        return _open(f"{old}'s header names {successor}, not {new}")
    if _under(root, new) is None:
        return _open(f"{new} does not exist next to {old}")
    return line, bounded.sub(new, line, count=1)


def _git_head_text(repo: Path, rel: str) -> str | None:
    """The committed (HEAD) content of ``rel`` — a staged or working-tree copy does not count."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{rel}"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or len(r.stdout) > _MAX_BYTES:
        return None
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _check_swap_value(fix: dict, text: str, repo: Path | None) -> tuple[str, str] | Outcome:
    ident, vold, vnew, file = fix["identifier"], fix["value_old"], fix["value_new"], fix["code_file"]
    if not _IDENTIFIER.match(ident) or not _VALUE.match(vold) or not _VALUE.match(vnew) or vold == vnew:
        return _open("not an identifier with two numeric values")
    if repo is None:
        return _open("the project has no bound source repo")
    parts = {p.casefold() for p in Path(file).parts}
    if parts & _TEST_PARTS or _TEST_NAME.search(Path(file).name) or Path(file).suffix.lower() not in _CODE_EXT \
            or ".." in parts or Path(file).is_absolute():
        return _open(f"{file} is not a source file (tests, vendored and built files do not count)")
    body = _git_head_text(repo, file)
    if body is None:
        return _open(f"{file} is not a committed file of the bound repo")
    decl = re.compile(
        rf"^\s*(?:export\s+)?(?:(?:const|let|var|final|static|public|private|protected|readonly|val)\s+)*"
        rf"(?:[A-Za-z_][\w<>\[\]]*\s+)?{re.escape(ident)}\s*(?::\s*[\w\[\]<>.]+\s*)?=\s*"
        rf"[\"']?(?P<v>v?\d+(?:\.\d+){{0,3}})[\"']?\s*(?:[;,]|#|//|$)", re.MULTILINE)
    # a comment line or a call argument cannot match: the identifier must open the line
    # (after declaration keywords), and a comment marker is not one of them
    values = [mm.group("v") for mm in decl.finditer(body)]
    if len(values) != 1:
        return _open(f"{file} does not declare {ident} exactly once with a number")
    if values[0].lstrip("v") != vnew.lstrip("v"):
        return _open(f"{file} declares {ident} = {values[0]}, not {vnew}")
    hit = _target_line(text, fix["line"])
    if isinstance(hit, Outcome):
        return hit
    _idx, line = hit
    stated = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(ident)}`?\s*(?:=|:|\bis\b|\bof\b|\bto\b|\bat\b)?\s*`?"
        rf"(?P<v>(?<![\w.\-,]){re.escape(vold)}(?![\w]|[.,\-]\d))")
    hits = list(stated.finditer(line))
    if len(hits) != 1:
        return _open(f"the line does not state {ident} as {vold} exactly once")
    s, e = hits[0].span("v")
    shown = ("v" + vnew.lstrip("v")) if vold.startswith("v") else vnew.lstrip("v")
    return line, line[:s] + shown + line[e:]


def _key(fix: dict) -> str:
    """What a fix does, independent of the rest of its line: used to recognise a fix the
    user undid even after other words on that line changed."""
    if fix["type"] == "drop_condition":
        m = _CONDITION.fullmatch(fix["span"])
        what = f"{fix['span']}|{(m.group('x') or m.group('y')) if m else ''}"
    elif fix["type"] == "swap_superseded_path":
        what = f"{fix['path_old']}>{fix['path_new']}"
    else:
        what = f"{fix['identifier']}={fix['value_new']}"
    return f"{fix['type']}:{fix['doc']}:{what}"


# ─── the log ───────────────────────────────────────────────────────────────

def _log_rows(project_dir: Path) -> list[dict]:
    out = []
    try:
        raw = (project_dir / ".artifacts" / FIX_LOG).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.split("\n"):  # rows are written ASCII-escaped: "\n" is the only separator
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def past_fixes(project_dir: Path) -> list[dict]:
    """Every fix that was written (a ``failed`` write is dropped), in log order, each with
    ``index`` (its stable position among applied rows) and ``undone`` (an undo was
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
    """Is this fix one of the three provable fixes? Pure over what it reads; never writes.
    On success carries the line as it is and as it will be."""
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

    key = _key(fix)
    for row in past_fixes(project_dir):
        if row.get("target_doc") != fix["doc"]:
            continue
        if row["undone"] and row.get("key") == key:
            return _open("you undid this fix before; not applied again")
        if not row["undone"] and _ws(row.get("old_line", "")) == _ws(new_line):
            return _open("this would undo an earlier automatic fix")
    return Outcome(ok=True, reason="certain", doc_path=str(doc), mtime_ns=st.st_mtime_ns, size=st.st_size,
                   old_line=old_line, new_line=new_line, key=key)


# ─── writing ───────────────────────────────────────────────────────────────

def _backup_path(project_dir: Path, doc_name: str, run_id: str) -> Path:
    return project_dir / ".artifacts" / BACKUP_DIR / f"{Path(doc_name).stem}.{run_id}.md"


def _backup(project_dir: Path, doc: Path, run_id: str) -> None:
    """One whole-doc copy per doc per run. Keeps the oldest (before any auto-fix) and the
    newest BACKUPS_KEPT - 1."""
    target = _backup_path(project_dir, doc.name, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(doc.read_bytes())
    mine = sorted(target.parent.glob(f"{doc.stem}.*.md"), key=lambda p: p.stat().st_mtime)
    for stale in mine[1:-(BACKUPS_KEPT - 1)] if len(mine) > BACKUPS_KEPT else []:
        try:
            stale.unlink()
        except OSError:
            pass


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
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
        f.write(json.dumps(row) + "\n")  # ASCII-escaped: "\n" is the only line break in the file


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fix_id(run_id: str, doc: str, old_line: str, new_line: str) -> str:
    digest = hashlib.sha1(f"{old_line}\x00{new_line}".encode("utf-8")).hexdigest()[:12]
    return f"{run_id}:{doc}:{digest}"


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
        fix_id = _fix_id(run_id, fix["doc"], old_line, new_line)
        try:
            _backup(project_dir, doc, run_id)
            _append_log(project_dir, {"fix_id": fix_id, "status": "applied", "type": fix["type"], "run": run_id,
                                      "key": again["key"], "target_doc": fix["doc"], "old_line": old_line,
                                      "new_line": new_line,
                                      "evidence": {k: _clean(v) for k, v in fix.items() if k not in ("type", "doc")},
                                      "finding": _clean(finding_title), "timestamp": _now()})
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
                _append_log(project_dir, {"fix_id": fix_id, "status": "failed", "reason": _clean(e), "timestamp": _now()})
            except OSError:
                pass
            return _open(f"the fix was not written: {str(e)[:120]}; nothing changed")
    return Outcome(ok=True, reason="fixed", fix_id=fix_id, old_line=old_line, new_line=new_line)


def undo_drift_fix(project_dir: Path, index: int) -> Outcome:
    """Put one fix back (``index`` as listed by ``past_fixes``). The row must be genuine —
    its id matches its lines and the run's backup holds the old line — and the new line
    must still be in the doc exactly once. The undo is recorded BEFORE the doc changes,
    so the self-audit never redoes it."""
    from utils.file_lock import md_lock

    row = {r["index"]: r for r in past_fixes(project_dir)}.get(index)
    if row is None:
        return _open("no such fix")
    if row["undone"]:
        return _open("already undone")
    doc_name = str(row.get("target_doc", ""))
    new, old = str(row.get("new_line", "")), str(row.get("old_line", ""))
    if doc_name not in DDD_CANONICAL_DOCS or "\n" in old or "\n" in new or _odd_separators(old) \
            or row.get("fix_id") != _fix_id(str(row.get("run", "")), doc_name, old, new):
        return _open("the log row does not look like a fix this system wrote; not undone")
    backup = _read(_backup_path(project_dir, doc_name, str(row.get("run", ""))))
    if backup is None or old not in backup.split("\n"):
        return _open("the run's backup does not hold the original line; not undone")
    doc = _confined_doc_path(project_dir, doc_name)
    if doc is None:
        return _open("the document is gone")
    with md_lock(doc, blocking=True):
        try:
            lines = doc.read_text(encoding="utf-8").split("\n")
        except OSError:
            return _open("the document could not be read")
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
