"""scripts/memory_relocate.py (design 2026-10-31-memory-chain-audit M2).

Questions:
- is an entry in the plan written to its destination, under the section CDF uses, then
  gone from MEMORY, with its metadata line and nothing else?
- is an entry the destination already holds not written twice?
- does running it again change nothing?
- does a CDF entry's unit record its new place?
"""
from __future__ import annotations

import json

import pytest


def _entry(kind, title, marker=""):
    return (f"- [{kind}] **{title}** body of {title}.{marker}\n"
            "  <!-- ref:0 | last:2026-10-30 | decay:active -->\n")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    (ws / ".context").mkdir(parents=True)
    (ws / ".context/MEMORY.md").write_text(
        "## Principles\n\n" + _entry("principle", "Keep this one") + "\n"
        + _entry("principle", "Move to product") + "\n## Pitfalls\n\n"
        + _entry("pitfall", "A narrow tool fact", " <!-- cand:cand-t.unit -->") + "\n"
        + _entry("pitfall", "Already in the DDD") + "\n## Models\n\n" + _entry("model", "Stays too"),
        encoding="utf-8")
    und = ws / "Projects/SwarmAI/2-understanding"
    und.mkdir(parents=True)
    (ws / "Projects/SwarmAI/.project.json").write_text('{"name":"SwarmAI"}', encoding="utf-8")
    (und / "PRODUCT.md").write_text("# Product\n\n## Vision\n\n- existing.\n", encoding="utf-8")
    (und / "IMPROVEMENT.md").write_text("# I\n\n## What Failed\n\n" + _entry("pitfall", "Already in the DDD")
                                        + "\n## What to Watch For\n", encoding="utf-8")
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,content_hash,"
                 "envelope,project_snapshot,created_at,source_v2) VALUES('cand-t','cand-t','S7','s','session','{}',"
                 "'v2','h','{}','{}','2026-10-30','S7')")
    conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,owner_epoch,updated_at,destination)"
                 " VALUES('cand-t.unit','cand-t','{}','written',1,'t',?)",
                 (json.dumps({"store": "MEMORY", "relative_path": ".context/MEMORY.md"}),))
    conn.commit(); conn.close()
    from scripts import memory_relocate
    monkeypatch.setattr(memory_relocate, "PLAN", [
        ("Move to product", "ddd:product", "a product principle"),
        ("A narrow tool fact", "general:General knowledge", "a tool fact"),
        ("Already in the DDD", "ddd:lesson", "a project lesson")])
    return ws


def test_planned_entries_move_and_the_rest_stays(ws):
    from scripts import memory_relocate
    report = memory_relocate.run(ws, apply=True)
    assert report["moved"] == 3 and report["already_in_destination"] == 1
    memory = (ws / ".context/MEMORY.md").read_text(encoding="utf-8")
    for gone in ("Move to product", "A narrow tool fact", "Already in the DDD"):
        assert gone not in memory
    assert "Keep this one" in memory and "Stays too" in memory and "\n\n\n" not in memory
    product = (ws / "Projects/SwarmAI/2-understanding/PRODUCT.md").read_text(encoding="utf-8")
    assert "## Vision\n\n- existing.\n\n- [principle] **Move to product**" in product
    assert "decay:active" in product.split("Move to product")[-1]                  # the metadata line moved with it
    general = (ws / "Knowledge/Library/general-knowledge.md").read_text(encoding="utf-8")
    assert "## What Failed" in general and "A narrow tool fact" in general and "cand:cand-t.unit" in general
    improvement = (ws / "Projects/SwarmAI/2-understanding/IMPROVEMENT.md").read_text(encoding="utf-8")
    assert improvement.count("**Already in the DDD**") == 1                              # not written twice
    from core.ingestion.store import connect_ro
    conn = connect_ro()
    dest = json.loads(conn.execute("SELECT destination FROM ingestion_units WHERE unit_id='cand-t.unit'").fetchone()[0])
    moved = conn.execute("SELECT count(*) FROM ingestion_events WHERE kind='moved'").fetchone()[0]
    conn.close()
    assert dest["relative_path"] == "Knowledge/Library/general-knowledge.md" and dest["store"] == "COMMON_DDD"
    assert moved == 1


def test_running_it_again_changes_nothing(ws):
    from scripts import memory_relocate
    memory_relocate.run(ws, apply=True)
    before = {p: p.read_bytes() for p in ws.rglob("*.md")}
    assert memory_relocate.run(ws, apply=True)["to_move"] == 0
    assert {p: p.read_bytes() for p in ws.rglob("*.md")} == before


def test_a_dry_run_writes_nothing(ws):
    from scripts import memory_relocate
    before = {p: p.read_bytes() for p in ws.rglob("*.md")}
    assert memory_relocate.run(ws, apply=False)["to_move"] == 3
    assert {p: p.read_bytes() for p in ws.rglob("*.md")} == before


def test_open_threads_keep_their_knowledge_and_the_section_leaves(ws):
    from scripts import memory_relocate
    memory = ws / ".context/MEMORY.md"
    memory.write_text(memory.read_text(encoding="utf-8") + (
        "\n## Open Threads\n\n### P0 — Blocking\n"
        "- 🔴 **streaming freeze + overall slowness — stalls** (reported 1x)\n  Status: diagnosed.\n"
        "- 🔴 **chat Interrupted chip** (reported 1x)\n  Status: COE candidate.\n\n### P1\n"
        "- 🟡 **Frontend reconcile race — guarded** — watch it.\n"
        "  <!-- ref:0 | last:2026-08-06 | decay:active -->\n  **Two axes resolved.**\n\n"
        "---\n\n_Entries are written by CDF._\n"), encoding="utf-8")
    report = memory_relocate.move_open_threads(ws, apply=True)
    assert report["moved"] == 2
    text = memory.read_text(encoding="utf-8")
    assert "Open Threads" not in text and "Interrupted" not in text
    assert text.rstrip().endswith("_Entries are written by CDF._") and "Stays too" in text
    improvement = (ws / "Projects/SwarmAI/2-understanding/IMPROVEMENT.md").read_text(encoding="utf-8")
    assert "- [model] **streaming freeze + overall slowness — stalls** (reported 1x)\n  Status: diagnosed." in improvement
    assert "- [pitfall] **Frontend reconcile race — guarded**" in improvement and "**Two axes resolved.**" in improvement
    assert "Interrupted" not in improvement
    assert memory_relocate.move_open_threads(ws, apply=True) == {"open_threads": False}


def test_an_evolution_entry_moves_by_its_heading(ws):
    from scripts import memory_relocate
    evo = ws / ".context/EVOLUTION.md"
    evo.write_text("# E\n\n## Corrections Captured\n\n### C070 | 2026-10-29\n- **Correction**: **Narrow one** body.\n"
                   "<!-- cand:cand-e.unit -->\n\n"
                   "### C071 | 2026-10-29\n- **Correction**: kept one\n\n## Competence Learned\n", encoding="utf-8")
    report = memory_relocate.run(ws, apply=True, source=".context/EVOLUTION.md",
                                 entries=[("C070 |", "ddd:lesson", "a pipeline stage")])
    assert report["moved"] == 1
    text = evo.read_text(encoding="utf-8")
    assert "C070" not in text and "### C071 | 2026-10-29\n- **Correction**: kept one" in text
    improvement = (ws / "Projects/SwarmAI/2-understanding/IMPROVEMENT.md").read_text(encoding="utf-8")
    assert "- [correction] **Narrow one** body. (EVOLUTION C070, 2026-10-29) <!-- cand:cand-e.unit -->" in improvement
