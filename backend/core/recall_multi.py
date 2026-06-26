"""Multi-domain READ-recall closure — unify recall across all knowledge domains.

SwarmAI has three pre-existing single-domain recall engines:
- ``context_recall.recall_context`` — the 11 context files (MEMORY.md etc), BM25+vector
- ``recall_engine.RecallEngine``     — the Knowledge Library (Notes/Learned/Signals), FTS5+vector
- ``session_recall.SessionRecall``   — past sessions, FTS5

Two domains had NO recall reader, and the three engines were invoked at three
scattered sites with no unified fan-out. This module closes that gap (READ-only):

- ``_ddd_section_scores``  — a GENERIC ``## section`` keyword scorer for DDD docs
  (Projects/*/{PRODUCT,TECH,IMPROVEMENT,PROJECT}.md). DDD docs are not MEMORY-keyed,
  so the MEMORY index scorer no-ops on them; this scores sections by BM25 over the
  section body text. Pure keyword — never embeds.
- ``_codeintel_recall``    — buckets ``load_project_graph().search_symbols`` +
  ``find_callers`` into a code-intel hit list. None-safe (no graph → empty bucket).
- ``recall_all``           — fans a query across all 5 domains into one
  ``BucketedRecall``. ``allow_embed=False`` by default → zero Bedrock embeds, zero
  writes (the READ-only anti-scope guard, run_4358cc95).

ANTI-SCOPE (hard): this module READS existing indexes/embeddings only. It never
triggers an embed (default), never persists a hit-log, never writes a used/verified
flag. Ingestion (the WRITE path) is a separate subsystem.

Public symbols:
- ``BucketedRecall``  — per-domain hits + per-domain hit_layer
- ``recall_all``      — the unified multi-domain fan-out
- ``DOMAINS``         — the canonical domain key tuple
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import at call sites for code_intel to keep import cost low + None-safe.
try:
    from core.code_intel import load_project_graph
except Exception:  # pragma: no cover - import guard
    load_project_graph = None  # type: ignore

DOMAINS = ("context_files", "ddd", "library", "session", "codeintel")


@dataclass
class BucketedRecall:
    """Result of a multi-domain recall fan-out.

    ``buckets`` maps each domain key to its hit list (shape varies per domain:
    text domains carry section/content dicts, codeintel carries symbol dicts).
    ``hit_layers`` maps each domain to how it matched ("keyword"/"hybrid"/
    "fts"/"graph"/"none") for observability — the multi-domain analogue of
    RecallResult.hit_layer.
    """

    query: str
    buckets: dict[str, list] = field(default_factory=dict)
    hit_layers: dict[str, str] = field(default_factory=dict)

    def any_hits(self) -> bool:
        return any(self.buckets.get(d) for d in DOMAINS)


# ── DDD domain: generic ##-section keyword scorer ─────────────────────


def _ddd_section_scores(query: str, doc_text: str) -> dict[str, float]:
    """Score the ``## sections`` of a DDD doc against ``query`` by BM25.

    DDD docs are plain ``## section`` markdown with no MEMORY-style keyed index,
    so the MEMORY index scorer (``_keyword_section_scores``) no-ops on them. This
    reuses the GENERIC pieces — ``parse_memory_sections`` (generic ## split) +
    ``_bm25_scores`` (scores any {key: text} corpus) + ``_normalize_bm25_scores``
    — to rank sections by their BODY text. Pure keyword: NEVER embeds.

    Returns {section_name: normalized_score in [0,1]}; empty if no section matches.
    """
    from core import memory_index

    sections = memory_index.parse_memory_sections(doc_text)
    if not sections:
        return {}
    # _bm25_scores takes {key: text}; section_name → section_body is exactly that.
    raw = memory_index._bm25_scores(query, sections)
    if not raw:
        return {}
    return memory_index._normalize_bm25_scores(raw)


# ── CodeIntel domain: bucket graph search results ─────────────────────


def _codeintel_recall(query: str, project: Optional[str] = None,
                      limit: int = 8) -> list[dict]:
    """Bucket code-graph search hits for ``query`` in ``project``.

    Wraps ``load_project_graph(project).search_symbols`` (FTS over symbol names)
    and enriches the top hit with its direct callers. Returns a list of symbol
    hit dicts ({name, id, file_path, rank, callers?}); EMPTY list when the
    project has no code graph (load_project_graph → None) — never auto-creates
    a DB, never crashes. Pure read over the existing graph index.
    """
    if not project or load_project_graph is None:
        return []
    try:
        graph = load_project_graph(project)
    except Exception as exc:  # noqa: BLE001 — best-effort domain
        logger.debug("codeintel recall: load_project_graph failed: %s", exc)
        return []
    if graph is None:
        return []  # project has no code_intel.db — unavailable, not empty-create

    try:
        hits = graph.search_symbols(query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("codeintel recall: search_symbols failed: %s", exc)
        return []

    # Enrich the top hit with direct callers (1 hop) for navigation context.
    if hits:
        try:
            callers = graph.find_callers(hits[0]["id"], depth=1)
            hits[0] = {**hits[0], "callers": [c[0] for c in callers[:5]]}
        except Exception as exc:  # noqa: BLE001
            logger.debug("codeintel recall: find_callers failed: %s", exc)
    return hits


# ── The unified multi-domain fan-out ──────────────────────────────────


def recall_all(
    query: str,
    *,
    project: Optional[str] = None,
    domains: tuple[str, ...] = DOMAINS,
    allow_embed: bool = False,
    max_sections: int = 3,
) -> BucketedRecall:
    """Fan ``query`` across all (or the requested) READ recall domains.

    Composes the existing per-domain verbs into ONE bucketed result. READ-only:
    ``allow_embed=False`` (default) guarantees no Bedrock embed and no write in
    ANY leg — context-files/DDD use keyword scoring, Library uses FTS5-only,
    session uses FTS5, codeintel reads the existing graph.

    Args:
        query: natural-language recall query.
        project: project name for the DDD + codeintel domains (e.g. "SwarmAI").
        domains: subset of DOMAINS to fan across (default: all 5).
        allow_embed: when True, the vector legs are enabled (Bedrock). Default
            False keeps the call provably embed-free (anti-scope).
        max_sections: per-text-domain section cap.

    Returns:
        BucketedRecall with one bucket + hit_layer per requested domain.
    """
    result = BucketedRecall(query=query)

    # context_files + ddd are markdown ##-section domains.
    if "context_files" in domains:
        result.buckets["context_files"], result.hit_layers["context_files"] = \
            _recall_context_files(query, allow_embed, max_sections)

    if "ddd" in domains:
        result.buckets["ddd"], result.hit_layers["ddd"] = \
            _recall_ddd(query, project, max_sections)

    if "library" in domains:
        result.buckets["library"], result.hit_layers["library"] = \
            _recall_library(query, allow_embed)

    if "session" in domains:
        result.buckets["session"], result.hit_layers["session"] = \
            _recall_session(query)

    if "codeintel" in domains:
        hits = _codeintel_recall(query, project=project)
        result.buckets["codeintel"] = hits
        result.hit_layers["codeintel"] = "graph" if hits else "none"

    return result


def _recall_context_files(query: str, allow_embed: bool,
                          max_sections: int) -> tuple[list, str]:
    """Recall over the 11 context files via the existing recall_context verb.

    Reads the live MEMORY.md (the highest-value excluded-section domain). Other
    context files are policy/budget-excluded per session; MEMORY is the canonical
    one a recall query targets. Keyword-only when allow_embed=False.
    """
    from pathlib import Path
    from core.context_recall import recall_context

    hits: list = []
    layer = "none"
    mem_path = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / "MEMORY.md"
    if not mem_path.exists():
        return hits, layer
    try:
        content = mem_path.read_text(encoding="utf-8")
    except OSError:
        return hits, layer
    res = recall_context("MEMORY.md", query, memory_content=content,
                         max_sections=max_sections, allow_embed=allow_embed)
    if res.allowed and res.sections:
        hits = [{"section": s} for s in res.sections]
        layer = res.hit_layer
    return hits, layer


def _recall_ddd(query: str, project: Optional[str],
                max_sections: int) -> tuple[list, str]:
    """Recall over a project's DDD docs via the generic ##-section scorer."""
    from pathlib import Path

    hits: list = []
    if not project:
        return hits, "none"
    base = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects" / project
    if not base.exists():
        return hits, "none"

    scored: list[tuple[str, str, float]] = []  # (doc, section, score)
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        p = base / doc
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for section, score in _ddd_section_scores(query, text).items():
            scored.append((doc, section, score))

    if not scored:
        return hits, "none"
    scored.sort(key=lambda t: t[2], reverse=True)
    hits = [{"doc": d, "section": s, "score": round(sc, 4)}
            for d, s, sc in scored[:max_sections]]
    return hits, "keyword"


def _recall_library(query: str, allow_embed: bool) -> tuple[list, str]:
    """Recall over the Knowledge Library via the existing RecallEngine.

    FTS5-only when allow_embed=False (embed_fn=None → vector leg skipped).
    """
    try:
        from core.knowledge_store import KnowledgeStore
        from core.recall_engine import RecallEngine
        from core.vec_db import open_vec_db
    except Exception as exc:  # noqa: BLE001
        logger.debug("library recall: import failed: %s", exc)
        return [], "none"

    try:
        with open_vec_db() as conn:
            if conn is None:
                return [], "none"
            store = KnowledgeStore(conn)
            engine = RecallEngine(store)
            embed_fn = None  # allow_embed=False → FTS5-only; never Bedrock here
            results = engine.search(query, embed_fn=embed_fn, top_k=8)
    except Exception as exc:  # noqa: BLE001
        logger.debug("library recall failed: %s", exc)
        return [], "none"

    if not results:
        return [], "none"
    hits = [{"source": r.get("source_file", ""), "heading": r.get("heading", ""),
             "score": round(r.get("score", 0.0), 4)} for r in results[:8]]
    return hits, "fts"


def _recall_session(query: str) -> tuple[list, str]:
    """Recall over past sessions via the existing SessionRecall (FTS5)."""
    try:
        from core.memory_index import _get_session_recall
        from jobs.paths import DB_PATH
    except Exception as exc:  # noqa: BLE001
        logger.debug("session recall: import failed: %s", exc)
        return [], "none"

    if not DB_PATH.exists():
        return [], "none"
    try:
        recall = _get_session_recall(DB_PATH)
        text = recall.recall_about(query, max_sessions=2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session recall failed: %s", exc)
        return [], "none"

    if not text:
        return [], "none"
    return [{"text": text[:500]}], "fts"
