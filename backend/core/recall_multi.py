"""Multi-domain READ-recall closure — unify recall across all knowledge domains.

SwarmAI has three pre-existing single-domain recall engines:
- ``context_recall.recall_context`` — the 11 context files (MEMORY.md etc), BM25+vector
- ``recall_engine.RecallEngine``     — the Knowledge Library (Notes/Learned/Signals), FTS5+vector
- ``session_recall.SessionRecall``   — past sessions, FTS5

Two domains had NO recall reader, and the three engines were invoked at three
scattered sites with no unified fan-out. This module closes that gap (READ-only):

- ``_ddd_section_scores_multi`` — the GENERIC ``## section`` keyword scorer for DDD
  docs (Projects/*/{PRODUCT,TECH,IMPROVEMENT,PROJECT}.md), scoring ALL docs in ONE
  shared corpus (comparable scores; run_9092cb25). The single-doc ``_ddd_section_scores``
  remains as a standalone unit (still directly tested). DDD docs are not MEMORY-keyed,
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

import json
import logging
import re
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


def _ddd_entry_hits(query: str, docs_text: dict[str, str], top_n: int) -> list[dict]:
    """Entry-level BM25 over the ``- [type]``/``- ``-prefixed entries of MULTIPLE
    DDD docs, scored in ONE shared corpus so scores are COMPARABLE across docs.

    The section leg (``_ddd_section_scores_multi``) scores whole ``## sections`` — so a
    fresh 1-line cultivated lesson buried in a 1000+-line section (IMPROVEMENT.md
    'What Failed'/'What Worked') is DILUTED to oblivion and unrecallable even by its
    exact words (run_97a6b1db: the loop's own cultivate→recall output was lost).
    This scores each ENTRY independently and emits the top matches as
    content-carrying hits, so a matching entry surfaces regardless of section size —
    the same entry-level cure context_recall already applies on the MEMORY read path.

    ``docs_text`` maps doc-name → full text; ALL entries across all docs share one
    ``_bm25_scores`` corpus (single normalization) — per-doc scoring would make a
    top entry in every doc tie at 1.0 and the cross-doc max pick arbitrarily
    (the cross-normalized-score trap). Returns [{"doc","section","score","content"}]
    highest-first; empty when no entry shares query vocabulary (precision > coverage —
    never emit head-biased non-matches). Pure keyword, never embeds.
    """
    import re as _re
    from core import memory_index

    entries: dict[str, str] = {}           # key → entry text
    entry_doc: dict[str, str] = {}         # key → owning doc
    entry_section: dict[str, str] = {}     # key → owning section name
    for doc, doc_text in docs_text.items():
        sections = memory_index.parse_memory_sections(doc_text)
        if not sections:
            continue
        for section, body in sections.items():
            # Split on line-anchored entry starts ('- ' at col 0); lookahead keeps
            # the delimiter so each chunk is a whole entry incl. its metadata line.
            for chunk in _re.split(r"(?m)^(?=- )", body):
                e = chunk.strip()
                if not e.startswith("- "):
                    continue
                k = str(len(entries))
                entries[k] = e
                entry_doc[k] = doc
                entry_section[k] = section
    if not entries:
        return []
    raw = memory_index._bm25_scores(query, entries)   # ONE corpus → comparable scores
    if not raw:
        return []  # no entry shares vocabulary → precision: emit nothing
    norm = memory_index._normalize_bm25_scores(raw)
    ranked = sorted(norm, key=lambda k: norm[k], reverse=True)[:max(1, top_n)]
    return [{"doc": entry_doc[k], "section": entry_section[k], "score": norm[k],
             "content": entries[k]} for k in ranked if norm[k] > 0]


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


def _ddd_section_scores_multi(query: str,
                              docs_text: dict[str, str]) -> list[tuple[str, str, float]]:
    """Score the ``## sections`` of MULTIPLE DDD docs in ONE SHARED corpus, so the
    scores are COMPARABLE across docs (run_9092cb25).

    The per-doc ``_ddd_section_scores`` normalizes each doc independently → every
    doc's top section pegs at 1.0 regardless of absolute relevance. So on an
    implementation query, PRODUCT.md's weakly-matching marketing section
    ("Strategic Positioning") scored 1.0 and tied/beat TECH.md's genuinely-relevant
    "Architecture" (also 1.0) and crowded out the domain layer. Pooling every
    section into one ``{doc::section: body}`` corpus and normalizing ONCE fixes the
    incomparability — the marketing section falls to its true (low) rank.

    Same shared-corpus fix pattern as ``_ddd_entry_hits`` (run_97a6b1db). Pure
    keyword: NEVER embeds. Returns ``[(doc, section, score in [0,1]), ...]``.
    """
    from core import memory_index

    corpus: dict[str, str] = {}
    key_map: dict[str, tuple[str, str]] = {}
    for doc, text in docs_text.items():
        for section, body in memory_index.parse_memory_sections(text).items():
            key = f"{doc}\x00{section}"  # NUL-join: unambiguous split (no path/section collision)
            corpus[key] = body
            key_map[key] = (doc, section)
    if not corpus:
        return []
    raw = memory_index._bm25_scores(query, corpus)
    if not raw:
        return []
    out: list[tuple[str, str, float]] = []
    for key, score in memory_index._normalize_bm25_scores(raw).items():
        doc, section = key_map[key]
        out.append((doc, section, score))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


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


# ── Render layer: BucketedRecall → injectable system-prompt string ────
# C-full M1 (run_ccd1b6c5): the unified runtime-injection path renders the
# 5-domain bucket into ONE provenance-tagged string. Replaces the two divergent
# formatters (RecallEngine.recall_knowledge string vs raw buckets). Each domain
# gets a labeled block; a [RECALLED] header marks the whole as retrieved (not
# this-turn reasoning); the project-DDD block additionally carries [DDD:<proj>].
# Empty buckets produce NO output (no noise). Pure formatting, no I/O.

_DOMAIN_LABELS = {
    "context_files": "Memory (MEMORY.md)",
    "ddd": "Project DDD",
    "library": "Knowledge Library",
    "session": "Past Sessions",
    "codeintel": "Code Symbols",
}

# Query tokens that IMPLY a recall domain should have matched (run_3416ad35,
# steal-from-gbrain gap-analysis). Used ONLY to decide whether an EMPTY requested
# leg is SURPRISING enough to surface a coverage line — never for retrieval.
# Gate-1 lesson: a coverage line on EVERY partial hit is wallpaper (1-2 of 4 legs
# typically hit → fires on most recalls → agent tunes it out). Emit ONLY when a
# query token implies the specific leg that came back empty → signal, not noise.
# Conservative by design (false-negative-biased): a miss here just means no line,
# never a wrong line.
#
# Matching is WHOLE-WORD (Gate-2 RP42 fix, run_3416ad35): each token is matched
# with `\b<token>\b` against the lowercased query, so short tokens can't
# substring-collide inside unrelated words ("code"∌encode/barcode,
# "import"∌important, "paper"∌newspaper, "reference"∌preference). Multi-word
# tokens ("we discussed") work identically under \b…\b. File-extension tokens
# (".py") use a leading \b + literal dot so ".py" matches "foo.py" and "a .py
# file" but not "occupy". Single generic nouns that carry no domain intent on
# their own ("code" alone, bare "the design") were dropped in favour of
# intent-bearing phrases — a missed weak signal is acceptable (false-negative
# bias); a false line is not.
_DOMAIN_EXPECT_TOKENS = {
    "codeintel": (
        "symbol", "function", "class", "module", "endpoint",
        "def", "docstring", ".py", ".ts", ".tsx", ".rs",
    ),
    "library": (
        "article", "the paper", "research paper", "the note", "knowledge base",
    ),
    "session": (
        "last time", "we discussed", "prior session", "we talked",
        "we decided", "earlier session", "previous session",
    ),
    "ddd": (
        "design decision", "tech spec", "product decision", "improvement.md",
        "architecture decision",
    ),
    "context_files": (
        "you said", "my preference", "remember that", "i told you",
    ),
}


def _query_implies_domain(query_lc: str, tokens: tuple[str, ...]) -> bool:
    """True if any token appears as a WHOLE WORD/PHRASE in the lowercased query.

    Whole-word (``\\b<token>\\b``) match, NOT raw substring — the RP42 fix
    (Gate-2, run_3416ad35): raw ``"code" in q`` false-fires inside
    encode/barcode/codebase; ``\\bcode\\b`` does not. ``re.escape`` makes the
    file-extension tokens (".py") literal, and ``\\b`` before the leading dot
    still anchors on the word→dot boundary ("foo.py" matches, "occupy" does not).
    """
    return any(
        re.search(r"\b" + re.escape(tok) + r"\b", query_lc) is not None
        for tok in tokens
    )


def render_bucketed_recall(
    result: "BucketedRecall",
    project: Optional[str] = None,
    graph_context: str = "",
) -> str:
    """Render a BucketedRecall into an injectable system-prompt block.

    Returns "" when there is nothing to inject (all buckets empty AND no graph
    context) — the caller then injects nothing (no empty "## Recalled" noise).

    Provenance:
      - a top-level ``[RECALLED]`` header (the whole block is retrieved context,
        NOT this turn's reasoning — the confabulation-boundary marker).
      - the DDD block additionally carries ``[DDD:<project>]`` so the project
        cognition layer is distinguishable from generic recall.
    """
    body = render_recall_body(result, project=project, graph_context=graph_context)
    if not body:
        return ""
    provenance = (
        "> **[RECALLED]** The block below is keyword/FTS-retrieved prior context "
        "— NOT this turn's reasoning and NOT new user input. Treat it as a lead "
        "to verify against source, not an established fact.\n\n"
    )
    return "## Recalled Knowledge\n" + provenance + body


def render_recall_body(
    result: "BucketedRecall",
    project: Optional[str] = None,
    graph_context: str = "",
) -> str:
    """The domain-blocks body of a rendered recall — WITHOUT the outer
    ``## Recalled Knowledge`` header + [RECALLED] provenance.

    Split out (C-full M2) so the runtime injection path can reuse the exact same
    5-domain rendering while keeping ITS OWN header/provenance/agentic-hint
    wrapping (single source of that wrapping stays in _maybe_inject_recall).
    Returns "" when nothing to render.
    """
    if result is None:
        return graph_context or ""
    parts: list[str] = []
    for domain in DOMAINS:
        hits = result.buckets.get(domain) or []
        if not hits:
            continue
        label = _DOMAIN_LABELS.get(domain, domain)
        header = (f"### {label} — [DDD:{project}]"
                  if domain == "ddd" and project else f"### {label}")
        parts.append(header + "\n" + _render_domain_hits(domain, hits))
    if graph_context:
        parts.append("### Graph-Connected\n" + graph_context)

    # Surprise-only coverage gap line (run_3416ad35). When recall FOUND something
    # (parts non-empty) but a REQUESTED leg came back empty AND a query token
    # implied that leg should have hit — surface it, so the agent distinguishes
    # "this leg had no match" from "this fact does not exist" (CLASS A′
    # recall-confabulation). Deterministic, zero-LLM, zero retrieval change.
    #   • requested = the domains actually queried (hit_layers ∪ buckets keys) —
    #     NEVER the full DOMAINS constant, so a leg not requested at runtime
    #     (e.g. ddd, excluded from the unified fan-out) is never a false gap.
    #   • surprising = requested AND empty AND query implies it (token match).
    #   • Gate-1 fix: NOT emitted on every partial hit (that was wallpaper) —
    #     only on a query-implied miss.
    if parts:
        q = (result.query or "").lower()
        requested = set(result.hit_layers.keys()) | set(result.buckets.keys())
        surprising = [
            _DOMAIN_LABELS.get(d, d)
            for d in DOMAINS
            if d in requested
            and not (result.buckets.get(d) or [])
            and _query_implies_domain(q, _DOMAIN_EXPECT_TOKENS.get(d, ()))
        ]
        if surprising:
            parts.append(
                "_Recall gap: your query implies "
                + ", ".join(surprising)
                + " but that leg returned no match — verify (grep/read) rather "
                "than assume it does not exist._"
            )
    return "\n\n".join(parts)


def _render_domain_hits(domain: str, hits: list) -> str:
    """Render one domain's hit list. Text domains emit content; codeintel emits
    symbol references; a hit missing content falls back to its pointer fields."""
    lines: list[str] = []
    for h in hits:
        if domain == "codeintel":
            ref = h.get("id") or h.get("name", "?")
            callers = h.get("callers") or []
            stale = " ⚠️STALE" if h.get("graph_stale") else ""
            line = f"- `{ref}`{stale}"
            if callers:
                line += f" (callers: {', '.join(str(c) for c in callers[:5])})"
            lines.append(line)
        elif domain == "session":
            txt = (h.get("text") or "").strip()
            if txt:
                lines.append(txt)
        else:
            # context_files / ddd / library: prefer real content, else pointer.
            content = (h.get("content") or "").strip()
            if content:
                lines.append(content)
            else:
                src = h.get("doc") or h.get("source") or ""
                sec = h.get("section") or h.get("heading") or ""
                ptr = " § ".join(x for x in (src, sec) if x)
                if ptr:
                    lines.append(f"- {ptr}")
    return "\n".join(lines)


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
    from core.context_recall import recall_context
    from core.project_registry import get_swarmws

    hits: list = []
    layer = "none"
    mem_path = get_swarmws() / ".context" / "MEMORY.md"
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
        # Carry the actual recalled CONTENT (res.content), not just the section
        # NAMES (res.sections). C-full M1 (run_ccd1b6c5): the bucket previously
        # kept only names — so a consumer that renders the bucket got "COE
        # Registry\nPitfalls" instead of the 5.8K of real text in res.content.
        # That made this bucket near-useless for injection and would have
        # REGRESSED runtime recall when C-full points it at recall_all. The
        # content is one string across the matched sections (recall_context's
        # own contract); attach it once + keep the section names for provenance.
        hits = [{"section": s} for s in res.sections]
        hits[0]["content"] = res.content  # full matched text, for rendering
        layer = res.hit_layer
    return hits, layer


def _recall_ddd(query: str, project: Optional[str],
                max_sections: int) -> tuple[list, str]:
    """Recall over a project's DDD docs via the generic ##-section scorer."""
    from core.project_registry import get_projects_dir

    hits: list = []
    if not project:
        return hits, "none"
    base = get_projects_dir() / project
    if not base.exists():
        return hits, "none"

    from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
    from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)
    scored: list[tuple[str, str, float]] = []  # (doc, section, score)
    # entry_hits carry CONTENT (the entry text) so a fresh cultivated lesson buried
    # in a huge section is recallable by its own words, not diluted at section-BM25
    # (run_97a6b1db). Reserved a slot below like the domain leg so it isn't crowded
    # out by whole-doc section scores at the default max_sections.
    _docs_text: dict[str, str] = {}
    for doc in DDD_CANONICAL_DOCS:
        p = ddd_path(base, doc)
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        _docs_text[doc] = text
    # Shared-corpus section scoring (run_9092cb25): normalize ONCE across all
    # canonical docs so scores are comparable — a weakly-matching marketing section
    # can no longer peg at 1.0 per-doc and crowd out a genuinely-relevant section /
    # the domain layer. (Was: per-doc _ddd_section_scores → every doc's top = 1.0.)
    scored.extend(_ddd_section_scores_multi(query, _docs_text))
    # Entry-level leg: score ALL docs' entries in one shared corpus (comparable
    # scores across docs) so a matching cultivated lesson surfaces from any doc.
    entry_hits: list[dict] = _ddd_entry_hits(query, _docs_text, max_sections)

    # ② KNOWLEDGE deep-reference leg: s_ddd-persist routes "reference material →
    # Projects/<X>/Knowledge/" — without this scan that write target is unrecallable
    # (the write/read-mismatch bug class, same as the pollinate one). Knowledge files
    # are whole-file blobs (no ## sections), so they're scored WHOLE-file via _bm25
    # ({rel_path: text}), NOT _ddd_section_scores (which no-ops on section-less docs).
    # BOUNDED (hot path — _recall_ddd runs on every recall_all fan-out): cap file count
    # + skip large files, so a big Knowledge/ tree can't blow up recall I/O.
    kdir = ddd_path(base, "knowledge")
    if kdir.is_dir():
        from core import memory_index
        _K_MAX_FILES = 60          # AIDLC has ~45; cap keeps the hot path bounded
        _K_MAX_BYTES = 64 * 1024   # skip an oversized note (belongs in the Library, not here)
        kdocs: dict[str, str] = {}
        for kp in sorted(kdir.rglob("*.md"))[:_K_MAX_FILES]:
            try:
                if kp.stat().st_size > _K_MAX_BYTES:
                    continue
                kdocs[str(kp.relative_to(base))] = kp.read_text(encoding="utf-8")
            except OSError:
                continue
        if kdocs:
            raw = memory_index._bm25_scores(query, kdocs)
            # Downweight: Knowledge/ is a SEPARATELY-normalized corpus, so its top file
            # also hits ~1.0 and would crowd out the 4 judgment docs at a low max_sections.
            # The 4 docs (product/tech/lesson/decision judgment) are primary; deep-reference
            # material is supporting — bias the pooled ranking toward the docs. (Gate-2 #3.)
            _K_WEIGHT = 0.7
            for rel, score in memory_index._normalize_bm25_scores(raw).items():
                scored.append((rel, "(whole file)", score * _K_WEIGHT))

    # ② GOVERNED-ASSET moat leg (six-section redesign, run_3a636c88): a data-agent
    # DDD's moat — its L3 data-semantic contract — lives at assets/<kind>/ (e.g.
    # CMHK's assets/data-source/data-contract/*.md). This is the DDD's DOMAIN
    # JUDGMENT (entity disambiguation "CMHK != China Mobile HK", the 6-gate output
    # self-check the AGENT runs) — exactly what a session touching the domain should
    # RECALL even without the skill loaded (PRI01: the moat's judgment reaches my
    # judgment). Read via ddd_asset_path (strangler: assets/ new, 4-capabilities/
    # or skills/ old pkg). BOUNDED STRICTER than the knowledge leg (contract tables
    # are denser/less narrative) + WEIGHTED LOWER (supporting reference, must not
    # crowd the 4 judgment docs — Gate-0 anti-pollution). rel-path carries the
    # assets/ prefix so it can never alias a Knowledge/ file (no double-count).
    from core.ddd_paths import ddd_asset_path
    dc_dir = ddd_asset_path(base, "data-source", old_rel="4-capabilities/s_cmhk-data-proxy") / "data-contract"
    if dc_dir.is_dir():
        from core import memory_index
        _DC_MAX_FILES = 20          # a data-contract is fewer/smaller files than Knowledge/
        _DC_MAX_BYTES = 32 * 1024   # stricter skip — contract tables are data, not narrative
        dcdocs: dict[str, str] = {}
        for dp in sorted(dc_dir.rglob("*.md"))[:_DC_MAX_FILES]:
            try:
                if dp.stat().st_size > _DC_MAX_BYTES:
                    continue
                dcdocs[str(dp.relative_to(base))] = dp.read_text(encoding="utf-8")
            except OSError:
                continue
        if dcdocs:
            raw_dc = memory_index._bm25_scores(query, dcdocs)
            _DC_WEIGHT = 0.5        # below _K_WEIGHT (0.7): machine-contract < cultivated judgment
            for rel, score in memory_index._normalize_bm25_scores(raw_dc).items():
                scored.append((rel, "(moat contract)", score * _DC_WEIGHT))

    # ③ code-intel domains[] leg (Run 3, §8.1): the code-intel.json business
    # semantic layer (domains/flows/steps) is a JSON EXPORT — the codeintel graph
    # leg reads code_intel.db (SQLite symbols), NEVER this file, so domains[] was
    # a recall orphan (verified SUPPORTED, run_6602eeab). Score it here, in the
    # ddd bucket, so business-rule/issue/gap content surfaces. verified:false
    # assertions are GATED (rendered as [llm-inferred, UNVERIFIED], not fact).
    # Tracked separately (run_89e28075): the domain/spec legs are the SPECIALIZED
    # business-semantic layer — at the default max_sections=3 they lose the flat
    # ranking to whole-doc BM25 (which normalizes to ~1.0) and get crowded out
    # ENTIRELY, defeating the DoD ("recall 命中其独有业务词"). So the single best
    # domain/spec hit gets a RESERVED slot below (never crowded to zero).
    domain_scored: list[tuple[str, str, float]] = []
    for dh in _score_domains(query, base, max_sections):
        domain_scored.append((dh["doc"], dh["section"], dh["score"]))

    # ④ spec-details [human]-block leg (Run 3, §8.1): index ONLY human-authored
    # blocks (backtick-fenced `[human]` list-items) — the [llm] skeleton is
    # already covered by leg ③ (domain leg), so a section-BM25 would double-hit
    # (r3 §8.1 correction). A [human] business rule that lives ONLY in a .spec.md
    # was unrecallable → orphan (§8.9 sentinel red baseline).
    for hh in _score_spec_details_human(query, base, max_sections):
        domain_scored.append((hh["doc"], hh["section"], hh["score"]))

    scored.extend(domain_scored)
    if not scored and not entry_hits:
        return hits, "none"
    scored.sort(key=lambda t: t[2], reverse=True)
    top = scored[:max_sections]
    # RESERVED SLOT: if the specialized domain/spec layer produced a positive-scoring
    # hit but the flat cut dropped ALL of them, graft the best one in (replacing the
    # weakest doc hit) so a business-flow query always surfaces its domain. Mirrors
    # leg ②'s anti-crowding intent, in the opposite direction. (run_89e28075 DoD.)
    domain_best = max(domain_scored, key=lambda t: t[2], default=None)
    if (max_sections >= 1 and domain_best is not None and domain_best[2] > 0
            and domain_best not in top):
        top = top[:max_sections - 1] + [domain_best]
        top.sort(key=lambda t: t[2], reverse=True)  # keep hits score-descending post-graft
    hits = [{"doc": d, "section": s, "score": round(sc, 4)}
            for d, s, sc in top]
    # RESERVED SLOT for the best ENTRY-level hit (run_97a6b1db): entry hits carry the
    # entry TEXT as `content`, so a fresh cultivated lesson buried in a giant section
    # surfaces verbatim rather than as a diluted section pointer. Graft the single
    # best matching entry if it isn't already represented by a section hit for the
    # same (doc, section) — mirrors the domain reserved-slot so it can't be crowded
    # out at the default max_sections.
    entry_best = max(entry_hits, key=lambda h: h["score"], default=None)
    if max_sections >= 1 and entry_best is not None and entry_best["score"] > 0:
        already = any(h.get("content") == entry_best["content"] for h in hits)
        if not already:
            entry_hit = {"doc": entry_best["doc"], "section": entry_best["section"],
                         "score": round(entry_best["score"], 4),
                         "content": entry_best["content"]}
            # Drop a bare section-POINTER hit for the SAME (doc, section) — the entry
            # carries that section's verbatim text, so keeping both renders the
            # content line AND a redundant "doc § section" pointer (Gate-2 LOW).
            hits = [h for h in hits
                    if not (not h.get("content")
                            and h.get("doc") == entry_hit["doc"]
                            and h.get("section") == entry_hit["section"])]
            hits = (hits[:max_sections - 1] + [entry_hit]) if hits else [entry_hit]
            hits.sort(key=lambda h: h["score"], reverse=True)
    return hits, "keyword"


# ── Run 3 §8.1: code-intel domains[] + spec-details [human] recall legs ──

# A [human] block = a markdown LIST ITEM that carries a BACKTICK-FENCED `[human]`
# marker. Two guards, both load-bearing (Gate-2 run_6602eeab):
#   - the backtick fence: the .spec.md legend / HTML-comment guidance mention the
#     bare word "[human]" WITHOUT backticks — indexing those is a false positive.
#   - the LIST-BULLET requirement: prose that quotes "`[human]`" (e.g. a legend
#     "the `[human]` marker denotes authorship") is NOT a rule — only a list item is.
_HUMAN_MARKER_RE = re.compile(r"`\[human\]`")
_LIST_BULLET_RE = re.compile(r"^(?:[-*+]\s|\d+\.\s)")
_HTML_COMMENT_INLINE_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _extract_human_blocks(spec_text: str) -> list[str]:
    """Return the human-authored lines of a .spec.md — ONLY markdown list items
    whose text carries a backtick-fenced ```[human]``` marker.

    Marker-aware, NOT section-based (§8.1 r3 correction): the [llm] skeleton is
    covered by the domain leg, so a per-section BM25 would double-hit. We index
    the [human] delta only.

    Robust to inline/multiline HTML comments (Gate-2 fix): an INLINE `<!-- … -->`
    is stripped from the line (so a real rule with a trailing comment still
    indexes — the prior version dropped the whole line = false negative); only an
    UNCLOSED `<!--` opens a multiline skip region. A bare/legend `[human]` (no
    backticks) or a non-list prose mention (no bullet) is NOT a block.
    """
    blocks: list[str] = []
    in_html_comment = False
    for raw in spec_text.splitlines():
        line = raw.strip()
        if in_html_comment:
            # Inside a multiline comment: it ends when a --> appears; residue
            # after the --> is real content and re-enters normal processing.
            if "-->" in line:
                in_html_comment = False
                line = line.split("-->", 1)[1].strip()
            else:
                continue
        # Strip any fully-closed inline comment(s), then detect a dangling opener.
        line = _HTML_COMMENT_INLINE_RE.sub("", line)
        if "<!--" in line:
            in_html_comment = True
            line = line.split("<!--", 1)[0].strip()
        if not line:
            continue
        # A real [human] rule is a LIST ITEM with a backtick-fenced marker.
        if _LIST_BULLET_RE.match(line) and _HUMAN_MARKER_RE.search(line):
            blocks.append(line)
    return blocks


def _score_spec_details_human(query: str, base, max_sections: int) -> list[dict]:
    """BM25-score the [human] blocks of every ``spec-details/*.spec.md`` under a
    project. Each block is its own tiny document keyed by ``<file>#<n>`` so a hit
    points at the exact human rule. Returns [] when no spec-details dir / no
    [human] blocks. Pure keyword — never embeds. BOUNDED like the Knowledge leg.
    """
    from core.project_registry import SPEC_DETAILS_DIR

    sd = base / SPEC_DETAILS_DIR
    if not sd.is_dir():
        return []
    from core import memory_index
    _SD_MAX_FILES = 100
    _SD_MAX_BYTES = 256 * 1024  # per-file clamp (hot path — same guard as Knowledge leg)
    docs: dict[str, str] = {}
    for sp in sorted(sd.rglob("*.spec.md"))[:_SD_MAX_FILES]:
        try:
            if sp.stat().st_size > _SD_MAX_BYTES:
                text = sp.read_text(encoding="utf-8")[:_SD_MAX_BYTES]
            else:
                text = sp.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(sp.relative_to(base))
        for i, blk in enumerate(_extract_human_blocks(text)):
            docs[f"{rel}#{i}"] = blk
    if not docs:
        return []
    raw = memory_index._bm25_scores(query, docs)
    _HUMAN_WEIGHT = 0.9  # human business rules are high-value; near-parity with docs
    out = []
    for key, score in memory_index._normalize_bm25_scores(raw).items():
        fpath, _, idx = key.partition("#")
        out.append({"doc": fpath, "section": f"[human] block {idx}",
                    "score": score * _HUMAN_WEIGHT})
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:max_sections]


def _domain_corpus(dom: dict, flows: list, steps: list) -> str:
    """Flatten a domain + its flows/steps into one recall document string, with
    EVERY LLM-sourced assertion honestly provenance-marked (§8.1 verified gating +
    Run C semantic-boundary honesty) so no claim is ever surfaced as an established
    code fact — the recall analogue of the [RECALLED] provenance boundary / CLASS A′
    defense:
    - verified:true  → '[llm-claim] label: txt (anchor: `x`)' — the LLM asserted it
      AND gave a code POINTER, but the prose was NOT verified against the code (the
      guard only checks the anchor STRING is present; see
      ai_ready_helpers.check_llm_assertion_guards). NOT a machine-verified fact.
    - verified:false / bare → '[llm-inferred, UNVERIFIED] label: txt'.
    Mirrors _fmt_assertion_row so the recall corpus and the .spec.md render agree —
    neither ever emits a verified:true assertion as bare fact (Gate-2 F1, run_3b2c85a7).
    """
    parts: list[str] = [str(dom.get("name", "")), str(dom.get("summary", ""))]

    def _emit_assertions(items, label):
        for a in items or []:
            if isinstance(a, dict):
                txt = a.get("rule") or a.get("cond") or a.get("case") or a.get("issue") \
                    or a.get("note") or ""
                if a.get("verified") is True:
                    anc = str(a.get("anchor") or "").strip()
                    parts.append(f"[llm-claim] {label}: {txt}"
                                 + (f" (anchor: `{anc}`)" if anc else ""))
                else:
                    parts.append(f"[llm-inferred, UNVERIFIED] {label}: {txt}")
            elif isinstance(a, str):
                # bare-string rule (no verified flag) → treat as unadjudicated
                parts.append(f"[llm-inferred, UNVERIFIED] {label}: {a}")

    _emit_assertions(dom.get("business_rules"), "rule")
    _emit_assertions(dom.get("issues"), "issue")
    _emit_assertions(dom.get("gaps"), "gap")
    did = dom.get("id")
    for fl in flows:
        if fl.get("domain_id") == did:
            parts.append(str(fl.get("name", "")) + " " + str(fl.get("summary", "")))
            fid = fl.get("id")
            for st in steps:
                if st.get("flow_id") == fid:
                    parts.append(str(st.get("name", "")) + " " + str(st.get("summary", "")))
                    _emit_assertions(st.get("rules"), "rule")
                    _emit_assertions(st.get("preconditions"), "precond")
                    _emit_assertions(st.get("exceptions"), "exception")
    return "\n".join(p for p in parts if p.strip())


def _score_domains(query: str, base, max_sections: int) -> list[dict]:
    """BM25-score the domains[] of a project's ``code-intel.json`` (§8.1 domain
    leg). Each domain is one document (its flows/steps folded in). Returns [] when
    no code-intel.json / no domains[]. verified:false assertions are gated inside
    _domain_corpus. Pure keyword — never embeds.
    """
    ci = base / "code-intel.json"
    if not ci.is_file():
        return []
    try:
        doc = json.loads(ci.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    domains = doc.get("domains") or []
    if not domains:
        return []
    flows = doc.get("flows") or []
    steps = doc.get("steps") or []
    from core import memory_index
    corpus = {}
    for dom in domains:
        if isinstance(dom, dict) and dom.get("id"):
            corpus[dom["id"]] = _domain_corpus(dom, flows, steps)
    if not corpus:
        return []
    raw = memory_index._bm25_scores(query, corpus)
    _DOMAIN_WEIGHT = 0.85
    out = []
    for dom_id, score in memory_index._normalize_bm25_scores(raw).items():
        out.append({"doc": "code-intel.json", "section": f"domain {dom_id}",
                    "score": score * _DOMAIN_WEIGHT})
    out.sort(key=lambda d: d["score"], reverse=True)
    return out[:max_sections]


def list_project_names() -> list[str]:
    """List SwarmWS project dir names (fs-scan, git-agnostic).

    fs-scan (not git) so an UNTRACKED project (e.g. a privacy-held project kept
    out of git) is still discoverable. Skips dotfiles. (run_91bc0651 M2.)

    Root resolves via project_registry.get_projects_dir() (SWARMWS env override),
    the single source shared with _recall_ddd — no hardcoded ~/.swarm-ai path.
    """
    from core.project_registry import get_projects_dir

    base = get_projects_dir()
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
    # Carry `content` (the matched body), not just source/heading. C-full M1
    # (run_ccd1b6c5): the bucket dropped content, so rendering it gave the model
    # a bare file list instead of the recalled text — a regression vs the
    # production _recall_for_query which returns full content.
    hits = [{"source": r.get("source_file", ""), "heading": r.get("heading", ""),
             "content": r.get("content", ""),
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
