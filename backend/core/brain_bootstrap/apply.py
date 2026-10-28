"""bootstrap-apply — write a new brain's first entries, checked, never guessed.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.4–§5.6 (phase one). The
agent reads the survey and writes ``.artifacts/bootstrap/bootstrap-entries.json``;
this module decides, deterministically, which of those entries reach the four docs.
The model judges what to write; this code owns where it goes, its shape, its evidence
and whether the doc may be touched at all.

Entry (one JSON object; unknown keys are refused, so a misspelt field is caught)::

    {"kind": "cognition", "type": "<VALID_TYPES>", "section": "<skeleton section>",
     "title": …, "what": …, "how"?, "why"?, "applicability"?,
     "rejected_alternatives"?, "sources": [ … ]}
    {"kind": "gap", "section": "What to Watch For", "title": …, "what": …,
     "why"?, "proposed_next"?, "sources"?: [ … ]}

Rules (each refusal names its reason in ``rejected.json``):
- the section is a skeleton section and admits the type (``ddd_skeleton.SECTION_TYPES``,
  CDF's routing matrix reversed); a gap goes to What to Watch For only;
- a decision has ``why`` and ``rejected_alternatives`` ("none" is an answer, empty is
  not); a pitfall has ``applicability``; a process has ``how``;
- every cognition entry cites at least one source, and each source is checkable:
  ``<path>[#heading|:line]`` (``<repo>:<path>…`` when the brain owns several repos) must
  be a tracked file of a repo the brain owns; ``file:<absolute path>`` an existing,
  non-secret file; ``url:<http(s) URL>``; ``user`` the person's own answer. Anything
  but ``user`` is outside content: the entry is written with ``taint:external``;
- a title of at most ``MAX_TITLE`` characters and fields of at most ``MAX_FIELD`` are
  never cut: a longer one is refused, to be split;
- the brain is ``setting-up`` (``ddd_setup_state``), and the target doc holds nothing
  but the skeleton plus entries this tool wrote, unchanged: each ``source:bootstrap``
  entry's first line is one ``applied.json`` recorded, and removing those entries'
  two rendered lines leaves exactly the skeleton. A doc the person (or any other writer) has written in, an edited bootstrap
  entry included, is reported and left alone.

Writes only insert, at the end of the entry's section; every existing byte, line
endings included, is kept. An entry whose rendered line is already in its section is
skipped, so a re-run is safe. ``applied.json`` (each written entry: its rendered line
and full source list) is written BEFORE the doc, so a crash in between can only leave
a recorded line that is not in the doc yet — which the next run writes — never a
written line the next run would mistake for someone else's. ``rejected.json`` holds
this run's refusals (a refused run writes its reason there too). The report also names the PRODUCT sections
still without evidence: ``ask`` (Vision, Non-Goals, Success Criteria — ask the person,
at most three questions) and ``need_gap`` (every PRODUCT section that P6 will fail
until it holds an entry or a gap).

Reader of what this writes: ``ddd_entry_lifecycle.parse_entries`` (the entries), the P6
gate (content check), CDF's baseline later (``taint``). When CDF replaces this writer
(its phase B), this module's write path is deleted (design §5.5, CDF §8).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from . import repo_read as rr

logger = logging.getLogger(__name__)

BOOTSTRAP_DIR = Path(".artifacts") / "bootstrap"
ENTRIES_FILE = BOOTSTRAP_DIR / "bootstrap-entries.json"
APPLIED_FILE = BOOTSTRAP_DIR / "applied.json"
REJECTED_FILE = BOOTSTRAP_DIR / "rejected.json"
MAX_TITLE = 200
MAX_FIELD = 4000
MAX_ENTRIES = 500

_COGNITION_KEYS = {"kind", "type", "section", "title", "what", "how", "why",
                   "applicability", "rejected_alternatives", "sources"}
_GAP_KEYS = {"kind", "section", "title", "what", "why", "proposed_next", "sources"}
_REQUIRED = {"decision": ("why", "rejected_alternatives"), "pitfall": ("applicability",),
             "process": ("how",)}
_ANCHOR_RE = re.compile(r"^(?P<path>[^#]+?)(?:#.+|:\d+(?:-\d+)?)?$")


class Rejected(ValueError):
    """An entry this tool will not write; the message is the reason."""


# ── sources ──────────────────────────────────────────────────────────────────

class Evidence:
    """What a source may point at: the files of each repo, by repo name (a git repo's
    tracked files, else the walked tree — ``repo_read.list_files``)."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self.repos: dict[str, set[str]] = {}
        for name, root in roots.items():
            try:
                self.repos[name] = set(rr.list_files(Path(root)))
            except Exception as exc:  # noqa: BLE001 — a repo that cannot be listed cites nothing
                logger.warning("bootstrap-apply: cannot list %s: %s", name, exc)
                self.repos[name] = set()

    @classmethod
    def for_project(cls, project_dir: Path) -> "Evidence":
        """The repos the brain owns (``survey.owned_repos``)."""
        from .survey import owned_repos
        return cls({r["name"]: Path(r["root"]) for r in owned_repos(project_dir)})

    def check(self, source: str) -> bool:
        """True when *source* is outside content (taint), False for ``user``.
        Raises Rejected when it points at nothing checkable."""
        s = source.strip()
        if s == "user":
            return False
        if s.startswith("url:"):
            if not re.match(r"^https?://\S+$", s[4:]):
                raise Rejected(f"source {s!r}: url: needs an http(s) URL")
            return True
        if s.startswith("file:"):
            try:
                p = Path(s[5:]).expanduser()
                ok = p.is_absolute() and p.is_file() and not rr.is_secret_path(p.name)
            except (OSError, RuntimeError, ValueError):
                ok = False
            if not ok:
                raise Rejected(f"source {s!r}: file: needs an existing, absolute, non-secret file")
            return True
        repo, rest = None, s
        head, sep, tail = s.partition(":")
        if sep and head in self.repos and "/" not in head:
            repo, rest = head, tail
        m = _ANCHOR_RE.match(rest)
        path = m.group("path") if m else ""
        candidates = [repo] if repo else [n for n, files in self.repos.items() if path in files]
        if repo and path not in self.repos.get(repo, set()):
            candidates = []
        if not candidates:
            raise Rejected(f"source {s!r}: not a tracked file of a repo this brain owns "
                           "(use <path>#heading, <repo>:<path>, file:, url: or user)")
        if len(candidates) > 1:
            raise Rejected(f"source {s!r}: in several repos ({', '.join(sorted(candidates))}); "
                           "prefix it with the repo name")
        return True


# ── one entry ────────────────────────────────────────────────────────────────

def _text(entry: dict, key: str) -> str:
    v = entry.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise Rejected(f"{key} must be text")
    v = v.strip()
    limit = MAX_TITLE if key == "title" else MAX_FIELD
    if len(v) > limit:
        raise Rejected(f"{key} is {len(v)} characters (limit {limit}); split the entry")
    return v


def validate(entry, evidence: Evidence) -> dict:
    """``{doc, section, render, taint, sources}`` for an entry that may be written, or
    Rejected."""
    from core.ddd_skeleton import GAP_SECTION, SECTION_TYPES, locate_section
    if not isinstance(entry, dict):
        raise Rejected("an entry must be a JSON object")
    kind = entry.get("kind")
    if kind not in ("cognition", "gap"):
        raise Rejected("kind must be 'cognition' or 'gap'")
    unknown = set(entry) - (_COGNITION_KEYS if kind == "cognition" else _GAP_KEYS)
    if unknown:
        raise Rejected(f"unknown field(s): {', '.join(sorted(unknown))}")
    place = locate_section(str(entry.get("section") or ""))
    if place is None:
        raise Rejected(f"section {entry.get('section')!r} is not a skeleton section")
    title, what = _text(entry, "title"), _text(entry, "what")
    if not title or not what:
        raise Rejected("title and what are required")
    sources = entry.get("sources") or []
    if not isinstance(sources, list) or not all(isinstance(s, str) and s.strip() for s in sources):
        raise Rejected("sources must be a list of non-empty strings")

    if kind == "gap":
        if place != GAP_SECTION:
            raise Rejected(f"a gap goes to {GAP_SECTION[1]}, not {place[1]}")
        render = {"type": "gap", "title": title, "what": what, "why": _text(entry, "why")}
        nxt = _text(entry, "proposed_next")
        if nxt:
            render["what"] = f"{what} Next: {nxt}"
    else:
        etype = entry.get("type")
        allowed = SECTION_TYPES.get(place)
        if allowed is None:
            raise Rejected(f"{place[1]} takes no bootstrap entry"
                           + (" (gaps only)" if place == GAP_SECTION else " (the person's live state)"))
        if etype not in allowed:
            raise Rejected(f"type {etype!r} does not belong in {place[1]} "
                           f"(allowed: {', '.join(sorted(allowed))})")
        for key in _REQUIRED.get(etype, ()):
            if not _text(entry, key):
                raise Rejected(f"a {etype} needs {key}")
        if not sources:
            raise Rejected("a cognition entry needs at least one source")
        render = {"type": etype, "title": title, "what": what, "how": _text(entry, "how"),
                  "why": _text(entry, "why"), "applicability": _text(entry, "applicability"),
                  "rejected": _text(entry, "rejected_alternatives")}
    taint = any([evidence.check(s) for s in sources])
    render["sources"] = [s.strip() for s in sources]
    return {"doc": place[0], "section": place[1], "render": render,
            "taint": "external" if taint else None, "sources": render["sources"]}


def check_entry(entry, evidence: Evidence, stamp: str) -> dict:
    """``validate`` plus the rendered block (``block``): exactly what a run does to
    one entry before it is placed. Raises Rejected (or ValueError from the renderer,
    e.g. a title that is not one line). The eval's ``bootstrap_entries`` check calls
    this too, so an agent's answer is judged by the rules that would write it."""
    from core.ddd_entry_lifecycle import render_ddd_unit
    v = validate(entry, evidence)
    v["block"] = render_ddd_unit(v["render"], date_str=stamp, source="bootstrap",
                                 taint=v["taint"])
    return v


# ── docs ─────────────────────────────────────────────────────────────────────

def _squash(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{2,}", "\n\n", "\n".join(lines)).strip()


def _bootstrap_only(doc: str, text: str, project_name: str, written_lines: set[str]) -> bool:
    """True when *text* is the skeleton plus only unchanged entries this tool wrote:
    each ``source:bootstrap`` entry's first line is one *written_lines* recorded
    (``applied.json``); its two rendered lines (entry + metadata) are removed and what
    is left must be the skeleton. So anything else — a note inside or after an entry, an
    edited entry, a person's own entry — makes the doc someone's work."""
    from core.ddd_entry_lifecycle import parse_entries
    from core.ddd_skeleton import render_skeleton
    lines = text.replace("\r\n", "\n").split("\n")
    drop: set[int] = set()
    for e in parse_entries("\n".join(lines)):
        if e.source != "bootstrap":
            continue
        if lines[e.line_number] not in written_lines:
            return False
        drop.update((e.line_number, e.line_number + 1))
    rest = "\n".join(ln for i, ln in enumerate(lines) if i not in drop)
    return _squash(rest) == _squash(render_skeleton(doc, project_name))


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    """``(heading index, end index)`` of ``## section`` in *lines* (each line with its
    own ending), end = the next ``## `` heading or the end of the doc."""
    from core.ddd_skeleton import normalize_heading
    def body(ln: str) -> str:
        return ln.rstrip("\r\n")
    start = next((i for i, ln in enumerate(lines) if body(ln).startswith("## ")
                  and normalize_heading(body(ln)[3:]) == normalize_heading(section)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if body(lines[i]).startswith("## ")),
               len(lines))
    return start, end


def _append_to_section(text: str, section: str, blocks: list[str]) -> str:
    """*blocks* inserted at the end of ``## section`` (after its last non-blank line).
    Only inserts: every existing character, line endings included, is kept — the
    lines are split with their own endings and joined back unchanged."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    span = _section_span(lines, section)
    if span is None:
        raise Rejected(f"## {section} is not in the doc")
    start, end = span
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    if at > 0 and not lines[at - 1].endswith(("\n", "\r")):
        lines[at - 1] += nl          # the doc ended without a newline: end that line
    new = [nl] + [ln + nl for b in blocks for ln in b.split("\n")]
    if at < len(lines) and at == end:
        new.append(nl)               # keep a blank line before the next heading
    return "".join(lines[:at] + new + lines[at:])


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


# ── the run ──────────────────────────────────────────────────────────────────

def apply_entries(project_dir: str | Path, entries_path: str | Path | None = None, *,
                  dry_run: bool = False, today: date | None = None) -> dict:
    """Validate and (unless *dry_run*) write the entries file. Returns the report:
    ``{status, written: {doc: n}, already_present, rejected: [{index, title, reason}],
    refused_docs: {doc: reason}, ask, need_gap}``; ``status`` is ``refused`` when the
    brain is not setting up or the entries file cannot be read (nothing written)."""
    from core.ddd_paths import ddd_path
    from core.ddd_setup_state import read_setup_state
    from core.ddd_skeleton import unexplained_product_sections, ASK_SECTIONS

    project_dir = Path(project_dir)
    name = project_dir.name

    def _refuse(reason: str) -> dict:
        if not dry_run:
            _write_json(project_dir / REJECTED_FILE,
                        [{"index": None, "title": None, "reason": reason}])
        return {"status": "refused", "reason": reason}

    if read_setup_state(project_dir) != "setting-up":
        return _refuse("the brain is not setting up; bootstrap writes only a newborn brain")
    path = Path(entries_path) if entries_path else project_dir / ENTRIES_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _refuse(f"cannot read {path.name}: {type(exc).__name__}")
    items = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return _refuse("the file must hold a list of entries")
    if len(items) > MAX_ENTRIES:
        return _refuse(f"more than {MAX_ENTRIES} entries in one run")
    log_path = project_dir / APPLIED_FILE
    try:
        old_log = json.loads(log_path.read_text(encoding="utf-8"))
        old_log = old_log if isinstance(old_log, list) else []
    except (OSError, ValueError):
        old_log = []
    written_lines = {a["line"] for a in old_log if isinstance(a, dict) and isinstance(a.get("line"), str)}

    evidence = Evidence.for_project(project_dir)
    stamp = (today or date.today()).isoformat()
    rejected: list[dict] = []
    by_doc: dict[str, list[tuple[int, dict]]] = {}
    for i, entry in enumerate(items):
        try:
            v = check_entry(entry, evidence, stamp)
        except (Rejected, ValueError) as exc:
            rejected.append({"index": i, "title": (entry or {}).get("title") if isinstance(entry, dict) else None,
                             "reason": str(exc)})
            continue
        by_doc.setdefault(v["doc"], []).append((i, v))

    written: dict[str, int] = {}
    already = 0
    refused_docs: dict[str, str] = {}
    applied_log: list[dict] = []
    texts: dict[str, str] = {}
    for doc in sorted(by_doc):
        p = ddd_path(project_dir, doc)
        try:
            with open(p, encoding="utf-8", newline="") as fh:
                text = fh.read()
        except OSError as exc:
            refused_docs[doc] = f"unreadable: {type(exc).__name__}"
            continue
        if not _bootstrap_only(doc, text, name, written_lines):
            refused_docs[doc] = ("already holds the person's own writing (or an edited "
                                 "bootstrap entry); nothing added")
            for i, v in by_doc[doc]:
                rejected.append({"index": i, "title": v["render"]["title"],
                                 "reason": f"{doc} already holds the person's own writing; "
                                           "not added — show it to them and ask"})
            continue
        lines = text.splitlines(keepends=True)
        sections: dict[str, list[str]] = {}
        doc_log: list[dict] = []
        for i, v in by_doc[doc]:
            first = v["block"].split("\n", 1)[0]
            span = _section_span(lines, v["section"])
            present = span and any(ln.rstrip("\r\n") == first for ln in lines[span[0]:span[1]])
            if present or any(b.split("\n", 1)[0] == first for b in sections.get(v["section"], [])):
                already += 1
                continue
            sections.setdefault(v["section"], []).append(v["block"])
            doc_log.append({"doc": doc, "section": v["section"], "title": v["render"]["title"],
                            "type": v["render"]["type"], "taint": v["taint"],
                            "sources": v["sources"], "line": first, "at": stamp})
        new = text
        for section, blocks in sections.items():
            new = _append_to_section(new, section, blocks)
        if new != text:
            written[doc] = sum(len(b) for b in sections.values())
            if not dry_run:
                # The log first: a crash before the doc write leaves a recorded line the
                # next run writes; the reverse would leave a line it calls foreign.
                applied_log.extend(doc_log)
                _write_json(log_path, old_log + applied_log)
                _write_atomic(p, new)
        texts[doc] = new

    def _doc_text(doc: str) -> str:
        if doc in texts:
            return texts[doc]
        try:
            return ddd_path(project_dir, doc).read_text(encoding="utf-8")
        except OSError:
            return ""

    unexplained = unexplained_product_sections(_doc_text("PRODUCT.md"), _doc_text("IMPROVEMENT.md"))
    report = {"status": "dry-run" if dry_run else "applied", "written": written,
              "already_present": already, "rejected": rejected, "refused_docs": refused_docs,
              "ask": [s for s in ASK_SECTIONS if s in unexplained], "need_gap": unexplained}
    if not dry_run:
        _write_json(project_dir / REJECTED_FILE, rejected)
    return report
