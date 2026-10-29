"""DDD drift auto-fix — three narrow fixes the OS can prove, nothing else.

Design: docs/design/2026-10-27-ddd-drift-auto-fix.md (r5). The weekly self-audit agent
(jobs/handlers/ddd_self_audit.py) can only read. For a finding it may name one of three
fixes; it never writes the new text — this module computes it from the document and the
evidence, and only when a fixed grammar matches:

``drop_condition``   {"doc", "line", "span", "done"}
    Delete a waiting condition — "once `X` exists|lands|ships|is built…", "等/待 `X`
    完成后 …" — from one line, when ``done`` is a ticked ``- [x]`` line of this project's
    DDD docs that says `X` was made (built / shipped / ported …), names it (exactly, as the
    skill ``s_X``, or qualified by a ``/`` or ``.`` prefix that is not a test or demo
    path), and whose item says nothing about removing or postponing it. The words around
    the span may not negate, limit or join it to another condition, and nothing but plain
    text may touch it.
``swap_superseded_path``   {"doc", "path_old", "path_new"}
    Replace the one occurrence of a path with the FIRST path named on the SUPERSEDED line
    (or the ``superseded_by:`` key) of that file's own header; ``path_old`` must resolve
    in exactly one root, and ``path_new`` in the same one.
``swap_code_value``   {"doc", "line", "identifier", "value_old", "value_new", "code_file"}
    Replace the value the line states for ``identifier`` (it follows the identifier) with
    the value the committed (HEAD) source file declares for it — exactly one top-level
    declaration (a call argument, docstring or block comment does not count).

Every fix stays inside one plain line of a canonical DDD doc (not front matter, a
heading, a code block or metadata; the doc has no unusual separators), never re-proposes
a fix the user undid, and never undoes an earlier fix. A dated or baseline unit may be
targeted because CDF appends a complete superseding unit; its existing bytes stay intact.
No free text from the model reaches a document.

``apply_fix`` re-checks the grammar and returns the computed replacement line. It
never writes the DDD. ``ddd_self_audit`` wraps that result in a complete S4 Cognition
unit whose relation hint supersedes the old entry. The historical
``.artifacts/ddd-drift-fixes.jsonl`` remains read-only input: when it says a user
previously undid the same fix, the grammar refuses to propose it again.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from core.ddd_paths import confined_doc_path
from core.ddd_entry_lifecycle import parse_entries
from core.project_registry import DDD_CANONICAL_DOCS

FIX_LOG = "ddd-drift-fixes.jsonl"
FIX_TYPES = ("drop_condition", "swap_superseded_path", "swap_code_value")
_FIELDS = {
    "drop_condition": ("doc", "line", "span", "done"),
    "swap_superseded_path": ("doc", "path_old", "path_new"),
    "swap_code_value": ("doc", "line", "identifier", "value_old", "value_new", "code_file"),
}
_MIN_LINE = 12
_MAX_BYTES = 2_000_000
_HEADER_LINES = 10

# once only: "until" is the opposite wait, "when" is usually a trigger, and "after" is
# also a time anchor ("two weeks after `X` ships") whose limit would be lost
_DONE_VERB = r"(?:exists|lands|ships|is\s+(?:built|done|ready|available|implemented|shipped|merged))"
_CONDITION = re.compile(
    rf"once\s+`(?P<x>[^`\s]{{3,120}})`\s+{_DONE_VERB}"
    rf"|(?:等|待)\s*`(?P<y>[^`\s]{{3,120}})`\s*(?:存在|上线|完成|就绪|落地|建好)(?:之?后)?",
    re.IGNORECASE)
_NEGATION_BEFORE = re.compile(
    r"(?:\b(?:not|no|never|only|even|but|unless|don't|doesn't|won't|cannot|can't|except)\b|[不勿别未没非仅只])"
    r"[^.;:!?。；：！？]{0,24}$", re.IGNORECASE)
_CONJ_AFTER = re.compile(r"^\s*(?:and|or|but|nor|then)\b|^\s*[且和或并]", re.IGNORECASE)
_CONJ_BEFORE = re.compile(r"(?:\b(?:and|or|nor|but|then|either)\b,?|[且和或并及])\s*$", re.IGNORECASE)
_SEP_END = ",;:—–-(（，；："
_SEP_START = ",.;:—–-)），。；："
_DONE_LINE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s")
_REMOVED_WORDS = re.compile(
    r"\b(?:remov(?:e|ed|es|ing|al)|delet(?:e|ed|es|ing|ion)|drop(?:s|ped|ping)?|abandon(?:s|ed|ing)?"
    r"|cancel(?:s|led|ed|ling|ing)?|revert(?:s|ed|ing)?|deprecat(?:e|ed|es|ing)|retir(?:e|ed|es|ing)"
    r"|postpon(?:e|ed|es|ing)|defer(?:s|red|ring)?|won't|will not|not)\b"
    r"|删除|删掉|放弃|不做|取消|废弃|下线|推迟|延后|暂缓",
    re.IGNORECASE)
# the ticked line must say the thing was made: an allowlist, a denylist alone fails open
_MADE_WORDS = re.compile(
    r"\b(?:built|shipped|landed|implemented|merged|added|ported|created|released|deployed|wired|registered|wrote|written"
    r"|integrated|completed|finished|exists|live)\b|完成|上线|落地|建好|已建|已实现|实现了|合入|已加",
    re.IGNORECASE)
_NOT_REAL = {"mock", "mocks", "demo", "demos", "stub", "stubs", "example", "examples", "sample", "samples", "fake"}
_FENCE = re.compile(r"^(?P<ind>\s*)(?P<f>`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
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
        return path.read_bytes().decode("utf-8")  # bytes: read_text would turn CRLF into LF
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

def _hidden_lines(lines: list[str]) -> dict[int, str]:
    """Lines that do not render as plain text, with why: inside a fenced block (closed only
    by the same character, at least as long, CommonMark-style), inside or on an HTML comment
    (tracked across lines), or an indented code block (4+ spaces after a blank line, not
    inside a list item)."""
    hidden: dict[int, str] = {}
    fence = None  # (char, length, indent)
    in_comment = False
    last = None  # kind of the last non-blank plain line: "list", "text", "code"
    blank_before = True
    for i, ln in enumerate(lines):
        m = _FENCE.match(ln)
        if fence:
            hidden[i] = "the line is inside a code block"
            if m and m.group("f")[0] == fence[0] and len(m.group("f")) >= fence[1] \
                    and len(m.group("ind")) <= fence[2] + 3 and not ln.strip().strip(fence[0]):
                fence, last = None, "text"
            continue
        if m:
            fence = (m.group("f")[0], len(m.group("f")), len(m.group("ind")))
            hidden[i] = "the line is inside a code block"
            continue
        if in_comment or "<!--" in ln or "-->" in ln:
            hidden[i] = "the line carries entry metadata"
            o, c = ln.rfind("<!--"), ln.rfind("-->")
            if o > c:
                in_comment = True
            elif c > o:
                in_comment = False
            continue
        if not ln.strip():
            blank_before = True
            continue
        if ln.startswith(("    ", "\t")):
            if not blank_before:
                kind = last or "code"
            else:
                kind = "list" if last == "list" else "code"
        else:
            kind = "list" if _LIST_ITEM.match(ln) else "text"
        if kind == "code":
            hidden[i] = "the line is an indented code block"
        last, blank_before = kind, False
    return hidden


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
    why = _hidden_lines(lines).get(idx)
    if why:
        return why
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
    why = _plain_line(text, idx, entries=False)
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
    if long_ == f"s_{short}":
        return True
    if not (long_.endswith("/" + short) or long_.endswith("." + short)):
        return False
    qualifier = {p.casefold() for p in re.split(r"[/.]", long_[:-len(short) - 1])}
    return not qualifier & (_TEST_PARTS | _NOT_REAL)


def _cjk(c: str) -> bool:
    return bool(c) and ("\u3000" <= c <= "\u9fff" or "\uff00" <= c <= "\uffef")


# ─── the three fixes ───────────────────────────────────────────────────────

def _check_drop_condition(fix: dict, text: str, project_dir: Path) -> tuple[str, str] | Outcome:
    hit = _target_line(text, fix["line"])
    if isinstance(hit, Outcome):
        return hit
    _idx, full = hit
    cr = "\r" if full.endswith("\r") else ""
    line = full[:-1] if cr else full
    span = fix["span"]
    m = _CONDITION.fullmatch(span)
    if not m:
        return _open("the span is not a waiting condition the system knows (once / after / 等 / 待 `X` …)")
    if line.count(span) != 1:
        return _open("the span is not in the quoted line exactly once")
    i = line.index(span)
    before, after = line[:i], line[i + len(span):]
    prefix = re.match(r"^\s*(?:>\s*)*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)?", line).group(0)
    if len(before) <= len(prefix):
        return _open("the condition starts the sentence; removing it needs rewording")
    if not (before[-1:].isspace() or _cjk(before[-1:])):
        return _open("the condition is joined to other markup or a word")
    if after and not (after[:1].isspace() or after[:1] in ",.;:—–)，。；：）" or _cjk(after[:1])):
        return _open("the condition is joined to other markup or a word")
    if _NEGATION_BEFORE.search(before):
        return _open("a negation or limit comes before the condition; removing it would change the meaning")
    if _CONJ_AFTER.match(after) or _CONJ_BEFORE.search(before):
        return _open("the condition is joined to another one; removing it needs rewording")
    if len(_CONDITION.findall(line)) != 1:
        return _open("the line has more than one waiting condition; removing one needs rewording")
    x = m.group("x") or m.group("y")

    done_line, rest = None, []
    if "\n" not in fix["done"]:
        for name in DDD_CANONICAL_DOCS:
            body = _read(confined_doc_path(project_dir, name))
            if body is None or _odd_separators(body) or body.count(fix["done"]) != 1:
                continue
            didx = body.count("\n", 0, body.index(fix["done"]))
            cand = body.split("\n")[didx]
            if _DONE_LINE.match(cand) and _plain_line(body, didx, entries=False) is None:
                done_line = cand
                indent = len(cand) - len(cand.lstrip())
                for nxt in body.split("\n")[didx + 1:]:  # the item's continuation lines
                    if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent:
                        break
                    rest.append(nxt)
                break
    if done_line is None:
        return _open("the 'done' quote is not a ticked plain line of this project's DDD docs")
    if done_line.strip() == line.strip():
        return _open("a line cannot be its own evidence")
    # a word inside a hyphenated term ("authz-in-backend-not-prose") is not a statement
    if any(_REMOVED_WORDS.search(re.sub(r"(?<=\w-)[A-Za-z']+(?=-\w)", "", t)) for t in [done_line, *rest]):
        return _open("the ticked item speaks of removing, postponing or not doing it")
    if not _MADE_WORDS.search(done_line):
        return _open("the ticked line does not say it was built, shipped or added")
    if not any(_names_same(x, y) for y in re.findall(r"`([^`\s]+)`", done_line)):
        return _open(f"the ticked line does not name `{x}`")

    left = before.rstrip(" \t")
    right = after.lstrip(" \t")
    if left[-1:] in _SEP_END and (not right or right[:1] in _SEP_START):
        return _open("removing the condition would leave its punctuation dangling; needs rewording")
    if not right or right[:1] in ",.;:)，。；：）" or _cjk(left[-1:]) or _cjk(right[:1]):
        joined = left + right
    else:
        joined = left + " " + right
    return full, joined + cr


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
    why = _plain_line(text, idx, entries=False)
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
    kw = r"(?:const|let|var|final|static|public|private|protected|readonly|val|internal)"
    decl = re.compile(
        rf"^(?:(?:export\s+)?(?:{kw}\s+)*|[ \t]+(?:export\s+)?(?:{kw}\s+)+)"
        rf"(?:[A-Za-z_][\w<>\[\]]*\s+)?{re.escape(ident)}\s*(?::\s*[\w\[\]<>.]+\s*)?=\s*"
        rf"[\"']?(?P<v>v?\d+(?:\.\d+){{0,3}})[\"']?\s*(?:;|#|//|$)", re.MULTILINE)
    # A declaration opens a line at top level, or indented only after a declaration keyword
    # (a class constant); "," does not end one. A keyword argument of a call split across
    # lines (the line before ends in "(" or ","), a docstring and a block comment never count.
    quoted = [mm.span() for mm in re.finditer(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|/\*[\s\S]*?\*/', body)]
    values = []
    for mm in decl.finditer(body):
        if any(a <= mm.start() < b for a, b in quoted):
            continue
        prev = next((ln for ln in reversed(body[:mm.start()].split("\n")[:-1]) if ln.strip()), "")
        if prev.rstrip().endswith(("(", ",")):
            continue
        values.append(mm.group("v"))
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


def _rid(row: dict) -> object:
    """Which applied row a row is about: its ``seq`` (each applied row has its own)."""
    return row.get("seq") or row.get("fix_id")


def past_fixes(project_dir: Path) -> list[dict]:
    """Every fix that was written (a ``failed`` write is dropped), in log order, each with
    ``index`` (its stable position among applied rows) and ``undone`` (an undo was
    recorded and not followed by a failed undo)."""
    rows = _log_rows(project_dir)
    failed = {_rid(r) for r in rows if r.get("status") == "failed"}
    undone: set = set()
    for r in rows:
        if r.get("status") == "undone":
            undone.add(_rid(r))
        elif r.get("status") == "undo_failed":
            undone.discard(_rid(r))
    applied = [r for r in rows if r.get("status") == "applied"]
    return [{**r, "index": i, "undone": _rid(r) in undone}
            for i, r in enumerate(applied) if _rid(r) not in failed]


def _ws(s: str) -> str:
    return " ".join((s or "").split())


def check_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None) -> Outcome:
    """Is this fix one of the three provable fixes? Pure over what it reads; never writes.
    On success carries the line as it is and as it will be."""
    if fix.get("type") not in FIX_TYPES:
        return _open("not one of the three fixes the system can prove")
    if fix["doc"] not in DDD_CANONICAL_DOCS:
        return _open(f"not a DDD document: {fix['doc']}")
    doc = confined_doc_path(project_dir, fix["doc"])
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


# ─── computed replacement (no write) ─────────────────────────────────────────

def apply_fix(fix: dict, project_dir: Path, workspace: Path, source_repo: Path | None,
              *, run_id: str = "", finding_title: str = "", dry_run: bool = False) -> Outcome:
    """Return the deterministic replacement line; never modify any file.

    ``run_id``, ``finding_title`` and ``dry_run`` remain descriptive inputs for the
    scheduled caller. CDF owns persistence and supersession.
    """
    _ = (run_id, finding_title, dry_run)
    checked = check_fix(fix, project_dir, workspace, source_repo)
    if not checked.get("ok"):
        return checked
    return Outcome(
        ok=True, reason="computed",
        doc_path=checked["doc_path"], old_line=checked["old_line"],
        new_line=checked["new_line"], key=checked["key"], fix_type=fix["type"],
    )


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
           "past_fixes"]
