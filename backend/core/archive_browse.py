"""archive_browse — list + search the gitignored .context/*-archive*.md cold layer.

Run B (run_8f852625). The C&M overlay's Memory + Evolution tabs need to show WHAT
got archived (moved out of the always-injected live file by the size-valve) and let
the user RECALL it. Archived content is recall-backed cold storage — moved, not
deleted — so this module reads the shards for a list view and reuses the existing
FTS5 index for search.

Two archive families, DIFFERENT on-disk shapes (Gate-0 run_8f852625 verified against
real shards — do NOT assume one parser fits both):

- MEMORY archives (.context/MEMORY-archive-YYYY-MM.md): native bullet entries
  ``- [type] **title** — …`` with inline ``<!-- ref:N | last:date | decay:… -->``
  metadata. parse_entries() is their native parser.

- EVOLUTION archives (.context/EVOLUTION-archive*.md): ``### Fxxx | date`` /
  ``### Cxxx`` / ``### CLASS …`` BLOCK entries, each preceded by a
  ``<!-- size-evicted from <SECTION> -->`` provenance comment. parse_entries()
  returns [] on this shape (it only recognizes ``## `` sections + ``- `` bullets),
  so EVOLUTION shards get a dedicated block parser here.

Search is ARCHIVE-ONLY (XG decision): the live MEMORY/EVOLUTION/USER files are
full-injected already, so searching them is redundant AND a privacy concern. The
FTS5 index tags every archive with a ``.context/Archives/<name>`` source_file, and
active files are never indexed (the ``*-archive*`` allowlist in knowledge_store),
so filtering results to that prefix + the requested family is fail-closed correct.

ZERO LLM anywhere (deterministic list + FTS5/BM25 search) — archive stability is a
first-class invariant (XG: the archive must be stable/controllable, never LLM-judged).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

__all__ = ["list_archive_entries", "search_archive", "ARCHIVE_GLOBS"]

ArchiveSource = Literal["memory", "evolution"]

# The shard-name glob per family. MEMORY shards are strictly dated
# (MEMORY-archive-YYYY-MM.md); EVOLUTION shards include both the dated monthly
# shards and a legacy un-suffixed EVOLUTION-archive.md — so the evolution glob is
# the broader ``EVOLUTION-archive*``.
ARCHIVE_GLOBS: dict[str, str] = {
    "memory": "MEMORY-archive*.md",
    "evolution": "EVOLUTION-archive*.md",
}

# EVOLUTION block header: `### F001 | 2026-04-12` / `### C049 | 2026-08-11 [Bias A]`
# / `### CLASS B: …` / `### DATA-POINT — …`. Capture the header text + an optional
# trailing `| YYYY-MM-DD` or `[YYYY-MM-DD]` date.
_EVO_HEADER_RE = re.compile(r"^###\s+(.+?)\s*$")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Provenance comment written by the size-valve: `<!-- size-evicted from Failed Evolutions -->`
_PROVENANCE_RE = re.compile(r"<!--\s*size-evicted from\s+(.+?)\s*-->")


def _context_dir(ws_path: Path) -> Path:
    return ws_path / ".context"


def _shards(ws_path: Path, source: ArchiveSource) -> list[Path]:
    ctx = _context_dir(ws_path)
    if not ctx.is_dir():
        return []
    # Newest-first by name (dated shards sort lexically = chronologically; the
    # legacy un-dated EVOLUTION-archive.md sorts before the dated ones, which is
    # fine — it is the oldest cold storage).
    return sorted(
        (p for p in ctx.glob(ARCHIVE_GLOBS[source]) if p.is_file()),
        reverse=True,
    )


def _parse_memory_shard(text: str) -> list[dict]:
    """MEMORY archive shard → entries via the native bullet parser (parse_entries).

    parse_entries needs a `## ` section to attach bullets to; a raw MEMORY shard is
    bullets under a `# Memory Archive — YYYY-MM` H1 title with no `## ` section. We
    inject a synthetic `## Archived` section header so the bullets parse, without
    mutating the file (read-only view)."""
    from core.ddd_entry_lifecycle import parse_entries

    # Prepend a synthetic section so the flat bullet list parses. The H1 title line
    # (if any) is harmless — parse_entries ignores non-`## `/`- ` lines.
    synthetic = "## Archived\n" + text
    out: list[dict] = []
    try:
        for e in parse_entries(synthetic):
            out.append({
                "title": e.title,
                "type": e.entry_type,
                "date": e.last_referenced.isoformat() if e.last_referenced else None,
                "status": e.decay_state,
                "archived_from": "",  # MEMORY shards carry no per-entry provenance
            })
    except Exception:
        return []
    return out


def _parse_evolution_shard(text: str) -> list[dict]:
    """EVOLUTION archive shard → entries via a `### ` block parser.

    Each entry is a `### <header>` block, optionally preceded by a
    `<!-- size-evicted from <SECTION> -->` provenance comment. The date (if present)
    is a `YYYY-MM-DD` in the header; the type is the header's leading token family
    (F=failed-evolution, C=correction, CLASS/DATA-POINT/ROOT-CAUSE = their literal
    kind). We do NOT force the 7-type memory ontology (Gate-0: EVOLUTION has its own
    shape) — `type` here is a descriptive kind read from the header."""
    lines = text.splitlines()
    out: list[dict] = []
    pending_from = ""  # most recent provenance comment, applies to the next header
    for line in lines:
        prov = _PROVENANCE_RE.search(line)
        if prov:
            pending_from = prov.group(1)
            continue
        m = _EVO_HEADER_RE.match(line)
        if not m:
            continue
        header = m.group(1)
        date_m = _DATE_RE.search(header)
        date = date_m.group(1) if date_m else None
        # A short title = the header with the trailing date stripped for readability.
        title = header
        out.append({
            "title": title,
            "type": _evolution_kind(header),
            "date": date,
            "status": "archived",
            "archived_from": pending_from,
        })
        pending_from = ""  # consumed
    return out


def _evolution_kind(header: str) -> str:
    """Descriptive kind from an EVOLUTION header's leading token (NOT the 7-type
    memory ontology — EVOLUTION has its own vocabulary)."""
    h = header.strip()
    if h.startswith("CLASS"):
        return "class"
    if h.startswith("DATA-POINT") or h.startswith("DATA POINT"):
        return "data-point"
    if h.startswith("ROOT-CAUSE"):
        return "root-cause"
    if h.startswith("META-CORRECTION"):
        return "meta-correction"
    if h.startswith("DIRECTIVE"):
        return "directive"
    if re.match(r"^F\d", h):
        return "failed-evolution"
    if re.match(r"^C\d", h):
        return "correction"
    return "entry"


def list_archive_entries(ws_path: Path, source: ArchiveSource) -> dict:
    """List archived entries for a family. Empty-but-valid when no shards exist.

    Returns {entries: [...], total: int, shards: [names]}. NEWEST shard first; within
    a shard, document order (the archive's own newest-first append convention)."""
    shards = _shards(ws_path, source)
    entries: list[dict] = []
    for shard in shards:
        try:
            text = shard.read_text(encoding="utf-8")
        except OSError:
            continue
        rows = (
            _parse_memory_shard(text) if source == "memory"
            else _parse_evolution_shard(text)
        )
        for r in rows:
            r["shard"] = shard.name
        entries.extend(rows)
    return {
        "entries": entries,
        "total": len(entries),
        "shards": [p.name for p in shards],
        "source": source,
    }


def search_archive(query: str, source: ArchiveSource, limit: int = 30) -> dict:
    """FTS5/BM25 search over the ALREADY-INDEXED cold archive layer, ARCHIVE-ONLY.

    Reuses the shared knowledge FTS5 index (populated by context_health's
    _sync_knowledge_library from .context/*-archive*). Results are FAIL-CLOSED
    filtered to source_file startswith `.context/Archives/<FAMILY>-archive` so:
      • active MEMORY/USER/EVOLUTION (never indexed) can't appear, AND
      • Knowledge/ library hits (same index, different prefix) are excluded, AND
      • the two archive families are separated by the requested `source`.
    Zero LLM/embedding (pure FTS5 — PRI11). Empty-but-valid on no index / no query.
    """
    if not query or not query.strip():
        return {"results": [], "q": query, "source": source}

    from core.knowledge_store import KnowledgeStore
    from core.vec_db import open_vec_db

    # The shard-name prefix this family's indexed source_file starts with, under the
    # synthetic ".context/Archives/" index prefix (knowledge_store.py:598).
    fam_prefix = f".context/Archives/{'MEMORY' if source == 'memory' else 'EVOLUTION'}-archive"

    results: list[dict] = []
    try:
        with open_vec_db() as conn:
            if conn is None:
                return {"results": [], "q": query, "source": source}
            store = KnowledgeStore(conn)
            # Over-fetch then filter (the index holds all archives + Knowledge/), so
            # a family with few hits still fills up to `limit` after filtering.
            raw = store.fts5_search(query, limit=limit * 5)
            for r in raw:
                sf = r.get("source_file", "")
                if not sf.startswith(fam_prefix):
                    continue
                content = r.get("content", "") or ""
                results.append({
                    "title": (r.get("heading") or content[:60] or "").strip(),
                    "snippet": content[:300].strip(),
                    "source_file": sf,
                    "shard": sf.rsplit("/", 1)[-1],
                })
                if len(results) >= limit:
                    break
    except Exception:
        return {"results": [], "q": query, "source": source}

    return {"results": results, "q": query, "source": source}
