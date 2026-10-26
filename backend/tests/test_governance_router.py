"""governance_router is the proposal sink: flock-safe append (identity dedup,
stable id) and removal. The classify -> count -> escalate chain that used to
live here was deleted (docs/design/2026-10-26-evolution-loop-admission.md §3.1).
"""

from __future__ import annotations

import json

from core.evolution import governance_router as gr


def _rows(p):
    return json.loads(p.read_text())


def test_append_stamps_gc_id_and_replaces_same_identity(tmp_path):
    p = tmp_path / "proposals.json"
    gr._append_proposal({"target": "governance", "gc_id": "GC14", "proposed_rule": "v1"}, p)
    gr._append_proposal({"target": "governance", "gc_id": "GC14", "proposed_rule": "v2"}, p)
    rows = _rows(p)
    assert [(r["id"], r["proposed_rule"]) for r in rows] == [("GC14", "v2")]


def test_append_keeps_other_rows(tmp_path):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps([{"skill_name": "legacy"}]))
    gr._append_proposal({"target": "governance", "gc_id": "GC14"}, p)
    gr._append_proposal({"target": "governance", "gc_id": "GC15"}, p)
    rows = _rows(p)
    assert {"skill_name": "legacy"} in rows
    assert [r["id"] for r in rows if r.get("target") == "governance"] == ["GC14", "GC15"]


def test_remove_drops_one_row(tmp_path):
    p = tmp_path / "proposals.json"
    gr._append_proposal({"target": "governance", "gc_id": "GC14"}, p)
    gr._append_proposal({"target": "governance", "gc_id": "GC15"}, p)
    assert gr.remove_governance_proposal("GC14", p) is True
    assert [r["id"] for r in _rows(p)] == ["GC15"]
    assert gr.remove_governance_proposal("GC14", p) is False


def test_chain_symbols_are_gone():
    for name in ("classify_new_corrections", "route_classification", "escalate_class",
                 "_append_pending", "_default_pending_path"):
        assert not hasattr(gr, name), name
