#!/usr/bin/env python3
"""Deterministic probe harness for the recall-chain GS_RCHAIN_* eval cases.

Each GS_RCHAIN_* case (canary_pass) runs ONE scenario here. Every scenario
drives the REAL recall code path — pure FTS5+BM25 (the sqlite-vec vector leg was
removed 2026-08-14, PRI11), real section aggregation (design §4.3 / GUI26
prompt-source = answer-source, GUI32 render-fidelity must exercise the real
assembly path, NOT a mocked scorer).

Determinism: the fixed in-memory MEMORY string never drifts with the live
MEMORY.md; the on-disk DB path is redirected to a per-scenario temp DB.

Scenarios (each is non-vacuous — a mutation that disables the guarded behavior
makes the marker absent → the eval case FAILS). See ``_SCENARIOS`` below for the
live set (codeintel_live / resume_fill / entry_recall).

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


# A distinctive marker placed at the BOTTOM of a long fixture section, past 120
# filler entries — so its presence in recall output is an unambiguous signal that
# the entry-level slicer surfaced the matching entry past the truncation cliff.
_ENTRY_MARKER = "task_budget_zephyr_marker_800K"


def _entry_recall_fixture() -> str:
    """A MEMORY.md whose Decisions section has 120 filler entries followed by ONE
    target entry carrying _ENTRY_MARKER at the very BOTTOM — well past the
    RECALL_MAX_TOKENS=2000 front-truncation cliff. The old whole-section-then-
    front-truncate path returns only the filler head (marker absent); the
    entry-level slicer ranks the marker entry #1 and surfaces it."""
    pad = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod"
    filler = [
        f"- [decision] **Filler decision {i} about unrelated plumbing** — {pad} {pad} (2026-01-{(i % 28) + 1:02d})"
        for i in range(120)
    ]
    target = (
        f"- [decision] **{_ENTRY_MARKER}: desktop 800K, channels 400K** — the CLI "
        f"default is 128K; we override to avoid premature PreCompact (2026-06-17)"
    )
    body = "\n".join(filler + [target])
    index = (
        "## Memory Index\n"
        "<!-- MEMORY_INDEX_START -->\n"
        f"- [DEC32] {_ENTRY_MARKER} desktop channel token limit | 2026-06-17, "
        f"{_ENTRY_MARKER}, precompact\n"
        "<!-- MEMORY_INDEX_END -->\n"
    )
    return f"{index}\n## Decisions\n{body}\n\n## Open Threads\n- none\n"


def _entry_recall(negative: bool = False) -> int:
    """ENTRY-LEVEL RECALL WIRE (run_c1624c89 G1): the REAL recall_context must
    surface a query-matching entry sitting at the BOTTOM of a large section,
    instead of returning the section body front-truncated at RECALL_MAX_TOKENS
    (which drops the matching entry). Drives the REAL recall_context +
    _slice_section_entries on a live string with allow_embed=False — so it also
    proves the no-embed / keyword-only path is no longer dead. Nothing under test
    is mocked (GUI26 prompt-source = answer-source).

    POSITIVE: the bottom entry's unique marker MUST appear in content, the result
    stays within RECALL_MAX_TOKENS, sections still carries the SECTION NAME
    ("Decisions") — not an entry id (Gate-1 condition C). Prints ENTRY_RECALL_OK.

    negative=True (teeth): demand the OLD whole-section behavior — assert the
    marker is ABSENT (i.e. front-truncation dropped it). That is FALSE on the
    entry-level code (marker IS present), so the OK marker is withheld → exit 1,
    proving the assertion bites. A no-op / reverted build makes the marker absent
    again → teeth fire."""
    from core.context_recall import recall_context, RECALL_MAX_TOKENS
    from core.context_directory_loader import ContextDirectoryLoader

    mem = _entry_recall_fixture()
    res = recall_context(
        "MEMORY.md", f"{_ENTRY_MARKER} desktop channel 800K token limit",
        memory_content=mem, allow_embed=False,
    )
    marker_present = _ENTRY_MARKER in res.content
    within_budget = (
        ContextDirectoryLoader.estimate_tokens(res.content) <= RECALL_MAX_TOKENS
    )
    section_name_kept = "Decisions" in res.sections

    if negative:
        # Teeth: demand the OLD invariant — "the bottom entry is DROPPED, marker
        # ABSENT". On the fixed code the marker IS present, so the old invariant
        # is false → withhold OK → exit 1 (proves the assertion discriminates).
        # A reverted/no-op build drops the marker → old invariant holds → the
        # teeth would wrongly pass, which is exactly the regression we want a
        # mutation test to catch when run against HEAD.
        if marker_present:
            print("ENTRY_RECALL_TEETH (marker present = entry-level fix is live; old 'dropped' invariant false)")
            return 1
        print("ENTRY_RECALL_OK")  # only reached on a reverted build (marker dropped)
        return 0

    if marker_present and within_budget and section_name_kept and res.drilled:
        print("ENTRY_RECALL_OK")
        return 0
    print(
        f"ENTRY_RECALL_FAIL marker={marker_present} budget_ok={within_budget} "
        f"section_kept={section_name_kept} layer={res.hit_layer} sections={res.sections}"
    )
    return 1


def _resume_fill(negative: bool = False) -> int:
    """RESUME-EXTRACTION WIRE (READ-path "alive != correct", run_674f32ef):
    the REAL build_resume_context must fill ELASTICALLY — a generous token
    budget yields MORE enriched context than a small one for the SAME complex
    session — instead of the months-long ~4% under-fill where fixed item-caps
    (conclusions 5, directives 10, tool_results 15, turns 30) made budget
    irrelevant. This is the behavior contract that the old GS_LOP005 import-OK
    canary was structurally blind to.

    Drives the REAL build_resume_context against synthetic message fixtures,
    mocking ONLY the database boundary (count/list) + a non-git working_directory
    (so _extract_uncommitted_state returns "" deterministically). NO mock of the
    extraction/cap logic under test — GUI26 prompt-source = answer-source.

    POSITIVE asserts three things:
      1. ELASTIC: same 40-msg complex session fills MORE at 1M budget than at
         128K budget (the delta is the enriched layers; the untrimmable
         checkpoint is constant in both, so a real difference proves the caps
         scale with budget).
      2. LEAN: a trivial 2-msg session stays small at 1M budget (does not
         balloon to the generous cap).
      3. CLAMP: output never exceeds token_budget*4 chars (the hard backstop).

    negative=True (teeth, mirrors _recall_budget): demand the OLD hard-coded-caps
    invariant — "a 10x-larger budget does NOT grow the fill." That is TRUE on a
    reverted fixed-caps build and FALSE on the elastic code (fill DOES grow), so
    the OK marker is withheld -> exit 1, proving the assertion bites. (Pinning
    caps to a fixed value was rejected at Gate-0: it would test the mock and pass
    a reverted build.)"""
    import asyncio
    import tempfile as _tf
    from unittest.mock import AsyncMock, MagicMock

    from core import context_injector as ci

    def _session(n_turns: int, answer_chars: int) -> list[dict]:
        msgs: list[dict] = []
        for i in range(n_turns):
            msgs.append({"role": "user",
                         "content": [{"type": "text",
                                      "text": f"please do task {i} now"}]})
            msgs.append({"role": "assistant",
                         "content": [{"type": "text",
                                      "text": f"turn{i} " + ("z" * answer_chars)}]})
        return msgs

    def _fill(msgs: list[dict], model_window: int, workdir: str) -> int:
        ci._resume_cache.clear()
        fake_db = MagicMock()
        fake_db.messages.count_by_session = AsyncMock(return_value=len(msgs))
        fake_db.messages.list_by_session_paginated = AsyncMock(return_value=msgs)
        fake_database = MagicMock()
        fake_database.db = fake_db
        # Hermeticity: _merge_crash_checkpoint reads jobs.paths.STATE_DIR/
        # session_checkpoint.json — real machine state. Point STATE_DIR at the
        # empty tempdir so the checkpoint read is deterministic (returns None),
        # not bleeding the live daemon's crash checkpoint into the fill size.
        import jobs.paths as _jp
        with patch.dict(sys.modules, {"database": fake_database}), \
                patch.object(_jp, "STATE_DIR", Path(workdir)):
            out = asyncio.run(ci.build_resume_context(
                "probe-sess", model_context_window=model_window,
                working_directory=workdir))
        return len(out)

    with _tf.TemporaryDirectory() as workdir:  # non-git → uncommitted = ""
        complex_session = _session(40, answer_chars=2000)
        big = _fill(complex_session, 1_000_000, workdir)     # 150K token budget
        small = _fill(complex_session, 128_000, workdir)     # 20K token budget
        trivial = _fill(_session(2, 200), 1_000_000, workdir)

    # The elastic delta must be meaningful, not a rounding sliver.
    grew = big > small + 1000
    lean = trivial < 20_000
    # Hard clamp: 128K model → 20K token budget → 80K char ceiling.
    clamp_budget, _, _ = ci._compute_resume_budget(128_000)
    clamped_ok = small <= clamp_budget * 4

    if negative:
        # Teeth: demand the OLD no-growth invariant. On elastic code big>small,
        # so `grew` is True → the old invariant is violated → withhold OK.
        if not grew:  # budget made no difference → the fixed-caps regression
            print("RESUME_FILL_OK")
            return 0
        print("RESUME_FILL_TEETH (budget scaled the fill as required; "
              "teeth withhold OK)")
        return 1
    if grew and lean and clamped_ok:
        print("RESUME_FILL_OK")
        return 0
    print(f"RESUME_FILL_FAIL (big={big}, small={small}, grew={grew}, "
          f"trivial={trivial}, lean={lean}, clamped_ok={clamped_ok})")
    return 1


# NOTE: the vector/semantic scenarios (synonym_guard, stale_index, missing_vector,
# knowledge_live, recall_budget) were REMOVED with their golden cases (retired via
# s_golden-case) when the pure-filesystem recall design (run_e9b8507e, 2026-06-28)
# deleted the vector/Titan leg. Recall is keyword/FTS5/graph only — these remaining
# scenarios exercise the surviving deterministic paths.
_SCENARIOS = {
    "codeintel_live": _codeintel_live,
    "resume_fill": _resume_fill,
    "entry_recall": _entry_recall,
}

# Scenarios that accept a 2nd arg "negative" (teeth mode): break the wire →
# the OK marker must NOT appear → exit 1. Used as each case's negative_command.
_NEGATIVE_CAPABLE = {"codeintel_live", "resume_fill", "entry_recall"}


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
