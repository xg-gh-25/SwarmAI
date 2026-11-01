"""cognition — the stores an agent can read on demand, each as whole units.

Cognition entrance design (docs/design/2026-11-01-cognition-entrance.md) §3. One read
entrance for the whole brain: the ``cognition`` command (``knowledge`` is the same
command) reads every store below, each cut into whole units with ``path:line`` and the
8-character ``unit_id`` a pipeline document or an expert finding cites.

``STORES`` is the table of what can be read:

- ``ddd`` — a Brain: a project's DDD, or the General Brain (``Knowledge/Library``),
  through ``addressing`` (its unit table, map and ids);
- ``library`` — the full-text index of ``Knowledge/`` (``run_knowledge._library_leg``);
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

Each store is ranked on its own (BM25 over its own units, as ``addressing.find``); scores
of two stores are never compared, so a big store cannot push a small one out.

Who may read is not decided here: the command refuses whole in a session the daemon
marked (``knowledge_cli``). On a worker Hive only the Brains and code are read: its
memory, sessions and Knowledge belong to other dispatches (the same rule as its recall).
"""
from __future__ import annotations

import logging
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
                     "DailyActivity, Library ...)", "library", can_get=False),
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
            rows = _library_leg(query)
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


# ── get ────────────────────────────────────────────────────────────────────

_STORE_RANK = {"memory": 0, "evolution": 1, "timeline": 2, "context": 3}


def get(ref: str) -> list[Unit]:
    """Every ``.context`` unit *ref* names (an id, or ``path:line``), main files first and
    archives after. A Brain's units are read through ``addressing.matches``."""
    import re
    ref = (ref or "").strip()
    stores = ("memory", "evolution", "timeline", "context")
    if re.fullmatch(r"[0-9a-f]{8}", ref):
        found = [u for s in stores for u in store_units(s) if u.id == ref]
        return sorted(found, key=lambda u: ("archive" in u.doc, _STORE_RANK[u.store], u.doc, u.line))
    m = re.match(r"^(?P<path>.+):(?P<line>\d+)$", ref)
    if not m:
        return []
    raw = Path(m.group("path")).expanduser()
    path = raw if raw.is_absolute() else context_dir() / raw
    try:
        path = path.resolve()
    except OSError:
        return []
    store = _store_of(path)
    if store is None:
        return []
    line = int(m.group("line"))
    for u in sorted(file_units(store, path), key=lambda u: u.line):
        if u.line <= line < u.line + len(u.text.splitlines()):
            return [u]
    return []


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
             "One unit: cognition get <id>|<path>:<line> [--project P]. Cite what you use by its id."]
    return "\n".join(rows)


def render_hit(store: str, h, render_unit: Callable) -> str:
    """One hit as an agent reads it, inside the recalled-data fence."""
    from core import addressing
    if isinstance(h, Unit):
        return render_unit(h)
    if store == "library":
        ext = " (external)" if h.get("external") else ""
        return f"- `{h.get('id')}` · {h.get('source')}{ext}\n" + addressing.fence(h.get("text") or "")
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
