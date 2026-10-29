"""ddd_format — is a DDD doc in the shape CDF writes, and the lossless move into it.

The shape (CDF's writer, ``core.ingestion.writer._render_ddd`` through
``ddd_entry_lifecycle.render_ddd_unit``; the sections and types of
``ddd_skeleton.SECTION_TYPES``):

- a doc holds only the skeleton's ``##`` sections, in the skeleton's order;
- a section holds entries ``- [type] **Title** …`` each followed by its metadata line
  ``<!-- ref:N | last:… | decay:… -->``, and guidance comments; the entry's type is
  one CDF may route into that section;
- the user-only sections (PROJECT § Current Focus / Blocked By) hold whatever the
  user writes.

Anything else in a doc — a section of its own, a paragraph, a table, a ``###`` block,
a bullet with no bold title — is reference material, not an entry. Its home is the
Brain's deep reference, ``2-understanding/knowledge/``, which the DDD recall leg and
``knowledge find`` read as well.

``check(doc, text)`` lists what is out of shape. ``normalize(doc, text, brain=…)``
returns the doc in shape plus the reference material it moved out, and loses nothing:

- an entry keeps its bytes; an untyped one gets the type the lifecycle already reads
  it as (``classify_entry_type``) when CDF may put that type in its section, else the
  section's own type; one with no metadata line gets one (dated by the entry's own
  date when it has one); one whose type CDF never routes to its section moves to the
  section CDF routes that type to, in the same doc;
- a typed entry in a section of its own moves the same way; everything else in such a
  section (its untyped bullets included: their kind cannot be told from the doc) moves,
  verbatim and in order, to ``knowledge/<doc>/<section>.md``;
- non-entry lines of a skeleton section move to ``knowledge/<doc>/<section>-notes.md``.

Running it again changes nothing. ``artifact_cli ddd-format`` is the command; the P6
gate (``verify_ddd_complete``) reports ``check`` as "② Knowledge/format".
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from core.ddd_entry_lifecycle import _ENTRY_RE, _META_RE, _scan_entry, VALID_TYPES, classify_entry_type
from core.ddd_skeleton import (USER_ONLY_SECTIONS, allowed_types, default_type, home_section,
                               load_skeleton, normalize_heading, skeleton_sections)

_H2 = re.compile(r"^## (?!#)(.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TYPED = re.compile(r"^- \[(\w+)\] \*\*")
_DATE = re.compile(r"\((\d{4}-\d{2}-\d{2})")


@dataclass
class Problem:
    line: int          # 1-based
    kind: str          # extra_section | not_an_entry | untyped | no_meta | wrong_type | order
    detail: str


@dataclass
class _Section:
    heading: Optional[str]                       # None: the text before the first ##
    lines: list[str] = field(default_factory=list)
    start: int = 0                               # 0-based index of the heading line


def _sections(text: str) -> list[_Section]:
    out = [_Section(None)]
    in_fence = False
    for i, ln in enumerate(text.splitlines()):
        if _FENCE.match(ln):
            in_fence = not in_fence
        m = None if in_fence else _H2.match(ln)
        if m:
            out.append(_Section(m.group(1).strip(), [], i))
        else:
            out[-1].lines.append(ln)
    return out


def _canonical(doc: str, heading: str) -> Optional[str]:
    key = normalize_heading(heading)
    for s in skeleton_sections(doc):
        if normalize_heading(s) == key:
            return s
    return None


def _is_comment_start(ln: str) -> bool:
    return ln.lstrip().startswith("<!--") and not _META_RE.match(ln)


@dataclass
class _Block:
    kind: str            # entry | comment | other | blank
    lines: list[str]
    start: int           # index into the section's lines


def _blocks(lines: list[str]) -> list[_Block]:
    """A section's lines as entries (read by ``_scan_entry``, the one reading of an
    entry), comments, blanks and everything else (kept in order). A fence is one
    block: a ``- `` inside it is not an entry."""
    out: list[_Block] = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            out.append(_Block("blank", [ln], i))
            i += 1
        elif _FENCE.match(ln):
            j = i + 1
            while j < n and not _FENCE.match(lines[j]):
                j += 1
            j = min(j + 1, n)
            out.append(_Block("other", lines[i:j], i))
            i = j
        elif _is_comment_start(ln):
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            j = min(j + 1, n)
            out.append(_Block("comment", lines[i:j], i))
            i = j
        elif ln.startswith("- "):
            _text_end, _meta, end = _scan_entry(lines, i)
            end = _close_fences(lines, i, max(end, i + 1))
            out.append(_Block("entry" if _ENTRY_RE.match(ln) else "other", lines[i:end], i))
            i = end
        else:
            j = i + 1
            while (j < n and lines[j].strip() and not lines[j].startswith("- ")
                   and not _FENCE.match(lines[j]) and not _is_comment_start(lines[j])):
                j += 1
            out.append(_Block("other", lines[i:j], i))
            i = j
    return out


def _close_fences(lines: list[str], start: int, end: int) -> int:
    """*end*, moved past the closing line of a code fence ``lines[start:end]`` opens and
    does not close (``_scan_entry`` ends an entry at a blank line, also one inside a
    fence), then past the continuation, metadata line and indented tail that follow."""
    n = len(lines)
    while end < n and sum(1 for ln in lines[start:end] if _FENCE.match(ln)) % 2:
        j = end
        while j < n and not _FENCE.match(lines[j]):
            j += 1
        end = min(j + 1, n)
        while end < n and lines[end].strip() and not lines[end].startswith("- ") and not _H2.match(lines[end]):
            end += 1
        k = end
        while k < n and not lines[k].strip():
            k += 1
        if k < n and _META_RE.match(lines[k]):
            end = k + 1
            while end < n and lines[end].strip() and lines[end][0] in " \t" and not _META_RE.match(lines[end]):
                end += 1
    return end


def _has_meta(block: _Block) -> bool:
    return any(_META_RE.match(ln) for ln in block.lines[1:])


def _entry_type(block: _Block) -> Optional[str]:
    m = _TYPED.match(block.lines[0])
    return m.group(1) if m else None


def check(doc: str, text: str) -> list[Problem]:
    """What in *text* (the doc named *doc*) is not in the shape CDF writes."""
    out: list[Problem] = []
    secs = _sections(text)
    seen: list[str] = []
    for sec in secs:
        canon = _canonical(doc, sec.heading) if sec.heading is not None else None
        if sec.heading is not None and canon is None:
            out.append(Problem(sec.start + 1, "extra_section", f"## {sec.heading} is not a section of {doc}"))
            continue
        if canon:
            seen.append(canon)
        if canon in USER_ONLY_SECTIONS.get(doc, ()):
            continue
        base = sec.start + 1 if sec.heading is not None else 0
        allowed = allowed_types(doc, canon) if canon else None
        for b in _blocks(sec.lines):
            line = base + b.start + 1
            if b.kind in ("blank", "comment"):
                continue
            if sec.heading is None:
                if b.kind == "other" and len(b.lines) == 1 and b.lines[0].startswith("# "):
                    continue
                out.append(Problem(line, "not_an_entry", "text before the first section"))
                continue
            if b.kind == "other":
                out.append(Problem(line, "not_an_entry", f"§ {canon}: {b.lines[0][:60]!r} is not an entry"))
                continue
            etype = _entry_type(b)
            if etype is None:
                out.append(Problem(line, "untyped", f"§ {canon}: entry has no [type]"))
            elif etype not in VALID_TYPES:
                out.append(Problem(line, "wrong_type", f"§ {canon}: [{etype}] is not a type"))
            elif allowed is not None and etype not in allowed:
                out.append(Problem(line, "wrong_type", f"§ {canon}: CDF never writes a [{etype}] here"))
            if not _has_meta(b):
                out.append(Problem(line, "no_meta", f"§ {canon}: entry has no metadata line"))
    order = [s for s in skeleton_sections(doc) if s in seen]
    if seen != order:
        out.append(Problem(1, "order", f"sections out of the skeleton's order: {', '.join(seen)}"))
    return out


def _slug(heading: str) -> str:
    s = re.sub(r"[^\w]+", "-", heading.casefold()).strip("-")
    return (s[:60].rstrip("-") or "section")


_OLD_META = re.compile(r"^\s*<!-- ref:(\d+) \| last:([\w\-]+)")


def _meta_line(block: _Block, today: str) -> str:
    """The metadata line an entry without one gets: its count and date from a metadata
    comment the lifecycle cannot read (an unknown field), else ref 0 and the entry's own
    date, else today."""
    for ln in block.lines[1:]:
        old = _OLD_META.match(ln)
        if old:
            return f"  <!-- ref:{old.group(1)} | last:{old.group(2)} | decay:active | source:manual -->"
    m = _DATE.search(block.lines[0])
    when = m.group(1) if m else today
    try:
        date.fromisoformat(when)
    except ValueError:
        when = today
    return f"  <!-- ref:0 | last:{when} | decay:active | source:manual -->"


def _typed_entry(doc: str, canon: str, block: _Block, today: str) -> tuple[str, list[str]]:
    """``(home section, lines)`` of an entry in skeleton section *canon*: typed, with a
    metadata line, and the section CDF routes its type to."""
    lines = list(block.lines)
    while lines and not lines[-1].strip():
        lines.pop()
    etype = _entry_type(block)
    if etype is None or etype not in VALID_TYPES:
        allowed = allowed_types(doc, canon) or frozenset(VALID_TYPES)
        guess = classify_entry_type("\n".join(lines))
        new = guess if guess in allowed else default_type(doc, canon)
        lead = lines[0]
        if etype is None:
            lines[0] = f"- [{new}] " + lead[2:]
        else:
            # "- [x] **T** …" (a checkbox, a made-up type): the word stays, after the title
            m = re.match(r"^- \[\w+\] (\*\*.+?\*\*)(.*)$", lead)
            lines[0] = f"- [{new}] {m.group(1)} ({etype}){m.group(2)}"
        etype = new
    if not _has_meta(_Block("entry", lines, 0)):
        lines.append(_meta_line(block, today))
    allowed = allowed_types(doc, canon)
    home = canon if (allowed is None or etype in allowed) else home_section(doc, etype)
    return home, lines


@dataclass
class Result:
    text: str                                    # the doc in shape
    moved: dict[str, str]                        # knowledge-relative path → markdown to add
    typed: int = 0
    meta_added: int = 0
    relocated: int = 0
    moved_blocks: int = 0


def normalize(doc: str, text: str, *, brain: str, today: Optional[str] = None) -> Result:
    """*text* (doc *doc* of Brain *brain*) in the shape CDF writes; see the module doc."""
    today = today or date.today().isoformat()
    stem = doc.removesuffix(".md").lower()
    skeleton = {s: [] for s in skeleton_sections(doc)}           # section → entry line lists
    comments: dict[str, list[list[str]]] = {s: [] for s in skeleton}
    user_text: dict[str, list[str]] = {}
    moved: dict[str, list[str]] = {}
    res = Result("", {})
    preamble_title, preamble_comments = None, []

    def _move(rel: str, title: str, lines: list[str]) -> None:
        if all(not ln.strip() or ln.startswith("## ") for ln in lines):
            return                                   # nothing but a heading: nothing to move
        body = list(lines)
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        if not body:
            return
        if rel not in moved:
            moved[rel] = [f"# {brain} — {title}", "",
                          f"<!-- Moved out of {doc} on {today} (ddd-format): reference material, "
                          "not entries. -->", ""]
        else:
            moved[rel].append("")
        moved[rel].extend(body)
        res.moved_blocks += 1

    for sec in _sections(text):
        if sec.heading is None:
            rest: list[str] = []
            for b in _blocks(sec.lines):
                if b.kind == "other" and len(b.lines) == 1 and b.lines[0].startswith("# ") and preamble_title is None:
                    preamble_title = b.lines[0]
                elif b.kind == "comment":
                    preamble_comments.append(b.lines)
                else:
                    rest.extend(b.lines)
            _move(f"{stem}/overview.md", f"{doc} overview", rest)
            continue
        canon = _canonical(doc, sec.heading)
        if canon in USER_ONLY_SECTIONS.get(doc, ()):
            user_text.setdefault(canon, []).extend(sec.lines)
            continue
        if canon is None:
            rest = []
            for b in _blocks(sec.lines):
                etype = _entry_type(b) if b.kind == "entry" else None
                if etype in VALID_TYPES:
                    home = home_section(doc, etype)
                    _h, lines = _typed_entry(doc, home, b, today)
                    skeleton[home].append(lines)
                    res.relocated += 1
                    res.meta_added += int(not _has_meta(b))
                else:
                    rest.extend(b.lines)
            while rest and not rest[0].strip():
                rest.pop(0)
            _move(f"{stem}/{_slug(sec.heading)}.md", sec.heading, [f"## {sec.heading}", ""] + rest)
            continue
        rest = []
        for b in _blocks(sec.lines):
            if b.kind == "blank":
                continue
            if b.kind == "comment":
                comments[canon].append(b.lines)
            elif b.kind == "entry":
                had_type = _entry_type(b) in VALID_TYPES
                home, lines = _typed_entry(doc, canon, b, today)
                skeleton[home].append(lines)
                res.typed += int(not had_type)
                res.meta_added += int(not _has_meta(b))
                res.relocated += int(home != canon)
            else:
                rest.extend(b.lines + [""])
        _move(f"{stem}/{_slug(canon)}-notes.md", f"{canon} (notes)", rest)

    # guidance comments of a section that had none come from the skeleton
    sk = _sections(load_skeleton()[doc].replace("{project_name}", brain))
    sk_comments = {s.heading: [b.lines for b in _blocks(s.lines) if b.kind == "comment"]
                   for s in sk if s.heading}
    out: list[str] = [preamble_title or (sk[0].lines[0] if sk[0].lines else f"# {brain}")]
    for c in (preamble_comments or [b.lines for b in _blocks(sk[0].lines) if b.kind == "comment"]):
        out += [""] + c
    for s in skeleton_sections(doc):
        out += ["", f"## {s}"]
        for c in comments.get(s) or sk_comments.get(s, []):
            out += [""] + c
        if s in USER_ONLY_SECTIONS.get(doc, ()):
            body = list(user_text.get(s, []))
            while body and not body[0].strip():
                body.pop(0)
            while body and not body[-1].strip():
                body.pop()
            if body:
                out += [""] + body
            continue
        for lines in skeleton[s]:
            out += [""] + lines
    res.text = "\n".join(out).rstrip("\n") + "\n"
    res.moved = {rel: "\n".join(v).rstrip("\n") + "\n" for rel, v in moved.items()}
    return res


def project_report(project_dir: Path) -> dict[str, list[Problem]]:
    """``check`` of each of the Brain's four docs that exists."""
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    out = {}
    for doc in DDD_CANONICAL_DOCS:
        p = ddd_path(project_dir, doc)
        if p.is_file():
            out[doc] = check(doc, p.read_text(encoding="utf-8"))
    return out


def apply_project(project_dir: Path, *, apply: bool = False, today: Optional[str] = None) -> dict:
    """Normalize the Brain's four docs. Without *apply*, report only. With it, the
    reference files are written first (appended to when one is there), then each doc
    through the journaled governed edit (archive-first, so the CDF baseline follows)."""
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    project_dir = Path(project_dir)
    kdir = ddd_path(project_dir, "knowledge")
    report: dict = {"project": project_dir.name, "applied": apply, "docs": {}}
    for doc in DDD_CANONICAL_DOCS:
        path = ddd_path(project_dir, doc)
        if not path.is_file():
            report["docs"][doc] = {"exists": False}
            continue
        raw = path.read_bytes()
        rev = hashlib.sha256(raw).hexdigest()      # governed_edit.revision_of of these bytes
        text = raw.decode("utf-8")
        before = check(doc, text)
        res = normalize(doc, text, brain=project_dir.name, today=today)
        changed = res.text != text
        report["docs"][doc] = {
            "exists": True, "problems": len(before), "changed": changed,
            "typed": res.typed, "meta_added": res.meta_added, "relocated": res.relocated,
            "moved_blocks": res.moved_blocks,
            "moved_to": sorted(f"knowledge/{rel}" for rel in res.moved),
            "after": [f"{p.line}: {p.detail}" for p in check(doc, res.text)]}
        if not apply or not changed:
            continue
        try:
            for rel, body in res.moved.items():
                _append_moved(kdir / rel, body)
            from core.ingestion.governed_edit import apply_user_edit
            apply_user_edit(path, res.text.encode("utf-8"), expected_revision=rev)
        except Exception as exc:  # noqa: BLE001 — reported per doc; the next run retries it
            report["docs"][doc]["error"] = f"{type(exc).__name__}: {exc}"
    return report


def _append_moved(target: Path, body: str) -> None:
    """Write or append a moved block. Each block carries a ``moved:<digest>`` comment;
    a block whose digest the file already holds (an earlier run wrote the reference file,
    then the doc edit failed) is not added again. Matching by digest, not by text, so a
    short block that happens to occur in the file is never skipped and lost."""
    head, _, moved_body = body.partition("\n\n<!-- Moved out of ")
    moved_body = "<!-- Moved out of " + moved_body
    comment, _, rest = moved_body.partition("\n\n")
    marker = f"<!-- moved:{hashlib.sha256(rest.strip().encode('utf-8')).hexdigest()[:16]} -->"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        old = target.read_text(encoding="utf-8").rstrip("\n")
        if marker in old:
            return
        text = old + "\n\n" + comment + "\n" + marker + "\n\n" + rest
    else:
        text = head + "\n\n" + comment + "\n" + marker + "\n\n" + rest
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(target)
