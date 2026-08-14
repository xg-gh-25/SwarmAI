"""Tests for entry cross-references (refs: field) in memory_index.

Validates that entries referencing other entries via [COE02], [DEC01], etc.
get a refs: field in the index, and that related entries are loaded together.

Section names + ID prefixes track the current 7-type knowledge ontology in
memory_index.py (Decisions→DEC, Guidelines→GUI, Pitfalls→PIT, Principles→PRI,
Corrections→COR, Models→MOD, Processes→PRC, COE Registry→COE). The older
Key Decisions/Recent Context/Lessons Learned sections are no longer scanned —
see PERMANENT_SECTIONS / ACTIVE_SECTIONS.
"""
from __future__ import annotations



SAMPLE_MEMORY = """\
## COE Registry
- 2026-03-15: **Streaming failure** — SSE drops on reconnect. Sessions: 2026-03-15
- 2026-03-18: **Lock timeout** — flock deadlock on concurrent writes. Sessions: 2026-03-18

## Decisions
- 2026-03-19: **Use WAL mode** — SQLite WAL for concurrent access. Related to [COE02].
- 2026-03-20: **Batch distillation** — Single lock per section. See [COE02] and [RC15].

## Guidelines
- 2026-03-22: **Always check lock state** — Prevents deadlock per [COE02].
- 2026-03-23: **Normal guideline** — No cross-references here.

## Open Threads
### P0 — Blocking
- 🔴 **Critical bug** (reported 1x: 2026-03-25)
"""


class TestEntryRefs:
    """Test suite for entry cross-reference detection."""






