"""governance_miner: the GC table is the only store of governance proposals.
A row is pending at 3/3 with a filled Checked cell and no verdict; a verdict is
written into its Count cell in place (docs/design/2026-10-26-evolution-loop-admission.md §3.2)."""

from __future__ import annotations

import os
import stat
from datetime import date

import pytest

from core.evolution import governance_miner as gm

HEADER = (
    "## Governance Candidates\n\n"
    "| ID | Proposed Rule | Evidence | Count | Since | Checked |\n"
    "|----|---------------|----------|-------|-------|---------|\n"
)
CHECKED = "checked against: R27, P1 — new because it names the grep step"


def _evo(tmp_path, rows, before="", after="\n## Later\n| GC1 | other table | x | 3/3 | d | " + CHECKED + " |\n"):
    p = tmp_path / "EVOLUTION.md"
    p.write_text("# Evolution\n\n" + before + HEADER + "\n".join(rows) + "\n" + after, encoding="utf-8")
    return p


def _ids(p):
    return [g.gc_id for g in gm.generate_governance_proposals(p)]


@pytest.mark.parametrize("row,expected", [
    (f'| GC1 | "Rule one. More." | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |', ["GC1"]),
    ('| GC2 | "Rule two" | C1, C2, C3 | 3/3 | 2026-10-01 |  |', []),            # Checked empty
    ('| GC3 | "Rule three" | C1, C2, C3 | 3/3 | 2026-10-01 | — |', []),          # placeholder
    ('| GC4 | "Rule four" | C1, C2, C3 | 3/3 | 2026-10-01 |', []),               # old 5-col row
    (f'| GC5 | "Rule five" | C1, C2 | 2/3 | 2026-10-01 | {CHECKED} |', []),      # below threshold
    (f'| GC6 | "Rule six" | C1 | ✔ ACCEPTED 2026-10-02 | 2026-10-01 | {CHECKED} |', []),
    (f'| GC7 | "Rule seven" | C1 | ✖ REJECTED 2026-10-02 | 2026-10-01 | {CHECKED} |', []),
    (f'| GC8 | "a \\\\| b" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |', ["GC8"]),  # escaped pipe stays in its cell
])
def test_admission(tmp_path, row, expected):
    assert _ids(_evo(tmp_path, [row])) == expected


def test_rows_outside_the_section_are_ignored(tmp_path):
    p = _evo(tmp_path, [], before="| GC9 | before | x | 3/3 | d | " + CHECKED + " |\n\n")
    assert _ids(p) == []                 # the GC1/GC9 rows live in other sections


def test_proposal_shape(tmp_path):
    p = _evo(tmp_path, [f'| GC1 | "Rule one. More detail." | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |'])
    d = gm.generate_governance_proposals(p)[0].to_proposal_dict()
    assert (d["id"], d["gc_id"], d["title"], d["checked"]) == ("GC1", "GC1", "Rule one", CHECKED)
    assert d["evidence"] == ["C1", "C2", "C3"]
    assert "confidence" not in d and "source_class" not in d


@pytest.mark.parametrize("accepted,mark", [(True, gm.ACCEPTED), (False, gm.REJECTED)])
def test_mark_rewrites_only_that_cell(tmp_path, accepted, mark):
    rows = [f'| GC1 | "Rule one" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |',
            f'| GC2 | "Rule two" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |']
    p = _evo(tmp_path, rows)
    before = p.read_text(encoding="utf-8")
    assert gm.mark_gc_decision(p, "GC1", accepted=accepted, today=date(2026, 10, 26)) is True
    expected = before.replace('| GC1 | "Rule one" | C1, C2, C3 | 3/3 |',
                              f'| GC1 | "Rule one" | C1, C2, C3 | {mark} 2026-10-26 |', 1)
    assert p.read_text(encoding="utf-8") == expected   # the GC1 row in "## Later" is untouched
    assert _ids(p) == ["GC2"]


def test_a_verdict_is_never_overwritten(tmp_path):
    p = _evo(tmp_path, [f'| GC1 | "Rule one" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |'])
    assert gm.mark_gc_decision(p, "GC1", accepted=True) is True
    after_first = p.read_text(encoding="utf-8")
    assert gm.mark_gc_decision(p, "GC1", accepted=False) is False
    assert p.read_text(encoding="utf-8") == after_first


def test_duplicate_ids_are_all_marked(tmp_path):
    rows = [f'| GC1 | "old" | C1 | ✔ ACCEPTED 2026-01-01 | 2026-01-01 | {CHECKED} |',
            f'| GC1 | "reused" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |']
    p = _evo(tmp_path, rows)
    assert _ids(p) == ["GC1"]
    assert gm.mark_gc_decision(p, "GC1", accepted=False) is True
    assert _ids(p) == []
    assert "✔ ACCEPTED 2026-01-01" in p.read_text(encoding="utf-8")   # the old verdict stays


def test_crlf_and_mode_are_preserved(tmp_path):
    p = tmp_path / "EVOLUTION.md"
    body = (HEADER + f'| GC1 | "Rule one" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n').replace("\n", "\r\n")
    p.write_bytes(body.encode("utf-8"))
    os.chmod(p, 0o644)
    assert gm.mark_gc_decision(p, "GC1", accepted=True, today=date(2026, 10, 26)) is True
    raw = p.read_bytes().decode("utf-8")
    assert raw == body.replace("| 3/3 |", f"| {gm.ACCEPTED} 2026-10-26 |")
    assert stat.S_IMODE(p.stat().st_mode) == 0o644


def test_mark_unknown_row(tmp_path):
    p = _evo(tmp_path, [f'| GC1 | "Rule one" | C1 | 3/3 | 2026-10-01 | {CHECKED} |'])
    before = p.read_text(encoding="utf-8")
    assert gm.mark_gc_decision(p, "GC9", accepted=True) is False
    assert p.read_text(encoding="utf-8") == before


def test_two_undecided_duplicates_are_both_marked(tmp_path):
    rows = [f'| GC1 | "a" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |',
            f'| GC1 | "b" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |']
    p = _evo(tmp_path, rows)
    assert gm.mark_gc_decision(p, "GC1", accepted=True) is True
    assert _ids(p) == []
