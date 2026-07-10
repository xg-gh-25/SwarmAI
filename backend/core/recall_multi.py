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

    # Freshness guard (pure-filesystem recall design §3.x/DoD5, 2026-06-28):
    # grep is always live; the AST graph can DRIFT (teammate rebase, a missed
    # watcher window, a failed incremental update) and then silently return STALE
    # edges — "confidently wrong", worse than grep. Before trusting the graph,
    # check it against HEAD (cheap `git rev-parse`). We do NOT block on staleness
    # (the index may still be useful) — we STAMP each hit with `stale` + `reason`
    # so the consuming agent knows "this graph is as-of an older commit, verify
    # against the live file". This is the code-layer cure for the same
    # stale-comment-fooled-me class the MEMORY line keeps hitting (R16b).
    stale_flag = False
    stale_reason = None
    try:
        from .code_intel.freshness import check_freshness
        fr = check_freshness(graph)
        stale_flag = bool(getattr(fr, "stale", False))
        stale_reason = getattr(fr, "reason", None)
        if stale_flag:
            logger.info(
                "codeintel recall: graph STALE vs HEAD (%s) — hits stamped "
                "stale=True so the agent verifies against live files",
                stale_reason or "unknown",
            )
    except Exception as exc:  # noqa: BLE001 — freshness is best-effort; never block recall
        logger.debug("codeintel recall: freshness check failed (non-fatal): %s", exc)

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

    # Stamp freshness on every hit (DoD5) — cheap, lets the agent discount stale edges.
    if stale_flag:
        hits = [{**h, "graph_stale": True, "stale_reason": stale_reason} for h in hits]
    return hits


# ── The unified multi-domain fan-out ──────────────────────────────────


def recall_all(
    query: str,
    *,
    project: Optional[str] = None,
    domains: tuple[str, ...] = DOMAINS,
    allow_embed: bool = False,
    max_sections: int = 3,
    policy_excluded_files: frozenset[str] = frozenset(),
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
        allow_embed: when True, the context_files vector leg is enabled (Bedrock).
            Default False keeps the call provably embed-free (anti-scope). NOTE:
            the ddd/library/session legs are keyword/FTS-only and have no vector
            path — only context_files honors this flag.
        max_sections: per-text-domain section cap.
        policy_excluded_files: files the current session excludes by POLICY
            (privacy), passed through to the file-reading legs so multi-domain
            recall enforces the SAME privacy gate as single-file recall_context.
            A leg whose source file is excluded returns an empty bucket. This
            closes the privacy leak where --domains bypassed the gate that --file
            enforces (run_4358cc95 Gate-2).

    Returns:
        BucketedRecall with one bucket + hit_layer per requested domain.
    """
    result = BucketedRecall(query=query)

    # context_files + ddd are markdown ##-section domains.
    if "context_files" in domains:
        result.buckets["context_files"], result.hit_layers["context_files"] = \
            _recall_context_files(query, allow_embed, max_sections,
                                  policy_excluded_files)

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


def _recall_context_files(query: str, allow_embed: bool, max_sections: int,
                          policy_excluded_files: frozenset[str] = frozenset(),
                          ) -> tuple[list, str]:
    """Recall over the 11 context files via the existing recall_context verb.

    Reads the live MEMORY.md (the highest-value excluded-section domain). Other
    context files are policy/budget-excluded per session; MEMORY is the canonical
    one a recall query targets. Keyword-only when allow_embed=False.

    Privacy: ``policy_excluded_files`` is passed to ``recall_context``, which
    HARD-DENIES (returns allowed=False, empty) when MEMORY.md is policy-excluded
    for this session (e.g. group_channel). This makes multi-domain recall enforce
    the same privacy gate as single-file recall — without it, --domains leaked
    MEMORY to sessions that --file correctly denies (run_4358cc95 Gate-2).
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
                         policy_excluded_files=policy_excluded_files,
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


def list_project_names() -> list[str]:
    """List SwarmWS project dir names (fs-scan, git-agnostic).

    fs-scan (not git) so an UNTRACKED project (e.g. CMHK_SalesIntel, kept out of
    git for privacy) is still discoverable. Skips dotfiles. (run_91bc0651 M2.)
    """
    from pathlib import Path

    base = Path.home() / ".swarm-ai" / "SwarmWS" / "Projects"
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def detect_active_project(
    editor_file_path: Optional[str] = None,
    query: Optional[str] = None,
    candidates: Optional[list[str]] = None,
) -> tuple[Optional[str], str]:
    """Detect the active project for runtime DDD recall. FAIL-CLOSED.

    Two signals, first hit wins (spec run_4a5a5cab §3.1; signal-2 project_id is
    vaporware — no session→thread wiring — so it is NOT implemented here):

      signal-1 (DETERMINISTIC): editor_file_path points into `Projects/<X>/` or
        `backend/skills/s_<x>-*` whose owning project is unambiguous.
      signal-3 (PROBABILISTIC): query keywords match EXACTLY ONE project's
        name/alias tokens. Ambiguous (≥2) or zero match → give up.

    Returns (project, signal_name) on a confident hit, else (None, reason).
    Ambiguity → (None, "ambiguous") so the caller injects NOTHING — a wrong
    project is worse than no project (fail-closed; never pollute context).
    """
    if candidates is None:
        candidates = list_project_names()
    if not candidates:
        return None, "no_projects"

    # ── signal-1: file path (deterministic) ──
    if editor_file_path:
        fp = editor_file_path.replace("\\", "/")
        # a) direct Projects/<X>/ path
        marker = "Projects/"
        if marker in fp:
            tail = fp.split(marker, 1)[1]
            proj = tail.split("/", 1)[0] if "/" in tail else tail
            if proj in candidates:
                return proj, "signal1_project_path"
        # b) skill path s_<x>-* → map to the business project that owns it
        if "backend/skills/s_" in fp:
            skill = fp.split("backend/skills/", 1)[1].split("/", 1)[0]
            # match a candidate whose derived prefix owns this skill dir
            for proj in candidates:
                pref = _project_skill_prefix(proj)
                if pref and skill.startswith(pref):
                    return proj, "signal1_skill_path"

    # ── signal-3: keyword disambiguation (probabilistic, fail-closed) ──
    if query:
        ql = query.lower()
        matched = [p for p in candidates if _project_matches_query(p, ql)]
        if len(matched) == 1:
            return matched[0], "signal3_keyword"
        if len(matched) >= 2:
            return None, "ambiguous"

    return None, "no_signal"


def _project_skill_prefix(project_name: str) -> Optional[str]:
    """`<domain>_<BizSuffix>` → `s_<domain>-` (business-project convention only).

    Delegates to the SINGLE source ddd_orchestrator._derive_skill_prefix (R25 —
    was a duplicated allowlist literal; drift risk if a suffix is added to one
    copy only, Gate-2 M2). signal-1's skill-path branch thus attributes a skill
    to the SAME project the WRITE side (staleness watch) does.
    """
    try:
        from core.ddd_orchestrator import _derive_skill_prefix
        return _derive_skill_prefix(project_name)
    except Exception:  # noqa: BLE001 — detection must never crash recall
        return None


def _project_matches_query(project_name: str, query_lower: str) -> bool:
    """True if the query mentions this project by a distinctive WHOLE-WORD token.

    Tokenizes the project name on separators (CMHK_SalesIntel →
    {cmhk, salesintel}); a token matches only as a WHOLE WORD in the query
    (\\b boundary), never a substring — so "aidlctastic" does NOT match AIDLC
    (Gate-2 M2: raw substring gave false single-project resolutions). ≥3-char
    tokens only. Deliberately strict — a false match pollutes recall, so this
    keeps the fail-closed bias.
    """
    import re

    tokens = [t for t in re.split(r"[_\-\s]+", project_name.lower()) if len(t) >= 3]
    return any(
        re.search(rf"\b{re.escape(t)}\b", query_lower) for t in tokens
    )


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
