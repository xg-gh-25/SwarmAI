"""Tests for build_brain_graph — the 7-type knowledge graph for the Memory tab.

DoD2 (goal run_d0ba3f69). Gate-1 correction #3: the 7 node types come from
Counter(entry_type) over parse_entries(MEMORY.md) using VALID_TYPES — NOT
knowledge_graph.py (which is an edge/relation store with no node-type concept).
"""
from __future__ import annotations

from core.brain_graph import build_brain_graph
from core.ddd_entry_lifecycle import VALID_TYPES


SAMPLE = """
## Principles
- [principle] **P-one** — first principle
  <!-- ref:3 | last:2026-08-01 | decay:active -->
- [principle] **P-two** — second principle
  <!-- ref:0 | last:2026-07-01 | decay:dormant -->

## Pitfalls
- [pitfall] **Pit-one** — a trap
  <!-- ref:1 | last:2026-08-02 | decay:active -->
"""


def test_returns_all_seven_types_as_nodes():
    g = build_brain_graph(SAMPLE)
    node_types = {n["type"] for n in g["nodes"]}
    # every VALID_TYPE present as a node (even zero-count ones — a stable 7-node graph)
    assert node_types == set(VALID_TYPES)


def test_node_counts_reflect_parsed_entries():
    g = build_brain_graph(SAMPLE)
    by_type = {n["type"]: n for n in g["nodes"]}
    assert by_type["principle"]["count"] == 2
    assert by_type["pitfall"]["count"] == 1
    assert by_type["decision"]["count"] == 0  # none in sample → zero node, still present


def test_active_vs_dormant_split_per_node():
    g = build_brain_graph(SAMPLE)
    principle = next(n for n in g["nodes"] if n["type"] == "principle")
    # one active + one dormant principle
    assert principle["active"] == 1
    assert principle["dormant"] == 1


def test_drill_returns_latest_entries_of_a_type():
    g = build_brain_graph(SAMPLE)
    drill = g["drill"]["principle"]
    assert len(drill) == 2
    # each drill row carries id/title/status/meta for the list
    row = drill[0]
    assert "title" in row and "status" in row
    assert row["status"] in ("active", "dormant", "archived")


def test_drill_capped_at_ten_latest():
    # 15 entries of one type → drill returns only the latest 10
    body = "## Guidelines\n" + "".join(
        f"- [guideline] **G{i}** — g{i}\n  <!-- ref:0 | last:2026-08-01 | decay:active -->\n"
        for i in range(15)
    )
    g = build_brain_graph(body)
    assert len(g["drill"]["guideline"]) == 10


def test_drill_is_newest_first_not_oldest(tmp_path=None):
    """Gate-2 regression: MEMORY.md prepends newest-at-TOP, so parse_entries yields
    newest-first. The drill must show the NEWEST entries under its 'Latest' label —
    i.e. the FIRST N in document order, presented top-first. A [-N:] slice would
    return the OLDEST N (the bug this pins)."""
    # 12 guideline entries, document order = newest-first (G-new at top … G-old at bottom)
    body = "## Guidelines\n" + "".join(
        f"- [guideline] **G{i:02d}** — entry {i}\n  <!-- ref:0 | last:2026-08-01 | decay:active -->\n"
        for i in range(12)  # G00 (newest, top) … G11 (oldest, bottom)
    )
    g = build_brain_graph(body)
    drill = g["drill"]["guideline"]
    assert len(drill) == 10
    # newest (G00, top of doc) MUST be first; the two oldest (G10, G11) MUST be dropped
    assert drill[0]["title"] == "G00", "drill[0] must be the newest (top-of-doc) entry"
    titles = [d["title"] for d in drill]
    assert "G11" not in titles and "G10" not in titles, "the 2 oldest (bottom) must be dropped, not the newest"


def test_empty_content_safe():
    g = build_brain_graph("")
    assert {n["type"] for n in g["nodes"]} == set(VALID_TYPES)
    assert all(n["count"] == 0 for n in g["nodes"])
    assert g["drill"] == {t: [] for t in VALID_TYPES}
    assert g["total"] == 0


def test_build_evolution_graph_total_equals_sum_of_node_counts():
    """Gate-2 HIGH-1: total MUST equal sum(node counts). An unknown kind is folded
    into 'entry' (not setdefault'd into an unrendered node), so no count is ever
    lost from the node list while still counted in total."""
    from core.brain_graph import build_evolution_graph
    g = build_evolution_graph(EVO_SAMPLE)
    node_total = sum(n["count"] for n in g["nodes"])
    assert g["total"] == node_total


def test_build_evolution_graph_lowercase_header_classifies_by_kind():
    """Gate-2 MED-2: a lowercase 'class …' header must classify as 'class', not
    silently fall to 'entry' (is_real_evolution_entry is IGNORECASE, so _evolution_kind
    must be too)."""
    from core.brain_graph import build_evolution_graph
    g = build_evolution_graph("### class foo lowercase\n- x\n")
    by_kind = {n["type"]: n for n in g["nodes"]}
    assert by_kind["class"]["count"] == 1
    assert by_kind["entry"]["count"] == 0


# ── Run C: display-priority ordering (Memory) ───────────────────────────────
EVO_SAMPLE = """# SwarmAI Evolution Registry

## Design Philosophy — What "Evolution" Means

### Evolution Target Hierarchy

Some narrative text that is NOT a real entry.

### Core Principle

More narrative.

## Corrections Captured

### C049 | 2026-08-11 [Bias A]
- **Pattern**: improve-before-justify

### C048 | 2026-08-11 [Bias]
- **Pattern**: overconfidence

### CLASS A: Confidence Skip Process
- pattern text

### DATA-POINT — held the bug-class knowledge
- data point text

### DIRECTIVE-OVERRIDE — cultivation autonomy
- directive text
"""


def test_memory_nodes_in_display_priority_order_not_valid_types_order():
    from core.brain_graph import MEMORY_DISPLAY_ORDER
    g = build_brain_graph(SAMPLE)
    order = [n["type"] for n in g["nodes"]]
    assert order == list(MEMORY_DISPLAY_ORDER)
    assert order[0] == "principle"
    assert order[-1] == "process"
    assert order != list(VALID_TYPES)


def test_memory_display_order_is_exactly_the_seven_valid_types():
    from core.brain_graph import MEMORY_DISPLAY_ORDER
    assert set(MEMORY_DISPLAY_ORDER) == set(VALID_TYPES)
    assert len(MEMORY_DISPLAY_ORDER) == len(VALID_TYPES)


def test_build_evolution_graph_classifies_real_kinds():
    from core.brain_graph import build_evolution_graph
    g = build_evolution_graph(EVO_SAMPLE)
    by_kind = {n["type"]: n for n in g["nodes"]}
    assert by_kind["correction"]["count"] == 2
    assert by_kind["class"]["count"] == 1
    assert by_kind["data-point"]["count"] == 1
    assert by_kind["directive"]["count"] == 1


def test_build_evolution_graph_filters_structural_headers_no_entry_noise():
    from core.brain_graph import build_evolution_graph
    g = build_evolution_graph(EVO_SAMPLE)
    by_kind = {n["type"]: n for n in g["nodes"]}
    assert by_kind.get("entry", {"count": 0})["count"] == 0
    assert g["total"] == 5


def test_build_evolution_graph_count_only_no_fabricated_decay():
    from core.brain_graph import build_evolution_graph
    g = build_evolution_graph(EVO_SAMPLE)
    for n in g["nodes"]:
        assert n["active"] == n["count"]
        assert n["dormant"] == 0


def test_evolution_nodes_in_display_priority_order():
    from core.brain_graph import build_evolution_graph, EVOLUTION_DISPLAY_ORDER
    g = build_evolution_graph(EVO_SAMPLE)
    order = [n["type"] for n in g["nodes"]]
    assert order == list(EVOLUTION_DISPLAY_ORDER)
    assert order[0] == "class"


def test_build_evolution_graph_empty_safe():
    from core.brain_graph import build_evolution_graph, EVOLUTION_DISPLAY_ORDER
    g = build_evolution_graph("")
    assert [n["type"] for n in g["nodes"]] == list(EVOLUTION_DISPLAY_ORDER)
    assert all(n["count"] == 0 for n in g["nodes"])
    assert g["total"] == 0


def test_valid_types_tuple_unchanged():
    assert VALID_TYPES == ("guideline", "pitfall", "decision", "model", "process",
                           "principle", "correction")
