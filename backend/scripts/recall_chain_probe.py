#!/usr/bin/env python3
"""Deterministic probe harness for the recall-chain GS_RCHAIN_* eval cases.

Each GS_RCHAIN_* case (canary_pass) runs ONE scenario here. Every scenario
drives the REAL hybrid recall code path — real sqlite-vec DB assembly, real
Okapi-BM25 + min-max keyword leg, real ``embedded_keys`` merge, real section
aggregation (design §4.3 / GUI26 prompt-source = answer-source, GUI32 render-
fidelity must exercise the real assembly path, NOT a mocked scorer).

The ONLY thing mocked is the network boundary: the Bedrock query embedding
(``_embedding_client_cache.embed_text``) returns a controlled vector, and the
on-disk DB path is redirected to a per-scenario temp DB. Entry vectors are
inserted into a real ``memory_vec`` table; the ranking is produced by the real
``_hybrid_section_scores`` / ``recall_context`` code, not by a stub.

Determinism: vectors are one-hot 1024-d, so nearest-neighbour is exact and
metric-agnostic (identical vector → distance 0; orthogonal → larger). The fixed
in-memory MEMORY string never drifts with the live MEMORY.md.

Scenarios (each is non-vacuous — a mutation that disables the guarded behavior
makes the marker absent → the eval case FAILS):
  synonym_guard  — keyword-first MISSES a zero-overlap synonym query; the real
                   hybrid VECTOR leg finds the SPECIFIC COE section. Disabling
                   the vector leg / mis-renorming the embedded entry drops it
                   below threshold. Prints SYNONYM_GUARD_OK.
  stale_index    — real hybrid ranks a section ABSENT from the live string #1
                   (DB stale); the stale-index guard must skip it and still
                   surface the live section. Removing the guard surfaces an
                   empty "Deleted Section". Prints STALE_INDEX_OK.
  missing_vector — an un-embedded keyword-strong entry must out-rank an embedded
                   peer that has BOTH a strong vector AND wins the no-renorm
                   merge. Only the §3.6.1 renorm flips the ranking; disabling it
                   makes the embedded entry win. Driven through the real
                   _hybrid_section_scores assembly. Prints MISSING_VECTOR_OK.

Usage: python backend/scripts/recall_chain_probe.py <scenario>
Exit 0 + marker on PASS; exit 1 (no marker) on regression.
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Fixed live-MEMORY string for the recall_context scenarios. Section names +
# bodies are stable, so the cases never drift with the real MEMORY.md.
_FIXTURE = """\
<!-- MEMORY_INDEX_START -->
## Memory Index
- [COE01] exit code -9 cascading SIGKILL failure | sigkill, oom, crash
- [LL01] Sync wrappers around async cleanup leak | async, cleanup, leak
<!-- MEMORY_INDEX_END -->

## COE Registry
- 2026-03-17: **Sev-1: exit code -9 cascading SIGKILL** — OOM kills, retry worse.

## Lessons Learned
- 2026-03-22: **Sync wrappers around async cleanup = resource leaks** — async callers.
"""

_DIM = 1024


def _onehot(idx: int) -> list[float]:
    """Deterministic 1024-d one-hot unit vector (metric-agnostic nearest-NN)."""
    v = [0.0] * _DIM
    v[idx % _DIM] = 1.0
    return v


class _FakeEmbedClient:
    """Stands in for the Bedrock EmbeddingClient — the network boundary. Returns
    a fixed query vector regardless of text (only the query is embedded; entry
    vectors come from the real DB)."""

    def __init__(self, query_vec: list[float]) -> None:
        self._q = query_vec

    def embed_text(self, text: str) -> list[float]:  # noqa: ARG002 — fixed by design
        return self._q


def _build_db(db_path: Path, entries: list[tuple]) -> None:
    """Create a real memory_entries + memory_vec DB at db_path.

    entries: list of (key, section, title, keywords, vector_or_None). A None
    vector means the entry is un-embedded (present in memory_entries only).
    """
    import sqlite3

    import sqlite_vec

    from core.memory_embeddings import MemoryEmbeddingStore

    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        store = MemoryEmbeddingStore(conn)
        store.ensure_tables()
        for key, section, title, keywords, vec in entries:
            store.upsert_entry(
                key=key, section=section, title=title,
                full_text=f"{title} body", keywords=keywords, embedding=vec,
            )
    finally:
        conn.close()


@contextmanager
def _real_hybrid(db_path: Path, query_vec: list[float]):
    """Redirect the DB path + Bedrock embedder so the REAL _hybrid_section_scores
    runs against our fixture. Nothing in the scoring path is mocked."""
    from core import memory_index

    with patch("jobs.paths.DB_PATH", db_path), \
            patch.object(memory_index, "_embedding_client_cache",
                         _FakeEmbedClient(query_vec)):
        yield


def _synonym_guard() -> int:
    """Keyword-first misses a zero-overlap synonym query; the real hybrid VECTOR
    leg returns the SPECIFIC COE section through the full recall_context chain."""
    from core.context_recall import recall_context

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "mem.db"
        # COE embedded at e_5; LL embedded at e_10. Query embeds to e_5 → COE
        # is the exact vector neighbour. BM25 is ~0 (zero lexical overlap), so
        # the match is genuinely carried by the vector leg.
        _build_db(db, [
            ("COE01", "COE Registry", "exit code -9 cascading SIGKILL failure",
             ["sigkill", "oom", "crash"], _onehot(5)),
            ("LL01", "Lessons Learned", "Sync wrappers around async cleanup leak",
             ["async", "cleanup", "leak"], _onehot(10)),
        ])
        with _real_hybrid(db, _onehot(5)):
            res = recall_context(
                "MEMORY.md", "application abruptly terminates at boot time",
                memory_content=_FIXTURE,
            )
    # Non-vacuous: the EXACT section must surface AND via the hybrid layer (a
    # broken vector leg / wrong renorm drops COE below threshold → not hybrid).
    if (res.allowed and res.hit_layer == "hybrid"
            and "COE Registry" in res.sections and res.drilled):
        print("SYNONYM_GUARD_OK")
        return 0
    print(f"SYNONYM_GUARD_FAIL layer={res.hit_layer} sections={res.sections}")
    return 1


def _stale_index() -> int:
    """Real hybrid ranks a section absent from the live string #1 (DB stale).
    The stale-index guard must skip it and still surface the live section."""
    from core.context_recall import recall_context

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "mem.db"
        # "Deleted Section" embedded at e_5 (matches the query vector, ranks
        # #1) but does NOT exist in the live _FIXTURE → must be guarded out.
        # "COE Registry" embedded at e_10 (ranks lower) and IS in the fixture.
        _build_db(db, [
            ("DEL01", "Deleted Section", "ghost entry removed from live memory",
             ["ghost", "removed"], _onehot(5)),
            ("COE01", "COE Registry", "exit code -9 cascading SIGKILL failure",
             ["sigkill", "oom", "crash"], _onehot(10)),
        ])
        with _real_hybrid(db, _onehot(5)):
            res = recall_context(
                "MEMORY.md", "unrelated semantic query xyzzy qqq",
                memory_content=_FIXTURE,
            )
    if ("Deleted Section" not in res.sections
            and "COE Registry" in res.sections and res.content.strip()):
        print("STALE_INDEX_OK")
        return 0
    print(f"STALE_INDEX_FAIL sections={res.sections} layer={res.hit_layer}")
    return 1


def _missing_vector() -> int:
    """Un-embedded keyword-strong entry out-ranks an embedded peer through the
    REAL _hybrid_section_scores assembly. Tuned so ONLY the §3.6.1 renorm flips
    the ranking: with renorm U1=1.0 > E1=0.6; without it U1=0.4 < E1=0.6."""
    from core.memory_index import _hybrid_section_scores

    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "mem.db"
        # E1 embedded at e_5 with a weak-keyword title; U1 un-embedded with a
        # strong unique-term title matching the query. Query embeds to e_5 so
        # E1 gets a STRONG vector (vs≈1.0) — without renorm E1 wins (0.6 > 0.4);
        # with renorm U1 competes on keyword alone (1.0) and wins.
        _build_db(db, [
            ("E1", "Embedded Sec", "alpha", ["alpha"], _onehot(5)),
            ("U1", "Unembedded Sec", "zephyr quartz nimbus",
             ["zephyr", "quartz", "nimbus"], None),
        ])
        with _real_hybrid(db, _onehot(5)):
            section_scores = _hybrid_section_scores("zephyr quartz nimbus")

    if not section_scores:
        print("MISSING_VECTOR_FAIL empty")
        return 1
    top = max(section_scores.items(), key=lambda kv: kv[1])[0]
    # Non-vacuous: the un-embedded entry's section must be rank-1. Disabling the
    # renorm makes "Embedded Sec" win (strong vector) → FAIL.
    if top == "Unembedded Sec":
        print("MISSING_VECTOR_OK")
        return 0
    print(f"MISSING_VECTOR_FAIL top={top} scores={section_scores}")
    return 1


def _knowledge_live(negative: bool = False) -> int:
    """Knowledge/Library WIRE: the REAL RecallEngine hybrid (FTS5 + sqlite-vec
    over a real KnowledgeStore) must surface the SPECIFIC chunk a zero-lexical-
    overlap query can only reach via the VECTOR leg. The only mocked thing is the
    Bedrock query embedding (the network boundary); entry vectors live in a real
    knowledge_vec table and the merge is the real 0.6·vec+0.4·bm25 in
    recall_engine.RecallEngine.search. A broken vector leg (or embed_fn not
    threaded through) drops the chunk → marker absent. Prints KNOWLEDGE_LIVE_OK.

    negative=True (teeth): embed_fn returns None → vector leg dead (FTS5-only).
    The zero-lexical-overlap query then cannot reach the chunk → OK condition
    cannot hold → exits 1 with the FAIL marker, proving the case has teeth."""
    import sqlite3

    import sqlite_vec

    from core.knowledge_store import KnowledgeStore
    from core.recall_engine import RecallEngine

    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        store = KnowledgeStore(conn)
        store.ensure_tables()
        # Target chunk embedded at one-hot e_5; query embeds to e_5 → exact
        # vector neighbour. Its TEXT shares zero lexical overlap with the query,
        # so FTS5/BM25 ≈ 0 and the hit is genuinely vector-carried.
        store.upsert_chunk(
            "Designs/quokka.md", 0, "## Quokka Design",
            "marsupial habitat rottnest island foliage", "kh1",
            embedding=_onehot(5),
        )
        store.upsert_chunk(
            "Notes/unrelated.md", 0, "## Unrelated",
            "kubernetes ingress controller tls termination", "kh2",
            embedding=_onehot(700),
        )
        engine = RecallEngine(store)
        # embed_fn IS the Bedrock boundary — fixed query vector, real merge.
        # negative: embed_fn=None kills the vector leg (FTS5-only fallback).
        results = engine.search(
            "application crash exit code sigkill",
            embed_fn=(lambda _t: None) if negative else (lambda _t: _onehot(5)),
            top_k=5,
        )
    finally:
        conn.close()

    # Non-vacuous: the exact vector-neighbour chunk must win, and its vector_score
    # must dominate its fts_score (proves the VECTOR leg carried it, not keyword).
    if results:
        top = results[0]
        if (top.get("source_file") == "Designs/quokka.md"
                and top.get("vector_score", 0) > top.get("fts_score", 0)):
            print("KNOWLEDGE_LIVE_OK")
            return 0
    print(f"KNOWLEDGE_LIVE_FAIL results={[(r.get('source_file'), r.get('vector_score'), r.get('fts_score')) for r in results[:3]]}")
    return 1


def _codeintel_live(negative: bool = False) -> int:
    """CodeIntel WIRE: the REAL recall_multi._codeintel_recall must drive the
    REAL load_project_graph().search_symbols over the LIVE SwarmAI code graph
    for a known symbol and return a hit (with 1-hop caller enrichment). Nothing
    is mocked — if the graph is absent or search_symbols is broken, the bucket is
    empty → marker absent. Prints CODEINTEL_LIVE_OK.

    negative=True (teeth): query a symbol that does NOT exist in the graph → the
    bucket must be empty → OK condition cannot hold → exits 1, proving teeth."""
    from core import recall_multi

    # "recall_all" is a real public symbol in core/recall_multi.py — it MUST be
    # in the live SwarmAI code graph. A real graph search returns it; a broken
    # wire (no graph / search_symbols error) returns []. negative: a nonexistent
    # symbol must yield an empty bucket.
    query = "zzz_nonexistent_symbol_qqq_xyzzy" if negative else "recall_all"
    bucket = recall_multi._codeintel_recall(query, project="SwarmAI")

    if bucket and any("recall" in str(h.get("name", "")).lower() for h in bucket):
        print("CODEINTEL_LIVE_OK")
        return 0
    print(f"CODEINTEL_LIVE_FAIL bucket={bucket[:3] if bucket else bucket}")
    return 1


_SCENARIOS = {
    "synonym_guard": _synonym_guard,
    "stale_index": _stale_index,
    "missing_vector": _missing_vector,
    "knowledge_live": _knowledge_live,
    "codeintel_live": _codeintel_live,
}

# Scenarios that accept a 2nd arg "negative" (teeth mode): break the wire →
# the OK marker must NOT appear → exit 1. Used as each case's negative_command.
_NEGATIVE_CAPABLE = {"knowledge_live", "codeintel_live"}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not (1 <= len(argv) <= 2) or argv[0] not in _SCENARIOS:
        print(f"usage: recall_chain_probe.py <{'|'.join(_SCENARIOS)}> [negative]")
        return 2
    scenario, negative = argv[0], (len(argv) == 2 and argv[1] == "negative")
    if negative and scenario not in _NEGATIVE_CAPABLE:
        print(f"scenario '{scenario}' has no negative (teeth) mode")
        return 2
    fn = _SCENARIOS[scenario]
    return fn(negative=negative) if scenario in _NEGATIVE_CAPABLE else fn()


if __name__ == "__main__":
    raise SystemExit(main())
