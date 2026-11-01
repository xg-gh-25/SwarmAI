"""cognition — the stores an agent can read on demand, each as whole units.

Cognition entrance design (docs/design/2026-11-01-cognition-entrance.md) §3. One read
entrance for the whole brain: the ``cognition`` command (``knowledge`` is the same
command) reads every store below, each cut into whole units with ``path:line`` and the
8-character ``unit_id`` a pipeline document or an expert finding cites.

``STORES`` is the table of what can be read:

- ``ddd`` — a Brain: a project's DDD, or the General Brain (``Knowledge/Library``),
  through ``addressing`` (its unit table, map and ids);
- ``library`` — the full-text index of ``Knowledge/`` (``run_knowledge._library_leg``); a hit
  reads back by its id or its location from the same index (``library_get``);
- ``memory`` / ``evolution`` / ``timeline`` / ``context`` — the workspace's ``.context/``
  files: MEMORY and its archives, EVOLUTION and its archives, TIMELINE, and the other
  governed files (SWARMAI, IDENTITY, SOUL, SELF, AGENT, USER, STEERING, TOOLS, KNOWLEDGE);
- ``session`` — past sessions (the session recall leg), found only;
- ``code`` — a project's code graph (the code-intel recall leg), found only.

How a file is cut (§3.3): a file whose unit is a ``### `` block (EVOLUTION and its
archives, TIMELINE) is cut at each ``### `` heading, the heading and every line under it
up to the next ``### `` or ``## `` being one unit; the text of a section before its first
``### `` is cut like any other file. Every other file is cut as ``addressing`` cuts a DDD
document (entries, paragraphs, tables). Nothing between two units is left out, so every
line of a file can be read back by ``path:line``.

``locate`` reads back every ref ``find`` prints, without ``--project``: an id, a
``path:line`` (absolute or relative to the workspace) or a Library location. Only a
project DDD's id needs ``--project P``; ``find`` names it on that group.

Each store is ranked on its own (BM25 over its own units, as ``addressing.find``); scores
of two stores are never compared, so a big store cannot push a small one out.

Who may read is not decided here: the command refuses whole in a session the daemon
marked (``knowledge_cli``). On a worker Hive only the Brains and code are read: its
memory, sessions and Knowledge belong to other dispatches (the same rule as its recall).
Its ``Knowledge/Library`` is the synced General Brain, so the full-text search there
returns only the hits inside it (``worker_library``): a task under the General Brain is
told to search with ``--in library``, and a Hive of an older release knows no other way.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.addressing import Unit

logger = logging.getLogger(__name__)

MULTI_TOP = 3                     # units per store when several stores are searched
MULTI_BUDGET = 6_000              # characters of unit text per store then


@dataclass(frozen=True)
class Store:
    name: str
    about: str
    domain: Optional[str]          # the push-recall domain (recall_multi.DOMAINS) it covers
    needs_project: bool = False
    can_get: bool = True
    on_worker: bool = False        # readable on a worker Hive


CONTEXT_FILES = ("SWARMAI.md", "IDENTITY.md", "SOUL.md", "SELF.md", "AGENT.md", "USER.md",
                 "STEERING.md", "TOOLS.md", "KNOWLEDGE.md")

STORES: dict[str, Store] = {s.name: s for s in (
    Store("ddd", "a Brain: a project's DDD (--project P), or the General Brain (--project General, "
                 "Knowledge/Library)", "ddd", needs_project=True, on_worker=True),
    Store("library", "full-text search of Knowledge/ (Notes, Reports, Designs, Meetings, "
                     "DailyActivity, Library ...)", "library", on_worker=True),
    Store("memory", "MEMORY.md and its archives: cross-session principles, decisions, corrections",
          "context_files"),
    Store("evolution", "EVOLUTION.md and its archives: corrections and their data points", None),
    Store("timeline", "TIMELINE.md: key events, including the ones not injected", None),
    Store("context", "the other context files: " + ", ".join(f[:-3] for f in CONTEXT_FILES), None),
    Store("session", "past sessions (the matched messages of each)", "session", can_get=False),
    Store("code", "a project's code graph: symbols with their callers (--project P)", "codeintel",
          needs_project=True, can_get=False, on_worker=True),
)}

# a file whose units are its ``### `` blocks
_H3_FILES = ("EVOLUTION", "TIMELINE")


@dataclass
class Hits:
    store: str
    units: list = field(default_factory=list)     # Unit, or a dict for session / code
    omitted: list = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # a leg's own "not shown" lines (no id)
    note: str = ""                                   # why the store was not searched


# ── the .context stores ────────────────────────────────────────────────────

def context_dir() -> Path:
    from core.project_registry import get_swarmws
    return get_swarmws() / ".context"


def store_files(store: str) -> list[Path]:
    """The files of a ``.context`` store that exist, main file first."""
    d = context_dir()
    if not d.is_dir():
        return []
    if store == "memory":
        names = ["MEMORY.md"] + sorted(p.name for p in d.glob("MEMORY-archive*.md"))
    elif store == "evolution":
        names = ["EVOLUTION.md"] + sorted(p.name for p in d.glob("EVOLUTION-archive*.md"))
    elif store == "timeline":
        names = ["TIMELINE.md"]
    elif store == "context":
        names = list(CONTEXT_FILES)
    else:
        return []
    from core.addressing import _readable_file
    return [d / n for n in names if _readable_file(d / n, d)]


def _store_of(path: Path) -> Optional[str]:
    """The ``.context`` store *path* (resolved) is a file of, or None."""
    for store in ("memory", "evolution", "timeline", "context"):
        if any(p.resolve() == path for p in store_files(store)):
            return store
    return None


def _skip_block(text: str) -> bool:
    from core.addressing import _COMMENT_RE
    return all(not ln.strip() or ln.lstrip().startswith("#")
               for ln in _COMMENT_RE.sub("", text).splitlines())


def _h3_units(store: str, doc: str, path: Path, root: Path, text: str,
              fences: bool = True) -> list[Unit]:
    """Units of a file whose unit is a ``### `` block (see the module docstring). A fence
    still open at the end of the file is not a fence: the file is cut again ignoring
    fences, so one stray marker never hides the headings after it (addressing's rule)."""
    from core.ddd_entry_lifecycle import split_blocks_at, unit_id
    from core.memory_index import section_name
    lines = text.splitlines()
    rel = str(path.relative_to(root))
    out: list[Unit] = []
    section = ""
    start: Optional[int] = None
    is_block = False
    fence = ""

    def flush(end: int) -> None:
        if start is None:
            return
        chunk = lines[start:end]
        if is_block:
            body = "\n".join(chunk).rstrip()
            if body.strip():
                out.append(Unit(str(path), rel, start + 1, store, doc, section, "block",
                                body, unit_id(body)))
            return
        for kind, unit_text, local in split_blocks_at("\n".join(chunk)):
            if kind == "block" and _skip_block(unit_text):
                continue
            out.append(Unit(str(path), rel, start + local + 1, store, doc, section, kind,
                            unit_text, unit_id(unit_text)))

    for i, ln in enumerate(lines):
        mark = ln.lstrip()[:3] if fences else ""
        code = bool(fence) or mark in ("```", "~~~")     # a heading inside a fence is code
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
        if not code and ln.startswith("## "):
            flush(i)
            section, start, is_block = section_name(ln[3:].strip()), None, False
            continue
        if not code and ln.startswith("### "):
            flush(i)
            start, is_block = i, True
            continue
        if start is None:
            start, is_block = i, False
    if fence:
        return _h3_units(store, doc, path, root, text, fences=False)
    flush(len(lines))
    return out


def file_units(store: str, path: Path) -> list[Unit]:
    """Every unit of one ``.context`` file. Never raises: an unreadable file has none."""
    root = path.parent
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("cognition: %s unreadable", path)
        return []
    try:
        if path.name.split("-")[0].split(".")[0] in _H3_FILES:
            return _h3_units(store, path.name, path, root, text)
        from core.addressing import _file_units
        return _file_units(store, path.name, path, root, text)
    except Exception:  # noqa: BLE001 -- one file that fails is left out alone
        logger.warning("cognition: %s not parsed", path, exc_info=True)
        return []


def store_units(store: str, doc: Optional[str] = None) -> list[Unit]:
    files = store_files(store)
    if doc:
        files = [p for p in files if p.name == doc]
    out: list[Unit] = []
    for p in files:
        out += file_units(store, p)
    return out


# ── find ───────────────────────────────────────────────────────────────────

def _rank(units: list[Unit], query: str, top: int, budget: int) -> tuple[list[Unit], list[Unit]]:
    from core import memory_index
    from core.recall_filter import take_whole
    if not (query or "").strip() or not units:
        return [], []
    raw = memory_index._bm25_scores(query, {str(i): u.text for i, u in enumerate(units)})
    scored = sorted(((s, units[int(k)]) for k, s in raw.items() if s > 0),
                    key=lambda su: su[0], reverse=True)
    best = [u for _s, u in scored[:max(1, top)]]
    return take_whole(best, budget, lambda u: len(u.text))


def find_store(store: str, query: str, *, project: Optional[str] = None,
               doc: Optional[str] = None, top: int = 8, budget: int = 12_000,
               general_searched: bool = False) -> Hits:
    """One store's best units for *query*. Never raises. *general_searched*: the General
    Brain's own units are in the same answer, so the Library hits inside it are left out."""
    hits = Hits(store)
    try:
        if store == "ddd":
            from core import addressing
            found = addressing.find(project or "", query, doc=doc, top=top, budget=budget)
            hits.units, hits.omitted, hits.skipped = found.units, found.omitted, found.skipped
        elif store == "library":
            from core.run_protocol.run_knowledge import _library_leg
            rows = worker_library(_library_leg(query))
            hits.notes = [str(r.get("text") or "") for r in rows if not r.get("id")]
            rows = [r for r in rows if r.get("id")]
            if general_searched:   # the General Brain's units already stand for Knowledge/Library
                rows = [r for r in rows if not str(r.get("source") or "").startswith("Library/")]
            from core.recall_multi import is_external_source, is_repo_doc_source
            for r in rows:
                src = str(r.get("source") or "")
                if src and (is_external_source(src) or is_repo_doc_source(src)):
                    r["external"] = True
            hits.units = rows
        elif store == "session":
            import os
            from core.recall_multi import _recall_session
            rows, _layer = _recall_session(query, os.environ.get("SWARM_APP_SESSION_ID") or None)
            hits.units = [{k: v for k, v in r.items() if k != "matched"} for r in rows[:top]]
        elif store == "code":
            from core.recall_multi import _codeintel_recall
            hits.units = _codeintel_recall(query, project=project, refresh=True)[:top]
        else:
            hits.units, hits.omitted = _rank(store_units(store, doc), query, top, budget)
    except Exception as exc:  # noqa: BLE001 -- one store that fails is named, never fatal
        logger.warning("cognition: %s search failed", store, exc_info=True)
        hits.note = f"not searched: {type(exc).__name__}"
    return hits


WORKER_NOTE = "not read on a worker Hive (its memory, sessions and Knowledge are other dispatches')"


def worker_refusal(store: str) -> Optional[str]:
    """Why *store* is not read here, or None: on a worker Hive only the Brains and code are
    (the rule its push recall follows, session_router). Every route that reads a store
    asks this: find, get and map."""
    from core.app_role import is_worker
    s = STORES.get(store)
    return WORKER_NOTE if s is not None and is_worker() and not s.on_worker else None


def worker_library(rows: list) -> list:
    """*rows* of the Library leg, on a worker Hive only those inside ``Knowledge/Library``
    (the synced General Brain; the rest of its Knowledge is other dispatches')."""
    from core.app_role import is_worker
    if not is_worker():
        return rows
    return [r for r in rows if str(r.get("source") or "").startswith("Library/")]


def stores_for(where: str, project: Optional[str]) -> tuple[list[str], dict[str, str]]:
    """(the stores to search, {store: why it is not searched}) for ``--in``."""
    names = list(STORES) if where == "all" else [w.strip() for w in where.split(",") if w.strip()]
    chosen: list[str] = []
    notes: dict[str, str] = {}
    for n in names:
        s = STORES.get(n)
        if s is None:
            notes[n] = "no such store (see: cognition map)"
        elif worker_refusal(n):
            notes[n] = worker_refusal(n)
        else:
            chosen.append(n)
    return chosen, notes


def find(where: str, query: str, *, project: Optional[str] = None, doc: Optional[str] = None,
         top: Optional[int] = None) -> tuple[list[Hits], dict[str, str]]:
    """Every chosen store's hits, in ``STORES`` order. In an ``all`` search the General
    Brain is always searched, and a project's DDD too when *project* is given."""
    chosen, notes = stores_for(where, project)
    multi = len(chosen) > 1 or where == "all"
    general_searched = multi and "ddd" in chosen
    out: list[Hits] = []
    for name in chosen:
        if multi and name == "ddd":
            from core.project_agent import GENERAL_BRAIN
            from core.addressing import is_general
            brains = [GENERAL_BRAIN] + ([project] if project and not is_general(project) else [])
            for b in brains:
                h = find_store("ddd", query, project=b, doc=doc if b == project else None,
                               top=top or MULTI_TOP, budget=MULTI_BUDGET)
                h.store = f"ddd:{b}"
                out.append(h)
            continue
        if STORES[name].needs_project and not project:
            notes[name] = "not searched: needs --project"
            continue
        out.append(find_store(name, query, project=project, doc=None if multi else doc,
                              top=top or (MULTI_TOP if multi else 8),
                              budget=MULTI_BUDGET if multi else 12_000,
                              general_searched=general_searched))
    return out, notes


# ── get ──────────────────────────────────────────────────────────────────

_STORE_RANK = {"memory": 0, "evolution": 1, "timeline": 2, "context": 3}
_ID_RE = re.compile(r"[0-9a-f]{8}")
_LOC_RE = re.compile(r"^(?P<path>.+):(?P<line>\d+)$")
# a Library location as find prints it: "<source>.md", or "<source>.md#<heading>" in a
# "not shown" note (recall_filter._pointer), with the note's " (N characters)" tail allowed
_LIB_RE = re.compile(r"^(?:Knowledge/)?(?P<src>[^#]+?\.md)(?:#(?P<heading>.*?))?"
                     r"(?:\s+\(\d+ characters\))?$")


def _unit_at(store: str, path: Path, line: int) -> list[Unit]:
    for u in sorted(file_units(store, path), key=lambda u: u.line):
        if u.line <= line < u.line + len(u.text.splitlines()):
            return [u]
    return []


def get(ref: str) -> list[Unit]:
    """Every ``.context`` unit *ref* names (an id, or ``path:line``: absolute, or relative
    to ``.context``), main files first and archives after. ``locate`` reads every ref
    ``find`` prints; this is its ``.context`` part."""
    ref = (ref or "").strip()
    stores = ("memory", "evolution", "timeline", "context")
    if _ID_RE.fullmatch(ref):
        found = [u for s in stores for u in store_units(s) if u.id == ref]
        return sorted(found, key=lambda u: ("archive" in u.doc, _STORE_RANK[u.store], u.doc, u.line))
    m = _LOC_RE.match(ref)
    if not m:
        return []
    raw = Path(m.group("path")).expanduser()
    path = raw if raw.is_absolute() else context_dir() / raw
    try:
        path = path.resolve()
    except OSError:
        return []
    store = _store_of(path)
    return _unit_at(store, path, int(m.group("line"))) if store else []


@dataclass
class Got:
    """What ``locate`` found: ``where`` is ``context``, a Brain's name or ``library``;
    ``units`` are ``Unit`` (context, Brain) or Library hit dicts; ``refused`` says why a
    store the ref names is not read here (a worker Hive)."""
    where: str = ""
    units: list = field(default_factory=list)
    refused: str = ""


def _brain_of(path: Path) -> Optional[str]:
    """The Brain whose dir holds *path* (resolved): the General Brain, or ``Projects/<P>``."""
    from core import addressing
    from core.project_agent import GENERAL_BRAIN
    lib = addressing.brain_root(GENERAL_BRAIN)
    if lib is not None and path.is_relative_to(lib.resolve()):
        return GENERAL_BRAIN
    from core.project_registry import get_projects_dir
    try:
        rel = path.relative_to(get_projects_dir().resolve())
    except (OSError, ValueError):
        return None
    name = rel.parts[0] if len(rel.parts) > 1 else ""
    if not name or addressing.is_general(name) or addressing.project_root(name) is None:
        return None
    return name


def locate(ref: str) -> Got:
    """The unit(s) any ref ``find`` prints names, read without ``--project``:

    - an id: the ``.context`` stores, then the General Brain, then the Library index
      (a project's DDD is not searched: its group in ``find`` names ``--project P``);
    - ``<source>.md`` or ``<source>.md#<heading>``: the Library index (``library_get``);
    - ``<path>:<line>``: absolute, relative to the workspace, or relative to ``.context``
      (for a ``.context`` file only): a ``.context`` store file, or a file of a Brain
      (the General Brain, ``Projects/<P>``) read through ``addressing.matches``.

    Nothing outside those files is read. On a worker Hive the ``.context`` stores are
    refused (``refused``), the Brains and the Library inside ``Knowledge/Library`` are
    read, as ``find`` there does."""
    from core import addressing
    from core.project_agent import GENERAL_BRAIN
    ref = (ref or "").strip()
    refused = worker_refusal("memory") or ""
    if _ID_RE.fullmatch(ref):
        found = [] if refused else get(ref)
        if found:
            return Got("context", found)
        found = addressing.matches(GENERAL_BRAIN, ref)
        if found:
            return Got(GENERAL_BRAIN, found)
        found = library_get(unit_id=ref)
        return Got("library", found) if found else Got(refused=refused)
    lib = _LIB_RE.match(ref)
    if lib and (lib.group("heading") is not None or not _LOC_RE.match(ref)):
        found = library_get(source=lib.group("src"), heading=lib.group("heading"))
        return Got("library", found) if found else Got()
    m = _LOC_RE.match(ref)
    if not m:
        return Got()
    from core.project_registry import get_swarmws
    raw = Path(m.group("path")).expanduser()
    line = int(m.group("line"))
    tries = [(raw, True)] if raw.is_absolute() else [(context_dir() / raw, False),
                                                     (get_swarmws() / raw, True)]
    for cand, any_store in tries:
        try:
            path = cand.resolve()
        except OSError:
            continue
        if not path.is_file():
            continue
        store = _store_of(path)
        if store:
            return Got(refused=refused) if refused else Got("context", _unit_at(store, path, line))
        brain = _brain_of(path) if any_store else None
        if brain:
            return Got(brain, addressing.matches(brain, f"{path}:{line}"))
    return Got()


def library_get(*, unit_id: Optional[str] = None, source: Optional[str] = None,
                heading: Optional[str] = None) -> list[dict]:
    """Library hits from the Knowledge index, as ``find`` returned them: by *source*
    (relative to ``Knowledge/``) and, when given, *heading* -- one indexed query over that
    source's chunks -- or by *unit_id*, the id ``find`` printed. An id is looked up only in
    what ``find`` recorded when it printed the hit (``record_library_refs``: its source and
    heading), then read as above: the index is never scanned for an id, since an id is a
    hash of the shaped section and cannot be searched for. An id ``find`` never recorded
    here (printed on another machine, or the record was lost) is not found; its location
    ``<source>#<heading>``, printed beside it, still is.

    Each hit is shaped by the same ``recall_multi._knowledge_hit_shape`` steps and named
    by the same ``run_knowledge.item_id`` as the Library leg, over the same rows (the
    index's Knowledge namespaces; a project's repo documents never), so an id ``find``
    printed names the same text here. On a worker Hive only rows inside
    ``Knowledge/Library``. No index on this machine: nothing (one is never created).
    Each hit: ``{id, source, heading, text, external?}``."""
    if unit_id is not None:
        where = recorded_library_ref(unit_id)
        if where is None:
            return []
        source, heading = where
        return [h for h in library_get(source=source, heading=heading) if h["id"] == unit_id]
    if not source:
        return []
    import sqlite3
    from jobs.paths import DB_PATH
    if not DB_PATH.is_file():
        return []
    from core.knowledge_store import BIND_KEY_PREFIX
    from core.recall_multi import _knowledge_shape_text, _knowledge_text, is_external_source
    from core.run_protocol.run_knowledge import item_id
    from core.app_role import is_worker
    from core.vec_db import open_vec_db
    if source.startswith(BIND_KEY_PREFIX) or (is_worker() and not source.startswith("Library/")):
        return []
    # (source_file, chunk_index) is the table's unique index: one source is a range read
    sql = "SELECT source_file, heading, content FROM knowledge_chunks WHERE source_file = ?"
    args: list = [source]
    if heading is not None:
        sql += " AND COALESCE(heading, '') = ?"
        args.append(heading)
    sql += " ORDER BY chunk_index"
    try:
        with open_vec_db() as conn:
            rows = conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        logger.warning("cognition: the Knowledge index could not be read", exc_info=True)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    text = _knowledge_text(source) if rows else None       # the one file, read once
    for src, head, content in rows:
        whole = _knowledge_shape_text(src, head or "", content, text)["content"] or content
        uid = item_id(whole)
        if uid in seen:
            continue
        seen.add(uid)
        hit = {"id": uid, "source": src, "heading": head or "", "text": whole}
        if is_external_source(src):
            hit["external"] = True
        out.append(hit)
    return out


# ── where find saw each Library hit (so get reads an id without scanning) ────

LIBRARY_REFS_MAX_BYTES = 1_000_000    # past this the record is rewritten with its newest ids,
                                      # half this size, so the next rewrite is far off


def library_refs_path() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "state" / "cognition_library_refs.jsonl"


def record_library_refs(rows: list) -> None:
    """Record ``{id, source, heading}`` of each Library hit with an id in *rows* (the
    Library leg's rows: ``find`` and a run's knowledge files print these ids), so
    ``get <id>`` reads its source's chunks only. Append-only JSON lines, one write per
    call; past ``LIBRARY_REFS_MAX_BYTES`` the file is rewritten (atomically) with the
    newest ids that fit half of it (an id found again counts as new). A derived cache of the index's own locations: never
    fails the search that records it."""
    import json
    import os
    lines = [json.dumps({"id": r["id"], "source": r.get("source") or "",
                         "heading": r.get("heading") or ""}, ensure_ascii=False)
             for r in rows if r.get("id") and r.get("source")]
    if not lines:
        return
    path = library_refs_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if path.stat().st_size > LIBRARY_REFS_MAX_BYTES:
            newest: dict[str, str] = {}               # id -> its last line, oldest first
            for line in _read_refs(path):
                try:
                    uid = json.loads(line)["id"]
                except (ValueError, KeyError, TypeError):
                    continue
                newest.pop(uid, None)
                newest[uid] = line
            keep: list[str] = []
            room = LIBRARY_REFS_MAX_BYTES // 2
            for line in reversed(list(newest.values())):
                room -= len(line.encode("utf-8")) + 1
                if room < 0:
                    break
                keep.append(line)
            keep.reverse()
            tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            os.replace(tmp, path)
    except (OSError, ValueError):
        logger.warning("cognition: Library hit locations not recorded", exc_info=True)


def _read_refs(path: Path) -> list[str]:
    """The record's lines. A line cut short (a crash mid-append) is replaced, so it fails
    to parse and is skipped alone; split on newlines only, the one separator written."""
    return path.read_bytes().decode("utf-8", errors="replace").split("\n")


def recorded_library_ref(unit_id: str) -> Optional[tuple[str, str]]:
    """(source, heading) ``find`` last recorded for *unit_id*, or None."""
    import json
    try:
        lines = _read_refs(library_refs_path())
    except OSError:
        return None
    for line in reversed(lines):
        if unit_id not in line:                 # cheap test before parsing
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("id") == unit_id and row.get("source"):
            return str(row["source"]), str(row.get("heading") or "")
    return None


def library_location(hit: dict) -> str:
    """Where a Library hit is, as ``get`` reads it back: ``<source>#<heading>``. Only the
    source for an untrusted source (its heading is its text, printed outside the fence),
    a heading ``recall_filter._pointer`` leaves out (one starting with ``(``), or one that
    would not match the stored heading as printed (a newline, outer spaces)."""
    src = str(hit.get("source") or "")
    head = str(hit.get("heading") or "")
    from core.recall_multi import is_external_source, is_repo_doc_source
    if (not head or head.startswith("(") or head != head.strip() or "\n" in head
            or is_external_source(src) or is_repo_doc_source(src)):
        return src          # (a heading that would not read back as printed: the source)
    return f"{src}#{head}"


# ── map ────────────────────────────────────────────────────────────────────

def store_map() -> str:
    """What every store holds and how to search it, made now (never stored, never in a
    prompt). The program name is the one the agent called."""
    rows = ["Stores (search: cognition find --query \"<words of your solution>\" "
            "[--in <store>[,<store>...]|all] [--project P]):"]
    for s in STORES.values():
        if worker_refusal(s.name):
            continue
        line = f"- {s.name}: {s.about}"
        files = store_files(s.name)
        if files:
            n = sum(len(file_units(s.name, p)) for p in files)
            line += f" — {len(files)} file{'s' if len(files) != 1 else ''}, {n} units"
        if not s.can_get:
            line += " (find only)"
        rows.append(line)
    rows += ["Brains: cognition map --projects; one Brain: cognition map --project P.",
             "One unit: cognition get <id>|<path>:<line>|<Library source>[#<heading>]: every ref "
             "find prints, as printed; a project DDD's id needs --project P (its group says so). "
             "Cite what you use by its id."]
    return "\n".join(rows)


def render_hit(store: str, h, render_unit: Callable) -> str:
    """One hit as an agent reads it, inside the recalled-data fence."""
    from core import addressing
    if isinstance(h, Unit):
        return render_unit(h)
    if store == "library":
        ext = " (external)" if h.get("external") else ""
        return f"- `{h.get('id')}` · {library_location(h)}{ext}\n" + addressing.fence(h.get("text") or "")
    if store == "session":
        return f"- session {str(h.get('session_id') or '')[:8]} · {h.get('date') or ''}\n" + \
            addressing.fence(h.get("text") or "")
    if store == "code":
        callers = h.get("callers") or []
        body = (h.get("signature") or h.get("name") or "") + (
            f"\ncalled by: {', '.join(callers[:8])}" + (f" (+{len(callers) - 8} more)" if len(callers) > 8 else "")
            if callers else "")
        stale = " (graph behind HEAD)" if h.get("graph_stale") else ""
        return f"- {h.get('repo') or ''}/{h.get('file_path')}:{h.get('line')}{stale}\n" + addressing.fence(body)
    return addressing.fence(str(h))
