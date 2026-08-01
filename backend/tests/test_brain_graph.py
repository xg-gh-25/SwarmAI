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


def test_empty_content_safe():
    g = build_brain_graph("")
    assert {n["type"] for n in g["nodes"]} == set(VALID_TYPES)
    assert all(n["count"] == 0 for n in g["nodes"])
    assert g["drill"] == {t: [] for t in VALID_TYPES}
    assert g["total"] == 0
