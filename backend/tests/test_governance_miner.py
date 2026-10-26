"""governance_miner: a GC row becomes a proposal only at 3x evidence with a
filled Checked cell and no verdict; a verdict is written back so the row is
never mined again (docs/design/2026-10-26-evolution-loop-admission.md §3.2)."""

from __future__ import annotations

from datetime import date

import pytest

from core.evolution import governance_miner as gm

HEADER = (
    "## Governance Candidates\n\n"
    "| ID | Proposed Rule | Evidence | Count | Since | Checked |\n"
    "|----|---------------|----------|-------|-------|---------|\n"
)


def _evo(tmp_path, rows: list[str], extra: str = "") -> object:
    p = tmp_path / "EVOLUTION.md"
    p.write_text("# Evolution\n\n" + extra + HEADER + "\n".join(rows) + "\n\n## Later\ntext\n", encoding="utf-8")
    return p


CHECKED = "checked against: R27, P1 — new because it names the grep step"


@pytest.mark.parametrize("row,expected", [
    (f'| GC1 | "Rule one. More." | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |', ["GC1"]),
    ('| GC2 | "Rule two" | C1, C2, C3 | 3/3 | 2026-10-01 |  |', []),            # Checked empty
    ('| GC3 | "Rule three" | C1, C2, C3 | 3/3 | 2026-10-01 | — |', []),          # placeholder
    ('| GC4 | "Rule four" | C1, C2, C3 | 3/3 | 2026-10-01 |', []),               # old 5-col row
    (f'| GC5 | "Rule five" | C1, C2 | 2/3 | 2026-10-01 | {CHECKED} |', []),      # below threshold
    (f'| GC6 | "Rule six" | C1 | ✅ PROMOTED 2026-10-02 | 2026-10-01 | {CHECKED} |', []),
    (f'| GC7 | "Rule seven" | C1 | ✖ REJECTED 2026-10-02 | 2026-10-01 | {CHECKED} |', []),
])
def test_admission(tmp_path, row, expected):
    p = _evo(tmp_path, [row])
    assert [g.gc_id for g in gm.generate_governance_proposals(p)] == expected


def test_proposal_carries_title_and_checked(tmp_path):
    p = _evo(tmp_path, [f'| GC1 | "Rule one. More detail." | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |'])
    (prop,) = gm.generate_governance_proposals(p)
    d = prop.to_proposal_dict()
    assert d["target"] == "governance" and d["gc_id"] == "GC1"
    assert d["title"] == "Rule one"
    assert d["checked"] == CHECKED
    assert d["evidence"] == ["C1", "C2", "C3"]
    assert "confidence" not in d and "source_class" not in d


def test_class_sections_never_become_proposals(tmp_path):
    extra = ("### CLASS B: Inference Without Verification [12 entries]\n"
             "- **Pattern**: assert without reading\n\n")
    p = _evo(tmp_path, [], extra=extra)
    assert gm.generate_governance_proposals(p) == []


@pytest.mark.parametrize("accepted,mark", [(True, gm.PROMOTED), (False, gm.REJECTED)])
def test_mark_decision_rewrites_only_that_cell_and_stops_mining(tmp_path, accepted, mark):
    rows = [f'| GC1 | "Rule one" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |',
            f'| GC2 | "Rule two" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |']
    p = _evo(tmp_path, rows)
    before = p.read_text(encoding="utf-8")
    assert gm.mark_gc_decision(p, "GC1", accepted=accepted, today=date(2026, 10, 26)) is True
    after = p.read_text(encoding="utf-8")
    expected = before.replace("| GC1 | \"Rule one\" | C1, C2, C3 | 3/3 |",
                              f"| GC1 | \"Rule one\" | C1, C2, C3 | {mark} 2026-10-26 |")
    assert after == expected
    assert [g.gc_id for g in gm.generate_governance_proposals(p)] == ["GC2"]


def test_mark_decision_unknown_row(tmp_path):
    p = _evo(tmp_path, [f'| GC1 | "Rule one" | C1 | 3/3 | 2026-10-01 | {CHECKED} |'])
    before = p.read_text(encoding="utf-8")
    assert gm.mark_gc_decision(p, "GC9", accepted=True) is False
    assert p.read_text(encoding="utf-8") == before
