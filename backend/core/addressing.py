"""addressing — what a DDD holds, where, and each unit of it with one identity.

Knowledge addressing design (docs/design/2026-10-28-knowledge-addressing.md) §2.3–§2.5.
Recall pushes a bounded starting set; this module is what lets an agent find the rest
on its own and cite what it found:

- ``ddd_units(project)`` — every recall unit of a project's DDD, with its file, first
  line, section and ``unit_id``. The scope is the four canonical docs, each one's
  ``*-archive*.md``, the ``2-understanding/knowledge/`` corpus and the data-source
  asset's ``data-contract/``. ``knowledge find`` searches it and the run engine checks
  citations against it, so "can be found" and "can be cited" are the same set.
- ``ddd_map(project, size)`` — what the DDD holds, generated at the point of use (a
  run's knowledge files, an expert's handout, ``knowledge map``), never stored and
  never put in the system prompt (the 2026-08-14 hot/cold rule: no in-prompt index).
- ``find(project, query, ...)`` — whole units ranked by BM25 (the recall scorer),
  each with ``path:line`` and its id; the ones that do not fit are named.
- ``get(project, ref)`` — one unit by id or ``path:line``.
- ``projects()`` — every project and whether it has a DDD (``knowledge map --projects``).

Layout comes only from ``ddd_paths`` / ``DDD_CANONICAL_DOCS``; nothing here knows a
directory name, so a layout change cannot leave it describing the old tree. Only files
inside the project dir (after resolving symlinks) are read; hidden files, ``.artifacts/``,
lock files and databases never are. Nothing here raises for a missing, partial or
unreadable DDD: what cannot be read is left out and named in ``skipped``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAP_FULL_BUDGET = 8000        # characters; whole lines only
SUBHEAD_OVER = 20_000         # a ## section larger than this lists its ### headings
CORPUS_MAX_FILES = 200
CORPUS_MAX_BYTES = 512 * 1024
FIND_TOP = 8
FIND_BUDGET = 12_000          # characters of unit text one find prints

_DONE_RE = re.compile(r"^-\s*\[[xX]\]")
_SUPERSEDED_RE = re.compile(r"<!--[^>]*\|\s*superseded_by:(?!null\b)[^|]+?-->")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


@dataclass
class Unit:
    path: str            # absolute path of the file
    rel: str             # path relative to the project dir
    line: int            # 1-based first line of the unit
    store: str           # doc | archive | corpus | data-contract
    doc: str             # canonical doc name (TECH.md) or the corpus file's rel path
    section: str         # the ## heading it sits under ("" before the first one)
    kind: str            # entry | block
    text: str
    id: str

    @property
    def title(self) -> str:
        first = _COMMENT_RE.sub("", self.text).strip().splitlines()
        return first[0][:160] if first else ""

    @property
    def live(self) -> bool:
        """Not a done item and not superseded: what find returns by default."""
        return not (_DONE_RE.match(self.text.lstrip()) or _SUPERSEDED_RE.search(self.text))


@dataclass
class UnitTable:
    project: str
    root: Optional[Path]
    units: list[Unit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def project_root(project: str) -> Optional[Path]:
    """The project's dir, or None for no / an unknown / an unsafe name."""
    from core.project_registry import get_projects_dir
    if not project or "/" in project or "\\" in project or project.startswith("."):
        return None
    base = get_projects_dir() / project
    return base if base.is_dir() else None


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _readable_file(path: Path, root: Path) -> bool:
    name = path.name
    if name.startswith(".") or name.endswith(".lock") or ".artifacts" in path.parts:
        return False
    return path.suffix == ".md" and path.is_file() and _inside(path, root)


def _doc_files(root: Path) -> list[tuple[str, str, Path]]:
    """(store, doc label, path) for the canonical docs and their archives."""
    from core.ddd_paths import ddd_path
    from core.project_registry import DDD_CANONICAL_DOCS
    out: list[tuple[str, str, Path]] = []
    for doc in DDD_CANONICAL_DOCS:
        p = ddd_path(root, doc)
        if _readable_file(p, root):
            out.append(("doc", doc, p))
        stem = Path(doc).stem
        if p.parent.is_dir():
            for a in sorted(p.parent.glob(f"{stem}-archive*.md")):
                if _readable_file(a, root):
                    out.append(("archive", a.name, a))
    return out


def _tree_files(root: Path, top: Path, store: str, skipped: list[str]) -> list[tuple[str, str, Path]]:
    if not top.is_dir() or not _inside(top, root):
        return []
    out: list[tuple[str, str, Path]] = []
    files = sorted(p for p in top.rglob("*.md"))
    for p in files:
        if len(out) >= CORPUS_MAX_FILES:
            skipped.append(f"{top.relative_to(root)}: {len(files) - CORPUS_MAX_FILES} more files not read")
            break
        if not _readable_file(p, root):
            continue
        try:
            if p.stat().st_size > CORPUS_MAX_BYTES:
                skipped.append(f"{p.relative_to(root)}: larger than {CORPUS_MAX_BYTES // 1024}K, not read")
                continue
        except OSError:
            continue
        out.append((store, str(p.relative_to(root)), p))
    return out


def _all_files(root: Path, skipped: list[str]) -> list[tuple[str, str, Path]]:
    from core.ddd_paths import data_contract_dir, ddd_path
    return (_doc_files(root)
            + _tree_files(root, ddd_path(root, "knowledge"), "corpus", skipped)
            + _tree_files(root, data_contract_dir(root), "data-contract", skipped))


def _file_units(store: str, doc: str, path: Path, root: Path, text: str) -> list[Unit]:
    """The units of one file, section by section, as recall splits them."""
    from core.ddd_entry_lifecycle import split_blocks_at, unit_id
    lines = text.splitlines()
    rel = str(path.relative_to(root))
    out: list[Unit] = []
    section, start, fence = "", 0, ""
    bounds: list[tuple[str, int, int]] = []          # (section, first body line, end)
    for i, ln in enumerate(lines):
        mark = ln.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
            continue
        if not fence and ln.startswith("## "):
            bounds.append((section, start, i))
            section, start = ln[3:].strip(), i + 1
    bounds.append((section, start, len(lines)))
    for sec, first, end in bounds:
        body = "\n".join(lines[first:end])
        for kind, unit_text, local in split_blocks_at(body):
            if kind == "block" and all(not l.strip() or l.lstrip().startswith("#")
                                       for l in _COMMENT_RE.sub("", unit_text).splitlines()):
                continue                                  # only headings / comments
            out.append(Unit(str(path), rel, first + local + 1, store, doc, sec, kind,
                            unit_text, unit_id(unit_text)))
    return out


def ddd_units(project: str) -> UnitTable:
    """Every unit of *project*'s DDD (see the module docstring). Never raises."""
    root = project_root(project)
    table = UnitTable(project, root)
    if root is None:
        return table
    for store, doc, path in _all_files(root, table.skipped):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            table.skipped.append(f"{path.relative_to(root)}: unreadable ({type(exc).__name__})")
            continue
        try:
            table.units += _file_units(store, doc, path, root, text)
        except Exception as exc:  # noqa: BLE001 -- one file that fails is left out alone
            logger.warning("addressing: %s skipped", path, exc_info=True)
            table.skipped.append(f"{path.relative_to(root)}: not parsed ({type(exc).__name__})")
    return table


def unit_ids(project: str) -> set[str]:
    """The ids of every unit that exists in *project*'s DDD now (archives included):
    what a citation may name besides the units recalled for it."""
    return {u.id for u in ddd_units(project).units}


# ── map ─────────────────────────────────────────────────────────────────────

def _k(n: int) -> str:
    return f"{n / 1024:.0f}K" if n >= 1024 else f"{n}B"


def _sections(text: str) -> list[tuple[int, str, int, list[tuple[int, str, int]]]]:
    """(line, ## heading, size, [(line, ### heading, size)]) for one doc."""
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []        # (index, level, title)
    fence = ""
    for i, ln in enumerate(lines):
        mark = ln.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
            continue
        if not fence and (ln.startswith("## ") or ln.startswith("### ")):
            heads.append((i, 2 if ln.startswith("## ") else 3, ln.lstrip("#").strip()))

    def size(a: int, b: int) -> int:
        return sum(len(x) + 1 for x in lines[a:b])
    out = []
    h2 = [h for h in heads if h[1] == 2]
    for j, (i, _lvl, title) in enumerate(h2):
        end = h2[j + 1][0] if j + 1 < len(h2) else len(lines)
        subs = [h for h in heads if h[1] == 3 and i < h[0] < end]
        sub_rows = []
        for m, (si, _l, st) in enumerate(subs):
            send = subs[m + 1][0] if m + 1 < len(subs) else end
            sub_rows.append((si + 1, st, size(si, send)))
        out.append((i + 1, title, size(i, end), sub_rows))
    return out


def _how_to(project: str) -> list[str]:
    return [
        "How to read it: the knowledge you were given is a starting set, not everything.",
        f'- find: "$SWARM_BACKEND_BIN" knowledge find --project {project} --query "<words of your solution>"',
        "  (whole entries with path:line and an id; add --doc TECH.md to search one doc)",
        f'- get: "$SWARM_BACKEND_BIN" knowledge get --project {project} <id>|<path>:<line>',
        "- or Grep -n a word in one of the files above, then Read it at that line.",
        "Never read TECH / IMPROVEMENT whole. Cite what you used by its id.",
    ]


def ddd_map(project: str, size: str = "full", budget: Optional[int] = None) -> str:
    """What *project*'s DDD holds, as text. ``full``: every doc with its ## sections
    (line, size; ### under a ## over ``SUBHEAD_OVER``), archives, the corpus files,
    governed assets, gates, capabilities. ``brief``: the root, each doc's path and
    size, and how to read it. Whole lines within *budget* (default ``MAP_FULL_BUDGET``
    for full, none for brief); the lines left out are counted with where to get them.
    Empty string for a project with no dir. Never raises."""
    root = project_root(project)
    if root is None:
        return ""
    try:
        return _map(project, root, size, budget)
    except Exception:  # noqa: BLE001 -- a map that cannot be made is not a run failure
        logger.warning("addressing: map of %s failed", project, exc_info=True)
        return f"DDD of project {project}: {root} (map unavailable)"


def _map(project: str, root: Path, size: str, budget: Optional[int]) -> str:
    from core.ddd_paths import CAPABILITIES_DIR, GATES_DIR, ASSETS_DIR, ddd_path
    skipped: list[str] = []
    head = [f"DDD of project {project}: {root}"]
    rows: list[str] = []
    docs = _doc_files(root)
    if not docs:
        rows.append("(no DDD documents yet)")
    for store, doc, path in docs:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            rows.append(f"{path} (unreadable)")
            continue
        if store == "archive":
            rows.append(f"{path} ({_k(len(text))}, archived: decayed entries, reference only)")
            continue
        rows.append(f"{path} ({_k(len(text))}, {len(text.splitlines())} lines)")
        if size != "full":
            continue
        for line, title, n, subs in _sections(text):
            rows.append(f"  L{line} ## {title} ({_k(n)})")
            if n > SUBHEAD_OVER:
                for sline, stitle, sn in subs:
                    rows.append(f"    L{sline} ### {stitle} ({_k(sn)})")
    if size == "full":
        corpus = _tree_files(root, ddd_path(root, "knowledge"), "corpus", skipped)
        if corpus:
            rows.append(f"{ddd_path(root, 'knowledge')}/ (deep reference, {len(corpus)} files)")
            for _s, rel, p in corpus:
                try:
                    rows.append(f"  {rel} ({_k(p.stat().st_size)})")
                except OSError:
                    continue
        assets = root / ASSETS_DIR
        if assets.is_dir() and _inside(assets, root):
            kinds = sorted(d.name for d in assets.iterdir() if d.is_dir() and not d.name.startswith("."))
            if kinds:
                rows.append(f"{assets}/ governed assets: " + ", ".join(kinds))
        for label, d in (("gates", GATES_DIR), ("capabilities", CAPABILITIES_DIR)):
            p = root / d
            if p.is_dir() and _inside(p, root):
                names = sorted(x.name for x in p.iterdir() if not x.name.startswith("."))
                if names:
                    rows.append(f"{p}/ {label}: " + ", ".join(names))
        present = [n for n in ("AGENTS.md", "bindings.yaml", "REFRESHER.md", "CONTEXT.md")
                   if (root / n).is_file()]
        if present:
            rows.append(f"{root}/: " + ", ".join(present))
        rows += [f"(not read: {s})" for s in skipped]
    tail = _how_to(project)
    limit = budget if budget is not None else (MAP_FULL_BUDGET if size == "full" else None)
    if limit is None:
        return "\n".join(head + rows + tail)
    used = sum(len(x) + 1 for x in head + tail) + 120
    shown: list[str] = []
    for r in rows:
        if used + len(r) + 1 > limit:
            break
        shown.append(r)
        used += len(r) + 1
    if len(shown) < len(rows):
        shown.append(f"... {len(rows) - len(shown)} more lines not shown; the whole map: "
                     f'"$SWARM_BACKEND_BIN" knowledge map --project {project}')
    return "\n".join(head + shown + tail)


# ── find / get ──────────────────────────────────────────────────────────────

@dataclass
class Found:
    units: list[Unit]
    omitted: list[Unit]
    skipped: list[str]


def find(project: str, query: str, *, doc: Optional[str] = None, top: int = FIND_TOP,
         budget: int = FIND_BUDGET, include_all: bool = False) -> Found:
    """The best *top* live units of *project*'s DDD for *query*, whole, while they fit
    *budget* characters; the rest of the top are returned as ``omitted``. All units in
    scope are ranked in ONE BM25 corpus, so a strong match anywhere beats a weak one
    elsewhere (ranking each file on its own gave every file's best the same score and
    returned one weak hit per file). The query is the agent's own words, chosen for
    what it is looking for -- unlike push recall, which ranks the request's words per
    doc with a quota each. *doc* limits the search to one doc (``TECH.md``) and its
    archive, or one corpus file by its relative path."""
    from core import memory_index
    table = ddd_units(project)
    pool = [u for u in table.units if include_all or u.live]
    if doc:
        stem = Path(doc).stem
        pool = [u for u in pool if u.doc == doc or u.rel == doc
                or (u.store == "archive" and u.doc.startswith(f"{stem}-archive"))]
    if not (query or "").strip() or not pool:
        return Found([], [], table.skipped)
    raw = memory_index._bm25_scores(query, {str(i): u.text for i, u in enumerate(pool)})
    scored = sorted(((s, pool[int(k)]) for k, s in raw.items() if s > 0),
                    key=lambda su: su[0], reverse=True)
    best = [u for _s, u in scored[:max(1, top)]]
    from core.recall_filter import take_whole
    shown, omitted = take_whole(best, budget, lambda u: len(u.text))
    return Found(shown, omitted, table.skipped)


_LOC_RE = re.compile(r"^(?P<path>.+):(?P<line>\d+)$")


def get(project: str, ref: str) -> Optional[Unit]:
    """One unit of *project*'s DDD by its id or by ``path:line`` (absolute or relative to
    the project dir; any line inside the unit). None when nothing matches."""
    table = ddd_units(project)
    ref = (ref or "").strip()
    if re.fullmatch(r"[0-9a-f]{8}", ref):
        return next((u for u in table.units if u.id == ref), None)
    m = _LOC_RE.match(ref)
    if not m or table.root is None:
        return None
    raw = Path(m.group("path")).expanduser()
    path = raw if raw.is_absolute() else table.root / raw
    try:
        path = path.resolve()
    except OSError:
        return None
    line = int(m.group("line"))
    mine = [u for u in table.units if Path(u.path).resolve() == path and u.line <= line]
    if not mine:
        return None
    u = max(mine, key=lambda x: x.line)
    return u if line < u.line + len(u.text.splitlines()) + 3 else None


def projects() -> list[tuple[str, Optional[Path], bool]]:
    """(name, project dir, has DDD documents) for every project, by name."""
    from core.project_registry import get_projects_dir
    root = get_projects_dir()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        out.append((d.name, d, bool(_doc_files(d))))
    return out


def render_unit(u: Unit) -> str:
    """One unit as an agent reads it: where it is, its id, its whole text in a fence."""
    text = re.sub(r"</?\s*recalled\s*>", "", u.text, flags=re.I).strip()
    sec = f" § {u.section}" if u.section else ""
    return f"- `{u.id}` · {u.path}:{u.line}{sec}\n<recalled>\n{text}\n</recalled>"
