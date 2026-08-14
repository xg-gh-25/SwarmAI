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

__all__ = [
    "build_brain_graph", "build_evolution_graph",
    "MEMORY_DISPLAY_ORDER", "EVOLUTION_DISPLAY_ORDER",
]

_DRILL_LIMIT = 10  # latest N entries per type in the drill-down

# DISPLAY-priority ordering (Run C). Node ORDER in the graph follows OUR cognitive
# priority, NOT the VALID_TYPES tuple order (which is count/decay-engine ordering)
# and NOT count-size/alphabetical. This is a DISPLAY concern only — VALID_TYPES is
# left untouched (9 callers + the decay engine depend on it; reordering it is a
# NEVER boundary). MEMORY_DISPLAY_ORDER MUST be a permutation of VALID_TYPES (a
# missing type would silently drop a node; an extra would be a phantom).
# SOUL cognitive layering: meta-cognitive → cognitive → operational.
MEMORY_DISPLAY_ORDER = (
    "principle", "correction",      # meta-cognitive
    "decision", "model",            # cognitive
    "guideline", "pitfall", "process",  # operational
)

# EVOLUTION has its own vocabulary (not the 7-type ontology). Priority: recurring
# structural patterns (class) first, then corrections, then their supporting kinds.
# 'meta-correction' is a real _evolution_kind output → included so it never drops;
# 'entry' is the catch-all fallback, LAST (structural ### are filtered out upstream,
# so it is normally zero — kept in the shape for stability).
EVOLUTION_DISPLAY_ORDER = (
    "class", "correction", "meta-correction", "root-cause",
    "data-point", "directive", "failed-evolution", "entry",
)


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

    # Nodes emitted in DISPLAY-priority order (Run C), NOT VALID_TYPES order.
    # MEMORY_DISPLAY_ORDER is a permutation of VALID_TYPES, so every type still
    # appears exactly once — only the sequence changes.
    nodes = []
    for t in MEMORY_DISPLAY_ORDER:
        items = by_type[t]
        active = sum(1 for e in items if e.decay_state == "active")
        dormant = sum(1 for e in items if e.decay_state in ("dormant", "archived"))
        nodes.append({"type": t, "count": counts[t], "active": active, "dormant": dormant})

    # Drill: latest N per type. parse_entries returns DOCUMENT order (top→bottom),
    # and MEMORY.md's auto-writer PREPENDS entries newest-at-TOP (context_health_hook
    # "PREPEND keeps newest-at-TOP"; locked_write --prepend = "newest-first"). So
    # by_type[t] is already NEWEST-FIRST → the newest N is the FIRST slice, not the
    # last. (Gate-2 caught this: [-N:] returned the OLDEST N under a "Latest" label.)
    drill: dict[str, list[dict]] = {}
    for t in VALID_TYPES:
        latest = by_type[t][:_DRILL_LIMIT]  # newest-first → first N = latest N
        drill[t] = [
            {
                "title": e.title,
                "status": e.decay_state,
                "ref_count": e.ref_count,
                # meta: a short human hint (last-referenced date if known, else section)
                "meta": (e.last_referenced.isoformat() if e.last_referenced else e.section or ""),
            }
            for e in latest  # already newest-first (prepend convention)
        ]

    return {"nodes": nodes, "drill": drill, "total": sum(counts.values())}


def build_evolution_graph(evolution_content: str) -> dict:
    """Build the kind-graph + drill for the C&M overlay's Evolution tab (Run C).

    Structurally parallel to build_brain_graph, but for EVOLUTION.md's `### ` block
    entries (corrections / CLASS patterns / data-points / directives), which
    parse_entries CANNOT parse (it recognizes `## ` sections + `- ` bullets only).
    So this uses a dedicated `### ` header scan + archive_browse._evolution_kind for
    classification, and archive_browse.is_real_evolution_entry to FILTER OUT
    structural section markers (Gate-1 BLOCK#2 — "Core Principle" etc. must never
    become 'entry' noise nodes).

    Two honest structural differences from the memory graph (do NOT paper over):
    - EVOLUTION.md has NO decay layer (zero `decay:` markers), so nodes are
      COUNT-ONLY: active == count, dormant == 0. No fabricated active/dormant split.
    - Node kinds are the evolution vocabulary (EVOLUTION_DISPLAY_ORDER), not the
      7-type ontology.

    Returns {nodes:[{type,count,active,dormant}], drill:{kind:[...]}, total} with
    ALL EVOLUTION_DISPLAY_ORDER kinds present (stable shape, even zero-count), nodes
    in display-priority order. Newest-first within a kind (document order = the
    registry's newest-at-top convention). Never raises on malformed content."""
    from core.archive_browse import _evolution_kind, is_real_evolution_entry

    by_kind: dict[str, list[str]] = {k: [] for k in EVOLUTION_DISPLAY_ORDER}
    try:
        for line in evolution_content.splitlines():
            if not line.startswith("### "):
                continue
            header = line[4:].strip()
            # Filter structural section markers — only real entry headers count.
            if not is_real_evolution_entry(header):
                continue
            kind = _evolution_kind(header)
            # Fold any kind NOT in EVOLUTION_DISPLAY_ORDER into the 'entry' catch-all
            # (Gate-2 HIGH-1): a setdefault would create a node OUTSIDE the ordered
            # tuple → counted in `total` but never rendered → total ≠ sum(node counts).
            # Folding to 'entry' keeps total == sum(nodes) always consistent.
            if kind not in by_kind:
                kind = "entry"
            by_kind[kind].append(header)
    except Exception:
        by_kind = {k: [] for k in EVOLUTION_DISPLAY_ORDER}

    nodes = []
    for k in EVOLUTION_DISPLAY_ORDER:
        count = len(by_kind.get(k, []))
        # Count-only: no decay layer in EVOLUTION.md, so active==count, dormant==0.
        nodes.append({"type": k, "count": count, "active": count, "dormant": 0})

    drill: dict[str, list[dict]] = {}
    for k in EVOLUTION_DISPLAY_ORDER:
        headers = by_kind.get(k, [])[:_DRILL_LIMIT]  # document order = newest-first
        drill[k] = [{"title": h, "status": "archived", "ref_count": 0, "meta": ""} for h in headers]

    return {"nodes": nodes, "drill": drill, "total": sum(len(v) for v in by_kind.values())}
