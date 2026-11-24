"""Tests for core/brain_graph — the entries of MEMORY.md and EVOLUTION.md for the
C&M overlay's 记忆 tab: type counts (nodes: only types that have entries, in display
order), every entry with its date, newest first (entries, and per type in drill).
"""
from __future__ import annotations

from core.brain_graph import (
    EVOLUTION_DISPLAY_ORDER, MEMORY_DISPLAY_ORDER, build_brain_graph, build_evolution_graph,
)
from core.ddd_entry_lifecycle import VALID_TYPES


SAMPLE = """
## Principles
- [principle] **P-one** — first principle (2026-08-01)
  <!-- ref:3 | last:2026-08-01 | decay:active -->
- [principle] **P-two** — second principle (2026-07-01)
  <!-- ref:0 | last:2026-07-01 | decay:dormant -->

## Pitfalls
- [pitfall] **Pit-one** — a trap (2026-08-02)
  <!-- ref:1 | last:2026-08-02 | decay:active -->
"""


# ── MEMORY ──────────────────────────────────────────────────────────────────
def test_nodes_are_only_types_with_entries_in_display_order():
    g = build_brain_graph(SAMPLE)
    assert g["nodes"] == [{"type": "principle", "count": 2}, {"type": "pitfall", "count": 1}]
    assert g["total"] == 3


def test_drill_keeps_every_display_type_key():
    # an absent type is an empty list (a client reading drill[type] never KeyErrors)
    g = build_brain_graph(SAMPLE)
    assert list(g["drill"]) == list(MEMORY_DISPLAY_ORDER)
    assert g["drill"]["decision"] == []


def test_entries_carry_type_and_date_and_are_newest_first():
    g = build_brain_graph(SAMPLE)
    assert [(e["title"], e["type"], e["date"]) for e in g["entries"]] == [
        ("Pit-one", "pitfall", "2026-08-02"),
        ("P-one", "principle", "2026-08-01"),
        ("P-two", "principle", "2026-07-01"),
    ]
    assert all({"title", "meta", "type", "date"} <= set(e) for e in g["entries"])


def test_date_is_the_trailing_stamp_not_an_earlier_date_in_the_text():
    body = ("## Pitfalls\n"
            "- [pitfall] **Sev-2 (2026-10-17): a recovery path removed** — fixed later. "
            "(2026-11-02, run_abc)\n")
    g = build_brain_graph(body)
    assert g["entries"][0]["date"] == "2026-11-02"


def test_date_falls_back_to_the_line_date_then_last_referenced_then_none():
    body = ("## Guidelines\n"
            "- [guideline] **Dated in line** — seen (2026-09-09 once, then again\n"
            "- [guideline] **Only referenced** — no date in text\n"
            "  <!-- ref:1 | last:2026-09-01 | decay:active -->\n"
            "- [guideline] **Undated** — nothing at all\n")
    g = build_brain_graph(body)
    assert [(e["title"], e["date"]) for e in g["entries"]] == [
        ("Dated in line", "2026-09-09"), ("Only referenced", "2026-09-01"), ("Undated", None)]


def test_newest_first_even_when_the_file_is_not_in_date_order():
    # the live MEMORY.md is grouped by section, so the newest entry can sit anywhere
    body = ("## Principles\n- [principle] **Old** — x (2026-01-01)\n"
            "## Guidelines\n- [guideline] **New** — y (2026-11-18)\n"
            "## Pitfalls\n- [pitfall] **Mid** — z (2026-06-01)\n")
    g = build_brain_graph(body)
    assert [e["title"] for e in g["entries"]] == ["New", "Mid", "Old"]


def test_equal_dates_keep_document_order():
    body = "## Pitfalls\n" + "".join(f"- [pitfall] **T{i}** — x (2026-11-02)\n" for i in range(4))
    assert [e["title"] for e in build_brain_graph(body)["entries"]] == ["T0", "T1", "T2", "T3"]


def test_drill_returns_all_entries_of_a_type_newest_first():
    # no cap: 15 entries of one type → all 15, newest first
    body = "## Guidelines\n" + "".join(
        f"- [guideline] **G{i:02d}** — g (2026-08-{i + 1:02d})\n" for i in range(15))
    g = build_brain_graph(body)
    drill = g["drill"]["guideline"]
    assert len(drill) == 15
    assert drill[0]["title"] == "G14" and drill[-1]["title"] == "G00"


def test_total_equals_sum_of_node_counts():
    g = build_brain_graph(SAMPLE)
    assert g["total"] == sum(n["count"] for n in g["nodes"]) == len(g["entries"])


def test_empty_content_safe():
    g = build_brain_graph("")
    assert g["nodes"] == [] and g["entries"] == [] and g["total"] == 0
    assert g["drill"] == {t: [] for t in MEMORY_DISPLAY_ORDER}


def test_memory_display_order_is_exactly_the_seven_valid_types():
    assert set(MEMORY_DISPLAY_ORDER) == set(VALID_TYPES)
    assert len(MEMORY_DISPLAY_ORDER) == len(VALID_TYPES)


def test_valid_types_tuple_unchanged():
    assert VALID_TYPES == ("guideline", "pitfall", "decision", "model", "process",
                           "principle", "correction")


# ── EVOLUTION ───────────────────────────────────────────────────────────────
EVO_SAMPLE = """# SwarmAI Evolution Registry

## Design Philosophy — What "Evolution" Means

### Evolution Target Hierarchy

Some narrative text that is NOT a real entry.

### Core Principle

More narrative.

## Corrections Captured

### C093 | 2026-11-01 · CLASS B: observe before asserting
- **Correction**: **Assert a state only from an observation made in this turn** Before I say…

### C103 | 2026-11-24
- **Correction**: **[C-bias] Next-step proposals stay one-by-one when the found work has independent parts: propose one plan** After…

### C098 | 2026-11-02
- **Correction**: **Whoever dispatched a Hive run judges its landing; ask the person only for what the agent cannot decide** I left…

## Governance Candidates

| ID | Proposed Rule |
|----|--------------|

## Competence Learned

### K001 | 2026-10-29
- **Competence**: **The agent must know its CDF** The CDF is…

### K003 | 2026-10-30
- **Competence**: **Authorship line written as 'Claude' instead of 'SwarmAI' — a recurring mistake** When…

## Failed Evolutions
"""


def test_evolution_counts_competence_entries():
    # the K entries under "## Competence Learned" were missed before (15 shown of 18)
    g = build_evolution_graph(EVO_SAMPLE)
    assert g["nodes"] == [{"type": "correction", "count": 3}, {"type": "competence", "count": 2}]
    assert g["total"] == 5


def test_evolution_entries_newest_first_with_date_id_and_label():
    g = build_evolution_graph(EVO_SAMPLE)
    assert [(e["id"], e["date"], e["type"]) for e in g["entries"]] == [
        ("C103", "2026-11-24", "correction"),
        ("C098", "2026-11-02", "correction"),
        ("C093", "2026-11-01", "correction"),
        ("K003", "2026-10-30", "competence"),
        ("K001", "2026-10-29", "competence"),
    ]
    labels = {e["id"]: e["label"] for e in g["entries"]}
    # the header's own text when it has one; else the body's bold, tags dropped, cut
    # where it turns into its explanation
    assert labels["C093"] == "CLASS B: observe before asserting"
    assert labels["C103"] == "Next-step proposals stay one-by-one when the found work has independent parts"
    assert labels["C098"] == "Whoever dispatched a Hive run judges its landing"
    assert labels["K003"] == "Authorship line written as 'Claude' instead of 'SwarmAI'"
    assert labels["K001"] == "The agent must know its CDF"
    # title stays the full header (backward compatible)
    assert g["entries"][0]["title"] == "C103 | 2026-11-24"


def test_evolution_block_body_stops_at_the_next_section():
    # a ## section between two blocks is not part of the first block's body
    g = build_evolution_graph("### C001 | 2026-01-01\n## Other\n- **Correction**: **Not mine** x\n")
    assert g["entries"][0]["label"] == "C001 | 2026-01-01"


def test_evolution_filters_structural_headers():
    g = build_evolution_graph(EVO_SAMPLE)
    assert all(e["type"] != "entry" for e in g["entries"])


def test_evolution_older_kinds_still_classify():
    body = ("### CLASS A: Confidence Skip Process\n- x\n"
            "### class foo lowercase\n- x\n"
            "### DATA-POINT — held the knowledge\n- x\n"
            "### C049 | 2026-08-11 [Bias A]\n- **Pattern**: improve-before-justify\n")
    g = build_evolution_graph(body)
    by_kind = {n["type"]: n["count"] for n in g["nodes"]}
    assert by_kind == {"class": 2, "correction": 1, "data-point": 1}
    assert g["total"] == sum(by_kind.values())
    # a header whose rest is only a tag and a body without a bold title keeps the header
    c049 = next(e for e in g["entries"] if e["id"] == "C049")
    assert c049["label"] == "C049 | 2026-08-11 [Bias A]"


def test_evolution_nodes_follow_display_order():
    g = build_evolution_graph(EVO_SAMPLE + "### CLASS Z: pattern\n- x\n")
    order = [n["type"] for n in g["nodes"]]
    assert order == [k for k in EVOLUTION_DISPLAY_ORDER if k in order]
    assert order[0] == "class"


def test_evolution_empty_safe():
    g = build_evolution_graph("")
    assert g["nodes"] == [] and g["entries"] == [] and g["total"] == 0
    assert list(g["drill"]) == list(EVOLUTION_DISPLAY_ORDER)
