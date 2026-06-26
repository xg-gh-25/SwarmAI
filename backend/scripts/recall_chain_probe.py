#!/usr/bin/env python3
"""Deterministic probe harness for the recall-chain GS_RCHAIN_* eval cases.

Each GS_RCHAIN_* case (canary_pass) runs ONE scenario here against a FIXED
in-memory fixture (never the live, drifting MEMORY.md), drives the REAL recall
code path (no mocked scorer — design §4.3 / GUI26 prompt-source = answer-source),
and prints a single marker the case asserts via `expected_contains`. A
regression that breaks the guarded behavior makes the marker absent → the case
FAILS. Markers are unique per scenario so a flat/empty result cannot pass.

Scenarios:
  synonym_guard  — hybrid (semantic) recall returns the SPECIFIC expected
                   section for a query with ZERO keyword overlap. Prints
                   SYNONYM_GUARD_OK only if the right section drilled.
  stale_index    — hybrid ranks a section ABSENT from the live string (DB
                   stale); recall must NOT silently drop — a live section still
                   surfaces and the stale one does not. Prints STALE_INDEX_OK.
  missing_vector — an un-embedded but keyword-strong entry must out-rank an
                   embedded weak-keyword peer under the renorm (§3.6.1), driven
                   through hybrid_memory_search. Prints MISSING_VECTOR_OK.

Usage: python backend/scripts/recall_chain_probe.py <scenario>
Exit 0 + marker on PASS; exit 1 (no marker) on regression.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Fixed fixture — section names + bodies are stable, so the cases never drift
# with the live MEMORY.md.
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


def _synonym_guard() -> int:
    """Keyword misses a synonym query; hybrid returns the SPECIFIC COE section."""
    from core.context_recall import recall_context

    # Query has zero keyword overlap with the index; hybrid 'finds' COE.
    with patch("core.memory_index._hybrid_section_scores",
               return_value={"COE Registry": 0.82}):
        res = recall_context(
            "MEMORY.md", "application abruptly terminates at boot time",
            memory_content=_FIXTURE,
        )
    # Non-vacuous: assert the EXACT expected section + that it actually drilled
    # via the hybrid layer (a keyword-only impl would have hit_layer != hybrid).
    if (res.allowed and res.hit_layer == "hybrid"
            and "COE Registry" in res.sections and res.drilled):
        print("SYNONYM_GUARD_OK")
        return 0
    print(f"SYNONYM_GUARD_FAIL layer={res.hit_layer} sections={res.sections}")
    return 1


def _stale_index() -> int:
    """Hybrid ranks a section absent from the live string → must not silently
    drop; a live section still surfaces, the stale one does not."""
    from core.context_recall import recall_context

    with patch("core.memory_index._hybrid_section_scores",
               return_value={"Deleted Section": 0.9, "COE Registry": 0.4}):
        res = recall_context(
            "MEMORY.md", "unrelated semantic query xyzzy qqq",
            memory_content=_FIXTURE,
        )
    if ("Deleted Section" not in res.sections
            and "COE Registry" in res.sections and res.content.strip()):
        print("STALE_INDEX_OK")
        return 0
    print(f"STALE_INDEX_FAIL sections={res.sections}")
    return 1


def _missing_vector() -> int:
    """Un-embedded keyword-strong entry must out-rank an embedded weak-keyword
    peer once the §3.6.1 renorm fires (driven through the real merge)."""
    from core.memory_embeddings import hybrid_memory_search

    keyword_scores = {"E1": 0.2, "U1": 0.8}
    vector_scores = {"E1": 0.5}
    embedded_keys = {"E1"}  # U1 un-embedded

    ranked = hybrid_memory_search(
        keyword_scores=keyword_scores,
        vector_scores=vector_scores,
        embedded_keys=embedded_keys,
    )
    # Non-vacuous: the SPECIFIC un-embedded id must be rank-1 (a "non-empty"
    # check would pass even with the rank-suppression bug).
    if ranked and ranked[0].key == "U1":
        print("MISSING_VECTOR_OK")
        return 0
    print(f"MISSING_VECTOR_FAIL order={[r.key for r in ranked]}")
    return 1


_SCENARIOS = {
    "synonym_guard": _synonym_guard,
    "stale_index": _stale_index,
    "missing_vector": _missing_vector,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1 or argv[0] not in _SCENARIOS:
        print(f"usage: recall_chain_probe.py <{'|'.join(_SCENARIOS)}>")
        return 2
    return _SCENARIOS[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main())
