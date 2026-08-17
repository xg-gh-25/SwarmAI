#!/usr/bin/env python3
"""Memory WRITE-machinery chain probe — deterministic eval probes for the
decay / archive / index machinery that the golden set previously left
UNGUARDED (only the recall READ path had behavioral cases; see
recall_chain_probe.py for the sibling read-side probes).

Each scenario drives the REAL function (mocks NOTHING under test — GUI26
prompt-source = answer-source) on a synthetic in-memory fixture, and prints a
`<NAME>_OK` marker on success. Each is `negative`-capable (teeth): the negative
mode demands the OLD/broken invariant, which is FALSE on the fixed code, so the
OK marker is withheld → exit 1. A reverted/no-op build flips the marker → the
teeth fire. This is the mutation-killing pattern that keeps a probe non-vacuous.

Scenarios (run_2a5ff539, 2026-06-28):
  decay_dormant  — assess_decay(dormant_days=45) ages a 50d entry to dormant
                   that the default 90d leaves active (locks A2 per-section decay).
  reclaim_shrink — reclaim_noise_entries archives a >180d dormant operational
                   entry AND shrinks the source content (lifts the pytest E2E to
                   system level). FIXTURE PRE-SETS decay:dormant in the metadata
                   comment — reclaim reads decay_state from the markdown, it does
                   NOT call assess_decay; without the comment parse_entries
                   defaults to active → vacuous (Gate-0 finding, run_2a5ff539).
  archive_recall — a synthetic MEMORY-archive entry indexed into an ISOLATED
                   :memory: KnowledgeStore is FTS5-retrievable (proves archived
                   memory is reachable by the library recall leg).

Usage: python backend/scripts/memory_chain_probe.py <scenario> [negative]
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Import from the backend package root (mirror recall_chain_probe bootstrap).
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


# ── decay_dormant ─────────────────────────────────────────────────────────────
def _decay_dormant(negative: bool = False) -> int:
    """A2 per-section decay (run_55cb38d6): assess_decay(dormant_days=45) must
    transition a 50-day-idle ref:0 operational entry to dormant, while the
    default (None → global 90d) leaves it active. Drives the REAL assess_decay.

    POSITIVE: @45 → dormant AND @90(default) → active. Prints DECAY_DORMANT_OK.
    negative (teeth): demand the OLD invariant — "@45 leaves it active". That is
    FALSE on the fixed code (it goes dormant) → withhold OK → exit 1. A build
    that ignored dormant_days (used the global) would leave it active → teeth
    would pass, which is exactly the regression a mutation test must catch."""
    from core.ddd_entry_lifecycle import EntryMetadata, assess_decay

    today = date(2026, 6, 28)
    fifty_days = (today - timedelta(days=50)).isoformat()

    def _mk():
        return EntryMetadata(
            title="Fifty-day idle operational entry",
            entry_type="guideline", ref_count=0, last_referenced=None,
            decay_state="active", created_date=date.fromisoformat(fifty_days),
            section="Guidelines",
        )

    at_45 = assess_decay([_mk()], today, dormant_days=45)
    at_90 = assess_decay([_mk()], today)  # default None → global 90

    went_dormant_at_45 = len(at_45) == 1 and at_45[0].new_state == "dormant"
    stayed_active_at_90 = at_90 == []

    if negative:
        # Teeth: demand "@45 leaves it active" (the broken/un-A2 invariant).
        if went_dormant_at_45:
            print("DECAY_DORMANT_TEETH (entry went dormant at 45d = A2 live; "
                  "old 'stays active' invariant false)")
            return 1
        print("DECAY_DORMANT_OK")  # only on a reverted build (A2 absent)
        return 0

    if went_dormant_at_45 and stayed_active_at_90:
        print("DECAY_DORMANT_OK")
        return 0
    print(f"DECAY_DORMANT_FAIL (45d→dormant={went_dormant_at_45}, "
          f"90d→active={stayed_active_at_90})")
    return 1


# ── reclaim_shrink ────────────────────────────────────────────────────────────
def _reclaim_fixture(today: date) -> str:
    """A MEMORY whose Pitfalls section holds ONE reclaimable entry — a >180d
    dormant ref:0 operational (pitfall) entry — plus one active guideline that
    MUST survive. CRITICAL (Gate-0): the metadata comment PRE-SETS decay:dormant
    because reclaim_noise_entries reads decay_state from the markdown and never
    calls assess_decay; without it parse_entries defaults to active and the
    entry is never reclaimed → the probe would be a silent false-positive."""
    old = (today - timedelta(days=400)).isoformat()
    recent = (today - timedelta(days=2)).isoformat()
    return (
        "## Guidelines\n"
        f"- [guideline] **Keep me — active and used** — recent. ({recent}, run_keep)\n"
        f"  <!-- ref:5 | last:{recent} | decay:active -->\n\n"
        "## Pitfalls\n"
        f"- [pitfall] **Reclaim me — ancient dormant noise** — long dead. ({old}, run_old)\n"
        f"  <!-- ref:0 | last:none | decay:dormant -->\n"
    )


def _reclaim_shrink(negative: bool = False) -> int:
    """reclaim_noise_entries must MOVE a stale dormant entry OUT to the archive
    file AND physically shrink the source content (not just relabel in place).
    Drives the REAL reclaim on a temp dir; mocks nothing.

    POSITIVE: archived==1, stale entry gone from new_content, active entry kept,
    len(new_content) < len(original), archive file gained the entry. Prints
    RECLAIM_SHRINK_OK.
    negative (teeth): demand "content did NOT shrink". FALSE on fixed code (it
    shrinks) → withhold OK → exit 1. A build whose strip step is broken would
    not shrink → teeth would pass (the regression we want to catch)."""
    from core.ddd_entry_lifecycle import reclaim_noise_entries

    today = date(2026, 6, 28)
    original = _reclaim_fixture(today)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "MEMORY.md"
        src.write_text(original, encoding="utf-8")
        report = reclaim_noise_entries(
            original, today, tmp,
            archive_name="MEMORY-archive.md", source_path=src, dry_run=False,
        )
        new_content = src.read_text(encoding="utf-8")
        archive = tmp / "MEMORY-archive.md"

        shrank = len(new_content) < len(original)
        stale_gone = "Reclaim me" not in new_content
        active_kept = "Keep me" in new_content
        archived_one = report.archived == 1
        in_archive = archive.exists() and "Reclaim me" in archive.read_text(encoding="utf-8")

    if negative:
        # Teeth: demand "content did NOT shrink".
        if shrank:
            print("RECLAIM_SHRINK_TEETH (content shrank = reclaim live; "
                  "old 'no-shrink' invariant false)")
            return 1
        print("RECLAIM_SHRINK_OK")  # only on a reverted build (strip broken)
        return 0

    if shrank and stale_gone and active_kept and archived_one and in_archive:
        print("RECLAIM_SHRINK_OK")
        return 0
    print(f"RECLAIM_SHRINK_FAIL (shrank={shrank}, stale_gone={stale_gone}, "
          f"active_kept={active_kept}, archived={report.archived}, in_archive={in_archive})")
    return 1


# index_ot_once probe RETIRED (2026-08-14): it drove generate_memory_index, which
# was DELETED with the in-prompt index (live MEMORY is full-injected; recall scans
# body-BM25). No index to probe.


# ── archive_recall ────────────────────────────────────────────────────────────
_ARCHIVE_MARKER = "zephyr_archived_sidecar_decommission_marker"


def _archive_recall(negative: bool = False) -> int:
    """An archived MEMORY-archive entry must be FTS5-retrievable. Proves the
    library recall leg can reach long-term archived memory (Archives/ was
    un-skipped, run_e9b8507e). Builds an ISOLATED :memory: KnowledgeStore —
    never touches the live ~/.swarm-ai/data.db — and drives the REAL
    ensure_tables + upsert_chunk + fts5_search (no embedding leg).

    POSITIVE: fts5_search for the distinctive marker returns ≥1 hit whose
    source_file is the archive file. Prints ARCHIVE_RECALL_OK.
    negative (teeth): demand a MISS. FALSE on fixed code (it hits) → withhold OK
    → exit 1. A build where FTS indexing is broken would miss → teeth fire."""
    import hashlib
    from core.knowledge_store import KnowledgeStore

    conn = sqlite3.connect(":memory:")
    try:
        # ensure_tables() creates a vec0 virtual table → the sqlite-vec
        # extension must be loaded into THIS connection (same as the real
        # vec_db.py connection builder). FTS5 is built into sqlite; only vec0
        # needs the extension. If sqlite-vec is unavailable, skip cleanly
        # rather than error — the probe's subject is FTS5 recall, not vectors.
        try:
            import sqlite_vec as _sqlite_vec
            conn.enable_load_extension(True)
            _sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as exc:  # noqa: BLE001
            # SKIP is neither pass nor fail: the marker-based harness already
            # reports non-pass (the _OK/_TEETH marker is absent), but return a
            # DISTINCT exit code (2) so exit-code consumers also see non-pass —
            # never a false-green (Gate-2 LOW, run_2a5ff539).
            print(f"ARCHIVE_RECALL_SKIP (sqlite-vec unavailable: {exc})")
            return 2
        store = KnowledgeStore(conn)
        store.ensure_tables()
        content = (
            f"- [decision] **{_ARCHIVE_MARKER}: 4-platform backend** — sidecar "
            f"decommissioned, replaced by platform-isolated lifecycle. (2026-05-08)"
        )
        store.upsert_chunk(
            source_file="Knowledge/Archives/MEMORY-archive-2026-05.md",
            chunk_index=0, heading="Decisions", content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            # FTS5-only, no vector leg — upsert_chunk dropped its `embedding`
            # param when the vector leg was torn out (knowledge_store.py:216).
        )
        hits = store.fts5_search(_ARCHIVE_MARKER, limit=5)
    finally:
        conn.close()

    hit = bool(hits) and any(
        "Archives" in (h.get("source_file") or "") for h in hits
    )

    if negative:
        # Teeth: demand a MISS.
        if hit:
            print("ARCHIVE_RECALL_TEETH (archive entry retrieved = recall covers "
                  "archives; old 'unreachable' invariant false)")
            return 1
        print("ARCHIVE_RECALL_OK")  # only on a reverted build (FTS broken)
        return 0

    if hit:
        print("ARCHIVE_RECALL_OK")
        return 0
    print(f"ARCHIVE_RECALL_FAIL (hits={len(hits)}, archive_attributed={hit})")
    return 1


_SCENARIOS = {
    "decay_dormant": _decay_dormant,
    "reclaim_shrink": _reclaim_shrink,
    "archive_recall": _archive_recall,
}
_NEGATIVE_CAPABLE = set(_SCENARIOS)  # all teeth-capable


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not (1 <= len(argv) <= 2) or argv[0] not in _SCENARIOS:
        print(f"usage: memory_chain_probe.py <{'|'.join(_SCENARIOS)}> [negative]")
        return 2
    scenario, negative = argv[0], (len(argv) == 2 and argv[1] == "negative")
    return _SCENARIOS[scenario](negative=negative)


if __name__ == "__main__":
    raise SystemExit(main())
