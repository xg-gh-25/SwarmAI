"""MEMORY.md parsing + BM25 keyword scoring for recall.

ARCHITECTURE (2026-08-14): live MEMORY.md is ALWAYS fully injected — there is
NO in-prompt index, NO selective/section-scored injection, NO L0/L1 tiering.
The old 3-layer "L0 Compact Index / L1 Section Selection" system and its
generator functions (generate_memory_index / keyword_relevance /
inject_index_into_memory / extract_index_from_memory) were DELETED. Size is
bounded on the WRITE side (distillation caps + the size-valve archiver), not by
injection-time section dropping. Recall is keyword-only — the vector leg was
removed the same day.

ARCHIVE REACHABILITY — several routes reach ``.context/*-archive*.md``, and the
recurring defect here is not naming the wrong one, it is writing a CLOSED list or a
system-wide "never". Two successive revisions of this docstring did exactly that (one
named a single mechanism, and an injected governance file propagated it as an absolute
"never body-BM25 over the archive"; the replacement said "TWO mechanisms" and denied a
third that ships an HTTP endpoint). So treat the list below as the routes MEASURED so
far, never as exhaustive — grep for the glob pattern to find the current set:

- FTS5 / `library` domain — `knowledge_store.sync_knowledge_index` globs the shards
  into the shared knowledge index. Early-returns unless the workspace ``Knowledge/``
  dir exists (a SIBLING of ``.context/``), so this route is not unconditional. It is the
  one route reached WITHOUT an explicit shard name: `session_router` passes every
  non-``ddd`` domain — `library` included — to `recall_all` on the session's first
  keyword-bearing message, so archived text can land in the prompt on its own. Bounded,
  though: once per session (latched), and a CHANNEL session skips recall entirely.
- Explicit body-BM25 — `context_recall_cli.py --file <shard>` reads the shard and hands
  its text to `_section_body_scores` below. Index-free, so it survives ``Knowledge/``
  being absent — but it is NOT universal, and the boundary is per-SHARD, not per-family:
  `parse_memory_sections` needs ``## `` headers, so a shard built from ``### `` blocks
  early-returns ``reason:"no sections parsed"`` before the scorer runs. Measured: MEMORY
  monthly shards mostly work, ``EVOLUTION-archive-<YYYY-MM>`` shards return "no sections
  parsed", and the undated legacy ``EVOLUTION-archive.md`` works. Check for ``## `` in the
  shard before relying on this route. Also query with BODY vocabulary — no section-name
  boost, so `--query Pitfalls` returns ``hit_layer:"none"`` on a shard whose sections are
  literally named Pitfalls.
- Direct shard read — `core/archive_browse.list_archive_files` (served by
  ``GET /api/eval/archive-list``) does `read_text` per shard with its own glob table and
  its own ``### ``-aware block parser, so it reads what the `--file` route cannot. Truly
  index-free. NOTE its sibling `search_archive` (``/api/eval/archive-search``) is NOT
  this route — it reuses the shared FTS5 index with a family filter, so it inherits the
  ``Knowledge/`` precondition above.

WHERE TO FIX THIS PROSE when it drifts again (durable, not a progress note): the same
statement lives in two injected governance files whose OWNERSHIP differs, so one edit
shape does not serve both — read ``context_directory_loader.CONTEXT_FILES`` first.
AGENT.md is ``user_customized=False`` → system-owned: edit the ``backend/context/`` seed
and rebuild; the live ``.context/`` copy is a projection that only changes on overwrite,
so a session can keep serving the old text until then. SELF.md is ``user_customized=True``
→ runtime-owned: the seed is copied ONLY when the live file is absent, so a seed-only
edit is a NO-OP for every existing install — the live ``.context/SELF.md`` is the
authoritative copy and must be edited directly.

Scoped negative (measured on ONE function, do not generalize): `_section_body_scores`
does no file I/O — the caller passes ``memory_content`` in. That constrains this
module's I/O and says nothing about which text reaches it; an archive shard the CLI
just read arrives here exactly like a live body.

This module now provides the surviving parse/score utilities used by that
recall path.

Public symbols:

- ``parse_memory_sections``        — Split MEMORY.md into named sections
- ``extract_body_without_index``   — Strip a legacy index block (still stripped
                                      on read; no longer generated)
- ``select_memory_sections``       — DEGRADED pass-through (returns the full
                                      body; kept as a contract-lock guard so a
                                      selective-mode regression is caught by tests)
- ``MEMORY_INDEX_START`` / ``MEMORY_INDEX_END`` — legacy block markers (used only
                                      to strip a legacy block, not to generate one)
"""

import hashlib
import re
import logging
import threading
from collections import OrderedDict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

# The index-block markers survive: the in-prompt index is no longer GENERATED
# (2026-08-14), but MEMORY.md files may still carry a legacy block, so
# extract_body_without_index() strips it on every recall/injection read.
MEMORY_INDEX_START = "<!-- MEMORY_INDEX_START -->"
MEMORY_INDEX_END = "<!-- MEMORY_INDEX_END -->"


# ── Section definitions: imported from single source of truth ─────────
from .ddd_entry_lifecycle import (
    MEMORY_PERMANENT_SECTIONS,
    MEMORY_ACTIVE_SECTIONS,
    MEMORY_EVERGREEN_SECTIONS,
    MEMORY_SECTION_NAMES,
    MEMORY_PREFIX_MAP,
    MEMORY_PREFIX_TO_SECTION,
)

# Re-export for backward compat with existing callers
PERMANENT_SECTIONS = MEMORY_PERMANENT_SECTIONS
ACTIVE_SECTIONS = MEMORY_ACTIVE_SECTIONS

# Sections always loaded in L1 regardless of keyword matching (CYCLE 2/7).
# = the MEMORY_EVERGREEN_SECTIONS SSOT (Principles / Corrections / COE Registry /
# Standing Preferences / Open Threads): query-INDEPENDENT judgment that shapes
# every turn, so it is injected in FULL every turn — never keyword-gated or
# budget-skipped. Derived from the schema (NOT a hand-list) so adding an evergreen
# section to MEMORY_SECTIONS auto-updates injection (P8 one-brain consistency).
# Was {"Open Threads"} only — which dropped Principles/Corrections on a keyword
# miss (the cross-language recall gap: a CJK query not matching a principle's
# English body silently omitted the whole Principles section).
ALWAYS_LOAD_SECTIONS = set(MEMORY_EVERGREEN_SECTIONS)

# Inert default retained only as select_memory_sections' now-inert max_tokens
# param default (MEMORY.md is ALWAYS full-injected — 2026-08-14; no selective
# mode). Kept because that function is a live contract-lock guard (its tests
# assert it returns the full body); its signature default must resolve.
DEFAULT_MAX_TOKENS = 50_000

# Prefix patterns: derived from single source of truth
SECTION_KEY_PREFIX = {
    **MEMORY_PREFIX_MAP,
    # Legacy names (backward compat — old MEMORY.md format still parseable)
    "Recent Context": "RC",
    "Key Decisions": "KD",
    "Lessons Learned": "LL",
    "COE Registry": "COE",
}

# Common stop words to filter from keyword matching
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "has", "his", "how",
    "its", "may", "new", "now", "old", "see", "way", "who", "did",
    "get", "let", "say", "she", "too", "use", "with", "this", "that",
    "from", "have", "been", "will", "more", "when", "what", "some",
    "them", "than", "each", "make", "like", "over", "such", "into",
    "just", "also", "back", "after", "only", "come", "made", "find",
    "here", "thing", "many", "well", "about", "which", "their",
    "there", "other", "could", "would", "should", "these", "those",
})


# ── Tokenization ─────────────────────────────────────────────────────

# The CJK class is OWNED by core.cjk_index and imported here, never re-declared.
# A local copy was the bug: this module kept the old ideographs-only range while
# cjk_index widened to kana + hangul, so the gate below never let a Korean or
# Japanese token reach the expansion it guards — `_bm25_tokenize('설정파일')`
# returned one token while `expand_cjk('설정파일')` returned three bigrams. That is
# precisely the cross-domain disagreement (Python-scored `context_files`/`ddd` vs
# FTS-scored `library`/`session`) that delegating to one authority exists to
# prevent, reintroduced by duplicating the constant.
from .cjk_index import _CJK_RE


def _tokenize_lower(text: str) -> list[str]:
    """Split text into lowercase tokens, filtering short/stop words.

    Unicode-aware: captures CJK characters as word constituents so
    Chinese/Japanese/Korean memory entries participate in keyword matching.
    """
    # \w with re.UNICODE includes CJK; add hyphen for compound terms
    tokens = re.findall(r"[\w\-]+", text.lower(), re.UNICODE)
    # CJK 2-char words (竞品, 周报) are meaningful → min 2.
    # ASCII 2-char tokens (is, at, to, pe) are usually noise → min 3.
    # Exception: ALL-CAPS 2-3 letter abbreviations in the ORIGINAL text
    # (PE, DB, SSE) would be useful, but we've already lowercased.
    # Those abbreviations appear as 3+ char tokens in aliases (e.g. "oom"),
    # so they still match via exact lookup. Acceptable tradeoff.
    return [t for t in tokens if t not in _STOP_WORDS and (
        len(t) > 2 or _CJK_RE.search(t)
    )]


# Mapping from ref prefix → MEMORY.md section name (from single source)
_REF_PREFIX_TO_SECTION: dict[str, str] = {
    **MEMORY_PREFIX_TO_SECTION,
    # Legacy prefixes (backward compat for old index entries)
    "KD": "Decisions",
    "RC": "Guidelines",
    "LL": "Pitfalls",
}




# ── Index Generation ──────────────────────────────────────────────────


_SECTION_HEAD_RE = re.compile(r"^##\s+(.+)$")


def section_name(heading: str) -> str:
    """A ``## `` heading's section name: a " — suffix" is dropped ("What Worked — Major
    Deliveries" -> "What Worked")."""
    return re.sub(r"\s*[—–-]\s+.*$", "", heading.strip())


def section_spans(text: str) -> list[tuple[str, int, int]]:
    """``(name, first body line, end)`` for each ``## `` section of *text* (0-based line
    indices into ``text.splitlines()``, end exclusive), in file order, the text before
    the first heading left out. A ``## `` line inside a code fence is code, not a
    heading. The one section split of recall and the knowledge unit table, so both cut
    a document the same way."""
    lines = text.splitlines()
    spans: list[tuple[str, int, int]] = []
    current, first, fence = None, 0, ""
    for i, line in enumerate(lines):
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
            continue
        m = None if fence else _SECTION_HEAD_RE.match(line)
        if m:
            if current is not None:
                spans.append((current, first, i))
            current, first = section_name(m.group(1)), i + 1
    if current is not None:
        spans.append((current, first, len(lines)))
    return spans


def parse_memory_sections(content: str) -> dict[str, str]:
    """Split MEMORY.md content into named sections.

    Returns dict mapping section name (e.g. "Recent Context") to its content.
    Strips the index block if present. Sections are cut by ``section_spans`` (a ``## ``
    inside a code fence is not a heading). A " — suffix" is dropped from each heading,
    so two headings can reduce to the same name (``## What Worked`` and ``## What
    Worked — Major Deliveries``): their bodies are joined in file order under that name,
    never overwritten -- a dict keyed by the name used to keep only the last one, which
    hid about 38% of SwarmAI's IMPROVEMENT.md from every DDD recall.
    """
    body = extract_body_without_index(content)
    lines = body.splitlines()
    sections: dict[str, str] = {}
    for name, first, end in section_spans(body):
        body_part = "\n".join(lines[first:end]).strip()
        if name in sections and sections[name]:
            if body_part:
                sections[name] = f"{sections[name]}\n\n{body_part}"
        else:
            sections[name] = body_part
    return sections




# ── Keyword Relevance ─────────────────────────────────────────────────




# ── Okapi-BM25 keyword scorer (run_1e2e663b, recall semantic upgrade) ──

# Standard Okapi-BM25 free parameters (Robertson/Sparck-Jones defaults).
BM25_K1 = 1.5  # term-frequency saturation
BM25_B = 0.75  # document-length normalization strength


def _bm25_tokenize(text: str) -> list[str]:
    """Tokenize for BM25: reuse the lexical tokenizer, but expand CJK runs into
    character bigrams so partial CJK matches still score (token-exact BM25 would
    miss '竞品分析陷阱' vs '竞品分析的结论' — they share no whole token, only the
    bigrams 竞品/品分/分析). Mirrors the CJK-flexibility ``keyword_relevance`` has,
    keeping the recall upgrade from REGRESSING bilingual matching.

    Delegates the actual segmentation to ``core.cjk_index`` so the codebase has
    ONE CJK policy. That module is also what the SQLite FTS5 indexes use, and two
    tokenizers that disagree about where a Chinese word ends would make the
    Python-scored domains (``context_files``, ``ddd``) and the FTS-scored domains
    (``library``, ``session``) answer the same query differently.

    The delegation also FIXES a defect that lived here: the old code bigram-sliced
    the WHOLE token whenever it contained any CJK, so ``用goal-pipeline跑`` became
    ``['用g','go','oa','al','l-','-p',...]`` — the embedded English word was
    destroyed and an English query could not match the document. The shared helper
    splits a mixed token into maximal CJK and non-CJK runs first. Pure-CJK and
    pure-ASCII output is byte-identical to before, so the two healthy domains keep
    their existing behaviour (pinned by tests, including a ranking-ORDER test:
    ``_bm25_scores`` derives avgdl and idf from the candidate corpus, so a token
    count change in ANY document re-normalizes every score).
    """
    from .cjk_index import expand_cjk

    out: list[str] = []
    for tok in _tokenize_lower(text):
        if _CJK_RE.search(tok):
            out.extend(expand_cjk(tok).split())
        else:
            out.append(tok)
    return out


# ── Doc-tokenize cache ───────────────────────────────────────────────────────
# `_bm25_scores` re-tokenizes EVERY doc on EVERY call (line below), but doc
# tokenization is a pure function of the doc CONTENT — query-independent. On the
# recall hot path (ddd leg: whole DDD corpus, ~260K tokens, CJK bigram expansion
# ≈376ms) that query-independent work was redone on every first-message recall.
# Cache it, keyed on the doc TEXT itself, so a repeated corpus is tokenized once.
#
# Content-keyed (not path/mtime): a doc whose text changes gets a DIFFERENT key →
# a fresh tokenization, so recall can never serve stale tokens (the "no stale
# recall" guarantee). Bounded (OrderedDict, LRU-evict oldest past the cap) — the
# cap is a MEMORY bound only; an evicted entry simply recomputes on next access,
# so eviction changes cache-hit rate, NEVER a result (the opposite of a
# STEERING #2 cost cap — it never truncates in-flight recall work).
#
# The QUERY tokenization in `_bm25_scores` (`q_terms = _bm25_tokenize(query)`)
# stays UNCACHED on purpose: the query varies every call and is short/cheap, so
# caching it would only grow the cache for zero reuse.
#
# ⚠️ CAP SIZING (learned the hard way): a single ddd-leg recall
# tokenizes the WHOLE DDD corpus split into sections + entries — ~2.7K distinct
# fragments for SwarmAI's own DDD today. The cap MUST exceed one recall's fragment
# working set, or the LRU evicts fragments from run N before run N+1 can reuse them
# → the cache thrashes and delivers ~0 speedup (an early 512 cap did exactly that:
# 8% instead of 65%). 16384 holds several recalls' worth with headroom for a
# growing corpus; each entry is a small token list, so the memory cost is modest
# (a few MB worst case). If a future corpus pushes a single recall's working set
# near this cap, RAISE it (it is a memory bound, never a correctness or
# recall-completeness bound — eviction only recomputes).
#
# RETENTION CONTRACT (Gate-2 security, LOW/defense-in-depth): this is a
# process-GLOBAL cache that retains the TOKENIZED content of whatever docs recall
# scored (DDD / MEMORY / Knowledge). Before this change those tokens were transient
# (GC'd after each `_bm25_scores` call); now they persist for the daemon's life,
# displaced only by LRU pressure or process exit — there is NO session-end/privacy
# hook that clears them (`_reset_doc_token_cache` is a test/manual hook). This
# deliberately matches the pre-existing `_session_recall_cache` precedent below (also
# process-lifetime, never auto-cleared), so it introduces no NEW privacy contract —
# but if a session-teardown clear is ever added for that cache, clear THIS one in the
# same place. It stores only tokenized text and is content-addressed (a caller can
# only get tokens back by already supplying the identical source text), so there is
# no cross-session READ path — the concern is in-memory retention, not an in-band leak.
_DOC_TOKEN_CACHE_MAX = 16384
_DOC_TOKEN_CACHE: "OrderedDict[str, list[str]]" = OrderedDict()


# Thrash-signal state: the cache delivers ~0 speedup if a SINGLE recall's working
# set exceeds the cap (it evicts fragments before the next run reuses them — the
# measured 512-cap failure). That regression is otherwise SILENT. `_doc_cache_saturated`
# latches on the first at-cap eviction so we WARN once per saturation episode (not per
# evict — that would spam), and re-arms when the cache drains back below cap.
_doc_cache_saturated = False

# Recall runs CONCURRENTLY from multiple threads — the io-worker pool
# (`executors.run_in("io", _unified_recall_body / _recall_for_query / ...)`) and
# `asyncio.to_thread` (library-overlay / ddd-brain recall). The cache's
# read-modify-write (`get` → `move_to_end` → insert → `popitem`) and the
# `_doc_cache_saturated` flag are check-then-act sequences spanning several
# bytecodes, so the GIL does NOT make them atomic: two threads can both pass the
# at-cap check and both `popitem` (second raises on an under-margin dict), or a
# `move_to_end(key)` can race a `popitem` of that same key → KeyError into the
# recall path. We guard the dict/flag mutations with a module `threading.Lock`,
# mirroring `recall_metrics._LOCK` (adopted for this exact dual-context recall
# concurrency). The expensive `_bm25_tokenize` stays OUTSIDE the lock so
# tokenization still runs in parallel; the lock covers only the O(1) structural ops.
_DOC_TOKEN_CACHE_LOCK = threading.Lock()


def _reset_doc_token_cache() -> None:
    """Clear the doc-tokenize cache (test hook; also safe to call at runtime)."""
    global _doc_cache_saturated
    with _DOC_TOKEN_CACHE_LOCK:
        _DOC_TOKEN_CACHE.clear()
        _doc_cache_saturated = False


def _bm25_tokenize_doc(text: str) -> list[str]:
    """Cached, content-keyed wrapper over `_bm25_tokenize` for DOC bodies.

    Returns EXACTLY what `_bm25_tokenize(text)` returns — this is a memoization
    layer over the same pure function, never a different tokenizer. The key is
    the doc content (``len`` prefix + blake2b digest — the length prefix makes a
    digest collision additionally require an equal-length text, driving the
    false-hit probability to negligible). Returns a COPY of the cached list every
    call, so a caller that mutates the result (e.g. ``.append``) cannot corrupt
    the stored tokens for the next reader (Gate-1 aliasing guard).

    Thread-safe: the dict/flag mutations are guarded by `_DOC_TOKEN_CACHE_LOCK`
    (recall runs from several io-pool / to_thread threads concurrently). The
    tokenize call on a miss is done OUTSIDE the lock to preserve parallelism — a
    rare duplicate tokenize of the same new text is harmless (pure function,
    identical result), whereas holding the lock across tokenize would serialize
    every recall leg.
    """
    global _doc_cache_saturated
    key = f"{len(text)}:{hashlib.blake2b(text.encode('utf-8'), digest_size=16).hexdigest()}"
    with _DOC_TOKEN_CACHE_LOCK:
        cached = _DOC_TOKEN_CACHE.get(key)
        if cached is not None:
            _DOC_TOKEN_CACHE.move_to_end(key)   # LRU: mark most-recently-used
            return list(cached)                 # copy — never hand out the shared list
    # Miss: tokenize OUTSIDE the lock (expensive; pure function → safe to race).
    toks = _bm25_tokenize(text)
    warn_saturated = False
    with _DOC_TOKEN_CACHE_LOCK:
        # Re-check: a concurrent miss on the same key may have inserted already.
        existing = _DOC_TOKEN_CACHE.get(key)
        if existing is not None:
            _DOC_TOKEN_CACHE.move_to_end(key)
            # Return the CANONICAL stored list, not our redundantly-computed `toks`.
            # They are equal today (`_bm25_tokenize` is pure), but returning the
            # stored value keeps the memoization layer correct even if the tokenizer
            # ever became nondeterministic — the cache's job is to hand back the one
            # value under the key.
            toks = existing
        else:
            _DOC_TOKEN_CACHE[key] = toks
            if len(_DOC_TOKEN_CACHE) > _DOC_TOKEN_CACHE_MAX:
                _DOC_TOKEN_CACHE.popitem(last=False)  # evict oldest (memory bound only)
                if not _doc_cache_saturated:
                    _doc_cache_saturated = True
                    warn_saturated = True  # emit the WARN after releasing the lock
            elif _doc_cache_saturated and len(_DOC_TOKEN_CACHE) < _DOC_TOKEN_CACHE_MAX:
                _doc_cache_saturated = False  # drained back below cap — re-arm the warning
    if warn_saturated:
        # Observability only — NOT a cap on work (STEERING #2): we never refuse
        # to tokenize, we only warn that the cache is churning so its speedup has
        # collapsed. The fix is to RAISE _DOC_TOKEN_CACHE_MAX (see cap-sizing note).
        # Logged outside the lock so logging I/O never serializes recall threads.
        logger.warning(
            "doc-tokenize cache saturated at %d entries and now evicting — "
            "a single recall's working set likely exceeds the cap, so the "
            "cache speedup has collapsed to ~0 (thrash). Raise "
            "_DOC_TOKEN_CACHE_MAX above one recall's fragment count.",
            _DOC_TOKEN_CACHE_MAX,
        )
    return list(toks)


def _bm25_scores(query: str, docs: dict[str, str]) -> dict[str, float]:
    """Score each doc against the query with Okapi-BM25 + corpus-relative IDF.

    IDF is computed over ``docs`` (the candidate set), so common terms are
    down-weighted relative to THIS corpus — the property the old token-overlap
    leg lacked. Returns raw BM25 scores, only those > 0 (a doc with no query term is
    left out); ``_normalize_bm25_scores`` puts them on (0, 1].

    Args:
        query: The user/recall query.
        docs: Mapping of doc key → doc text (the candidate set).

    Returns:
        Dict key → BM25 score (>= 0). Empty query or empty corpus → {}.
    """
    import math

    if not docs:
        return {}
    q_terms = _bm25_tokenize(query)
    if not q_terms:
        return {}

    # DOC tokenization is query-independent → cache it. The QUERY
    # tokenization above stays on the bare, UNcached `_bm25_tokenize`.
    doc_tokens = {k: _bm25_tokenize_doc(text) for k, text in docs.items()}
    doc_len = {k: len(toks) for k, toks in doc_tokens.items()}
    n_docs = len(docs)
    avgdl = (sum(doc_len.values()) / n_docs) if n_docs else 0.0

    # Document frequency per query term.
    doc_term_sets = {k: set(toks) for k, toks in doc_tokens.items()}
    q_unique = set(q_terms)
    df: dict[str, int] = {}
    for term in q_unique:
        df[term] = sum(1 for s in doc_term_sets.values() if term in s)

    # IDF: ln(1 + (N - df + 0.5)/(df + 0.5)) — the +1 form keeps IDF >= 0.
    idf: dict[str, float] = {}
    for term in q_unique:
        d = df[term]
        idf[term] = math.log(1.0 + (n_docs - d + 0.5) / (d + 0.5))

    scores: dict[str, float] = {}
    for key, toks in doc_tokens.items():
        if not toks:
            continue
        # term frequency in this doc
        tf: dict[str, int] = {}
        for t in toks:
            if t in q_unique:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        dl = doc_len[key]
        score = 0.0
        for term, f in tf.items():
            denom = f + BM25_K1 * (1.0 - BM25_B + BM25_B * (dl / avgdl if avgdl else 1.0))
            score += idf[term] * (f * (BM25_K1 + 1.0)) / denom
        if score > 0:
            scores[key] = score
    return scores


def _normalize_bm25_scores(raw: dict[str, float]) -> dict[str, float]:
    """Raw Okapi-BM25 scores of one candidate set on (0, 1], so legs and docs can be
    ranked together, without over-promoting a degenerate candidate set.

    - Spread set (hi > lo): scaled by the best, ``s/hi``, so the best is 1.0 and every
      candidate stays above 0. The raw set holds only real matches (``_bm25_scores``
      keeps ``score > 0``), and readers drop a score of 0 as "no match": min-max sent
      the weakest real match to 0, so it was dropped whenever there were two or more.
    - Degenerate set (single candidate or all-equal): saturation ``s/(s+K1)``
      of the RAW score. A lone WEAK match gets a small score; a lone STRONG
      match approaches 1. This fixes the prior ``→ 1.0`` blanket, which gave a
      weak single candidate full keyword weight (run_aba4f77a F1). K1 is the
      same TF-saturation constant BM25 already uses.
    """
    if not raw:
        return {}
    vals = list(raw.values())
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return {k: v / (v + BM25_K1) for k, v in raw.items()}
    return {k: v / hi for k, v in raw.items()}


# ── Section Selection ─────────────────────────────────────────────────






# ── SessionRecall singleton cache ────────────────────────────────────

_session_recall_cache: dict[str, object] = {}  # db_path_str → SessionRecall


def _get_session_recall(db_path: Path) -> object:
    """Return a cached SessionRecall instance for the given DB path.

    Avoids re-creating the object (and its sqlite connections) on every
    ``select_memory_sections()`` call.
    """
    key = str(db_path)
    if key not in _session_recall_cache:
        from core.session_recall import SessionRecall
        _session_recall_cache[key] = SessionRecall(db_path)
    return _session_recall_cache[key]


# ── Key→Section mapping ──────────────────────────────────────────────
#
# Derived from the SINGLE SOURCE OF TRUTH (_REF_PREFIX_TO_SECTION, which itself
# expands ddd_entry_lifecycle.MEMORY_PREFIX_TO_SECTION + the legacy KD/RC/LL
# aliases). The old hand-maintained _KEY_TO_SECTION literal listed only
# RC/KD/LL/COE and drifted: the live index uses GUI/PIT/DEC/OT/PRI/MOD/COR/SP,
# so the pure-keyword leg mapped 10/443 entries and returned {} for non-COE
# queries (run_c1624c89 G2). Deriving from the source-of-truth fixes the root
# cause once and cannot drift again.
_KEY_TO_SECTION = _REF_PREFIX_TO_SECTION


def _key_to_section(key: str) -> Optional[str]:
    """Map an index entry key like 'GUI01' / 'PIT12' / 'COE03' to a section name.

    Longest-prefix match: sort prefixes by length descending so a longer prefix
    (e.g. a hypothetical 'COE') is preferred over a shorter one that is its
    prefix. The current schema has no such collision, but this is cheap
    insurance against a future short prefix shadowing a longer one.
    """
    for prefix in sorted(_KEY_TO_SECTION, key=len, reverse=True):
        if key.startswith(prefix):
            return _KEY_TO_SECTION[prefix]
    return None


def _strip_superseded_entries(body: str, superseded_keys: set[str]) -> str:
    """Remove entry blocks whose key ∈ superseded_keys from a section body.

    Unified-retrieval STEP1: body-BM25 scores a whole ## section, so it has no
    per-entry key to down-weight (unlike the retired index scorer's per-entry
    SUPERSEDED_WEIGHT). Instead we strip a superseded entry's text from the body
    BEFORE scoring — a superseded entry then contributes 0 to its section's BM25
    score (section-body granularity of the same "superseded ≠ recall signal"
    intent). An entry block = its `- [KEY]` line through the line before the next
    top-level `- ` (or section end), incl. its metadata comment lines.
    """
    if not superseded_keys:
        return body
    lines = body.split("\n")
    out: list[str] = []
    skip = False
    for line in lines:
        m = re.match(r"\s*- \[([A-Z]{1,4}\d+)\]", line)
        if m:
            skip = m.group(1) in superseded_keys  # start/stop at each entry boundary
        if not skip:
            out.append(line)
    return "\n".join(out)


def _section_body_scores(
    user_message: str,
    sections: dict[str, str],
    superseded_keys: set[str] | None = None,
    *,
    include_evergreen: bool = False,
) -> dict[str, float]:
    """Score MEMORY ## sections against a query by body-BM25 (index-free).

    Unified-retrieval — the replacement for the old index-based section scorer
    (both it and the in-prompt index were deleted 2026-08-14). Mirrors DDD's
    ``recall_multi._ddd_section_scores`` (the correct, index-free pattern):
    ``_bm25_scores({section: body})`` → ``_normalize_bm25_scores``. Why body-BM25
    superseded the old index scorer (verified run_a2dffa0d): (1) the index's
    aliases were body-keyword-derived, so the body carries the same recall tokens
    + more; (2) ``_bm25_tokenize`` emits CJK bigrams → natively cross-language,
    where the old index leg's whole-token/prefix CJK match missed reordered CJK.

    ``include_evergreen`` selects the two callers' different section scopes — the
    ONE seam where injection and recall legitimately differ:
      • INJECTION (STEP1, default False): evergreen (principle/correction/COE/
        standing-preference/open-threads) is ALWAYS injected in full (CYCLE 2), so
        it is NOT keyword-selected — only OPERATIONAL sections are scored.
      • RECALL (STEP5a, True): recall returns SCOPED sections by query (not full
        injection), so evergreen MUST be reachable by a matching query too — score
        ALL sections.

    superseded entries are stripped from each body before scoring (see
    ``_strip_superseded_entries``).

    Returns ``{section_name: normalized_score in [0,1]}``; empty if none match.
    """
    corpus_sections = {
        name: body for name, body in sections.items()
        if body.strip() and (include_evergreen or name not in MEMORY_EVERGREEN_SECTIONS)
    }
    if not corpus_sections:
        return {}
    _sup = superseded_keys or set()
    corpus = {name: _strip_superseded_entries(body, _sup) for name, body in corpus_sections.items()}
    raw = _bm25_scores(user_message, corpus)
    if not raw:
        return {}
    return _normalize_bm25_scores(raw)


# Back-compat alias: the injection path (STEP1) calls the operational-only scorer.
def _operational_section_scores(
    user_message: str,
    sections: dict[str, str],
    superseded_keys: set[str] | None = None,
) -> dict[str, float]:
    """Injection-path scorer: operational sections only (evergreen always-injected).

    Thin wrapper over ``_section_body_scores(include_evergreen=False)`` — kept as a
    named entry point so the injection call site + its tests read intently."""
    return _section_body_scores(user_message, sections, superseded_keys, include_evergreen=False)


def _extract_superseded_keys(memory_content: str) -> set[str]:
    """Extract set of entry keys that are marked superseded in MEMORY.md.

    Scans for temporal metadata HTML comments with non-null superseded_by.
    Returns keys like {'KD03', 'RC14'} for entries that should be down-weighted.
    """
    superseded: set[str] = set()
    # Match entry key followed (within 2 lines) by superseded metadata
    for m in re.finditer(
        r"- \[([A-Z]{1,4}\d+)\].*?\n(?:.*?\n)?.*?"
        r"<!--.*?superseded_by:\s*(?!null)(\S+).*?-->",
        memory_content,
    ):
        superseded.add(m.group(1))
    return superseded




# ── Memory Injection ──────────────────────────────────────────────────


def select_memory_sections(
    memory_content: str,
    user_message: str = "",
    session_signals: Optional[dict] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context_percent_used: float = 0.0,
    memory_embeddings: bool = False,
) -> str:
    """Assemble live MEMORY.md for system-prompt injection = the WHOLE body.

    NEW ARCHITECTURE (2026-08-14, XG): live MEMORY.md is ALWAYS full-injected
    regardless of size — there is NO selective mode, NO keyword/section scoring,
    NO in-prompt index, NO channel-minimal, NO adaptive-budget truncation. Size is
    bounded UPSTREAM by the size-valve (distillation_hook._enforce_size_valve:
    body >30K → archive lowest-value operational to .context until ≤25K), and
    archived content stays reachable via recall by SEVERAL routes — see this module's
    header for the enumeration and the code as the authority; do not restate a closed
    list here. So the injector's ONLY job is: return the body.

    This deliberately RETIRED (old selective machinery, all removed): body-BM25
    section selection, _channel_minimal, _adaptive_max_tokens, EntryRefs 1-hop,
    SessionRecall injection, the in-prompt index block, and the "[Not loaded …]"
    manifest. Their value is now carried by full-injection (nothing is excluded to
    announce) + recall (past sessions / archived sections retrieved on demand).

    Channel privacy is enforced UPSTREAM, not here: group/non-owner sessions drop
    MEMORY.md wholesale via context_directory_loader.WHOLE_FILE_PRIVATE, so this
    function is never reached for them (the old is_channel branch was redundant).

    The extra params (user_message / session_signals / max_tokens /
    context_percent_used / memory_embeddings) are retained ONLY for call-site +
    test signature compatibility and are now INERT.

    Returns the full MEMORY body (index block stripped if a legacy file still
    carries one), or "" if empty.
    """
    body = extract_body_without_index(memory_content)
    return body if body.strip() else ""


# ── Index Injection / Extraction ──────────────────────────────────────






def extract_body_without_index(content: str) -> str:
    """Get MEMORY.md content with the index block removed.

    Strips both:
    1. Marker-delimited index blocks (<!-- MEMORY_INDEX_START/END -->)
    2. Bare '## Memory Index' sections written by agent Edit tool (no markers)

    The bare section is the root cause of duplicate indexes: the agent
    writes a '## Memory Index' via Edit, then inject_index_into_memory
    adds a marker-wrapped copy at the top — two copies coexist.

    Returns the original content if no index block is present.
    """
    start = content.find(MEMORY_INDEX_START)
    end = content.find(MEMORY_INDEX_END)

    if start == -1 or end == -1 or end <= start:
        result = content
    else:
        # Remove from start marker to end marker (inclusive) + trailing whitespace
        after_index = content[end + len(MEMORY_INDEX_END):]
        before_index = content[:start]
        result = before_index + after_index

    # Also strip a bare '## Memory Index' section (not marker-wrapped) — an
    # agent-written duplicate that persists across sessions. Pattern: the heading
    # followed by lines until the next ## header or EOF (.* not .+ consumes blank
    # lines within the section).
    # run_3cb6b9ae hardening (Gate-2 LOW): anchor to the TOP OF FILE only — the orphan
    # index block is always at the top. A bare `## Memory Index` heading appearing INSIDE
    # an entry body lower in the file must NOT be stripped: the old `^…$/MULTILINE` form
    # matched any line, so persisting the strip to disk (locked_write #6) could silently
    # delete a real entry whose body happened to contain that heading line.
    #
    # run_0f009a75 (Gate-0 fix): the anchor now ALSO allows the index one line below a
    # DOCUMENT TITLE — `# MEMORY\n\n## Memory Index` — the real shape the pure-`\A\s*`
    # form missed. The leading alt is deliberately `#[^#\n]` (a SINGLE-`#` document
    # title), NOT `[^\n]*` (any first line): a `[^\n]*` first-line would also swallow a
    # real `## Some Real Entry` header sitting above a bare index → catastrophic entry
    # deletion (the Gate-0 WRONG-FRAME catch). `#[^#\n]` matches `# MEMORY` but never
    # `## Section`, so a real section header above the index is untouchable. The
    # `(?!^## )` lookahead (needs re.MULTILINE) still stops the strip at the next
    # section, so content below the orphan index is never consumed.
    # CRLF-tolerant (`(?:\r\n|\n)` at every line break): MEMORY.md is written by the
    # agent via locked_write (always \n) on a macOS daemon, so CRLF is not expected —
    # but the pre-existing `\n`-only form would silently no-op on a CRLF file, so this
    # closes that latent gap at zero cost (Gate-2 run_0f009a75).
    result = re.sub(
        r"\A(?:\s*|#[^#\n][^\n]*(?:\r\n|\n)\s*)## Memory Index(?:\r\n|\n)(?:(?!^## ).*(?:\r\n|\n)?)*",
        "",
        result,
        flags=re.MULTILINE,
    )

    # Clean up extra blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    # If stripping the index left nothing meaningful, return as-is
    stripped = result.strip()
    return stripped + "\n" if stripped else ""


# ── Temporal Validity (P2) ───────────────────────────────────────────
# HTML comment metadata on MEMORY.md entries:
#   <!-- valid_from: YYYY-MM-DD | superseded_by: KEY | confidence: high -->
# Superseded entries score 0.1x weight in select_memory_sections().

_TEMPORAL_RE = re.compile(
    r"<!--\s*valid_from:\s*(\S+)\s*\|\s*superseded_by:\s*(\S+)\s*\|\s*confidence:\s*(\S+)\s*-->"
)

SUPERSEDED_WEIGHT = 0.1  # Score weight for superseded entries


def parse_temporal_metadata(text: str) -> Optional[dict]:
    """Extract temporal metadata from an HTML comment string.

    Args:
        text: String that may contain a temporal metadata HTML comment.

    Returns:
        Dict with keys valid_from, superseded_by (str or None), confidence.
        Returns None if no temporal metadata found.
    """
    if not text:
        return None

    m = _TEMPORAL_RE.search(text)
    if not m:
        return None

    superseded = m.group(2)
    if superseded.lower() == "null":
        superseded = None

    return {
        "valid_from": m.group(1),
        "superseded_by": superseded,
        "confidence": m.group(3),
    }


def _entry_temporal_weight(entry_text: str) -> float:
    """Return temporal score weight for an entry.

    Superseded entries → 0.1, active/no-metadata → 1.0.

    Args:
        entry_text: The full entry text including any HTML comment.

    Returns:
        Weight multiplier (0.1 or 1.0).
    """
    meta = parse_temporal_metadata(entry_text)
    if meta and meta.get("superseded_by"):
        return SUPERSEDED_WEIGHT
    return 1.0


def format_temporal_metadata(
    valid_from: str,
    superseded_by: Optional[str] = None,
    confidence: str = "high",
) -> str:
    """Generate an HTML comment string with temporal metadata.

    Args:
        valid_from: Date string (YYYY-MM-DD).
        superseded_by: Key of the superseding entry, or None.
        confidence: Confidence level (high/medium/low).

    Returns:
        HTML comment string like
        ``<!-- valid_from: 2026-04-11 | superseded_by: null | confidence: high -->``
    """
    sup = superseded_by if superseded_by else "null"
    return f"<!-- valid_from: {valid_from} | superseded_by: {sup} | confidence: {confidence} -->"


def add_temporal_metadata_to_entry(
    entry: str,
    valid_from: str,
    confidence: str = "high",
) -> str:
    """Add temporal metadata to a MEMORY.md entry if not already present.

    Appends the HTML comment on the line after the entry text.
    Idempotent — won't add if already present.

    Args:
        entry: The entry text (e.g. "- [KD28] 2026-04-11 New decision").
        valid_from: Date string for valid_from field.
        confidence: Confidence level.

    Returns:
        Entry with temporal metadata appended (or unchanged if already present).
    """
    if _TEMPORAL_RE.search(entry):
        return entry  # Already has temporal metadata

    meta = format_temporal_metadata(valid_from=valid_from, confidence=confidence)
    # Append on the next line with indentation
    return f"{entry.rstrip()}\n  {meta}"


def mark_entry_superseded(
    content: str,
    old_key: str,
    new_key: str,
) -> str:
    """Mark a MEMORY.md entry as superseded by another entry.

    If the entry already has temporal metadata, updates the superseded_by field.
    If not, adds temporal metadata with superseded_by set.

    Args:
        content: Full MEMORY.md content.
        old_key: Key of the entry to mark (e.g. "KD02").
        new_key: Key of the superseding entry (e.g. "KD99").

    Returns:
        Updated content string, or unchanged if old_key not found.
    """
    # Find the entry line by key
    entry_pattern = re.compile(
        rf"^(- \[{re.escape(old_key)}\].+?)$",
        re.MULTILINE,
    )
    match = entry_pattern.search(content)
    if not match:
        return content  # Key not found

    entry_start = match.start()
    entry_line = match.group(1)

    # Check if there's existing temporal metadata on the next line(s)
    # Look at the text after this entry line until the next entry or section
    after_entry = content[match.end():]

    # Check for existing temporal metadata within the next 2 lines
    next_lines = after_entry.split("\n", 3)
    temporal_line_idx = None
    for i, line in enumerate(next_lines[:3]):
        if _TEMPORAL_RE.search(line):
            temporal_line_idx = i
            break

    if temporal_line_idx is not None:
        # Update existing metadata
        old_meta_match = _TEMPORAL_RE.search(next_lines[temporal_line_idx])
        if old_meta_match:
            old_meta = old_meta_match.group(0)
            new_meta = format_temporal_metadata(
                valid_from=old_meta_match.group(1),
                superseded_by=new_key,
                confidence=old_meta_match.group(3),
            )
            return content.replace(old_meta, new_meta, 1)
    else:
        # Add new metadata after the entry line
        # Extract date from the entry for valid_from
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", entry_line)
        valid_from = date_match.group(0) if date_match else "unknown"
        meta = format_temporal_metadata(
            valid_from=valid_from, superseded_by=new_key
        )
        return content.replace(
            entry_line,
            f"{entry_line}\n  {meta}",
            1,
        )

    return content
