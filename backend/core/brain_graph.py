"""brain_graph — the 7-type knowledge graph for the C&M overlay Memory tab.

DoD2 (goal run_d0ba3f69). The Memory tab shows the agent's sedimented judgment as
a 7-type ontology: principle / correction / decision / guideline / pitfall /
process / model. Each type is a NODE (size = entry count, split active/dormant);
clicking a node drills the latest-10 entries of that type.

**Data source (Gate-1 correction #3):** the 7 node types come from
`Counter(entry_type)` over `parse_entries(MEMORY.md)` using `VALID_TYPES` — NOT
`knowledge_graph.py`, which is an EDGE/relation store (subject-predicate-object,
10 relation predicates) with no node-type concept. This reuses the exact pattern
already proven in `eval._build_learning_dashboard` (parse_entries + entry_type +
decay_state). Backend-primary: every count is served, the frontend invents none (R30).
"""
from __future__ import annotations

from collections import Counter

from core.ddd_entry_lifecycle import VALID_TYPES, parse_entries

__all__ = ["build_brain_graph"]

_DRILL_LIMIT = 10  # latest N entries per type in the drill-down


def build_brain_graph(memory_content: str) -> dict:
    """Build the 7-type node graph + per-type drill-down from MEMORY.md content.

    Returns:
        {
          "nodes": [{"type", "count", "active", "dormant"} × 7],  # ALL 7 types, stable order
          "drill": {type: [{"title","status","meta","ref_count"} × <=10 latest]},
          "total": int,
        }

    ALL 7 types always appear as nodes (even zero-count) so the graph is a stable
    7-node shape the UI can lay out once. Never raises on malformed content —
    parse_entries is defensive and an empty/garbage doc yields all-zero nodes.
    """
    try:
        entries = parse_entries(memory_content)
    except Exception:
        entries = []

    by_type: dict[str, list] = {t: [] for t in VALID_TYPES}
    for e in entries:
        # parse_entries already coerces unknown types to DEFAULT_TYPE (guideline),
        # so entry_type is always in VALID_TYPES — but guard anyway.
        if e.entry_type in by_type:
            by_type[e.entry_type].append(e)

    counts = Counter({t: len(by_type[t]) for t in VALID_TYPES})

    nodes = []
    for t in VALID_TYPES:
        items = by_type[t]
        active = sum(1 for e in items if e.decay_state == "active")
        dormant = sum(1 for e in items if e.decay_state in ("dormant", "archived"))
        nodes.append({"type": t, "count": counts[t], "active": active, "dormant": dormant})

    # Drill: latest N per type. parse_entries returns document order; MEMORY.md
    # sections prepend newest-first in some places and append in others, but the
    # last-N-by-document-position is the pragmatic "recent" slice the mockup shows.
    # Take the last _DRILL_LIMIT (most recently positioned) and present newest-first.
    drill: dict[str, list[dict]] = {}
    for t in VALID_TYPES:
        latest = by_type[t][-_DRILL_LIMIT:]
        drill[t] = [
            {
                "title": e.title,
                "status": e.decay_state,
                "ref_count": e.ref_count,
                # meta: a short human hint (last-referenced date if known, else section)
                "meta": (e.last_referenced.isoformat() if e.last_referenced else e.section or ""),
            }
            for e in reversed(latest)  # newest-first for the list
        ]

    return {"nodes": nodes, "drill": drill, "total": sum(counts.values())}
