"""MEMORY / EVOLUTION injection (design 2026-10-30-cdf-agent-decides D11): the whole file.

Questions: does the selector take no query input; is every unit carried, in file order,
without the metadata comments, whatever the size; is the carried set recorded once per
file version (and again when the file changes), with the file's bytes untouched; do the
loader specs carry no budget for MEMORY / EVOLUTION; does a relation hide nothing.
"""
from __future__ import annotations

import hashlib
import inspect

from core.context_directory_loader import CONTEXT_FILES
from core.ingestion.store import connect_rw
from core.memory_injection import select_context_file


def _memory(n: int) -> str:
    return "# Memory\n\n## Principles\n" + "".join(
        f"- [principle] **Rule {i}** {'body ' * 300}\n  <!-- ref:0 | last:2026-10-20 | decay:active | source:manual -->\n"
        for i in range(n))


def test_the_selector_takes_no_query_and_no_budget():
    names = set(inspect.signature(select_context_file).parameters)
    assert not names & {"query", "user_message", "prompt", "prompt_text", "input", "budget"}


def test_every_unit_in_file_order_recorded_once_per_version(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "app-data"))
    conn = connect_rw()
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text(_memory(30), encoding="utf-8")
    before = path.read_bytes()
    first = select_context_file(path, estimate=len, conn=conn)
    again = select_context_file(path, estimate=len, conn=conn)
    assert first.recomputed and not again.recomputed and first.text == again.text
    positions = [first.text.index(f"**Rule {i}**") for i in range(30)]
    assert positions == sorted(positions) and "ref:0" not in first.text
    assert len(first.anchors) == 30 and first.omitted == 0
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert select_context_file(path, estimate=len, conn=conn).recomputed
    assert conn.execute("SELECT COUNT(*) FROM injection_selection").fetchone()[0] == 2
    assert before == path.read_bytes()[:-1]
    row = conn.execute("SELECT file_hash FROM injection_selection ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    conn.close()


def test_the_loader_carries_no_budget_for_memory_and_evolution():
    specs = {spec.filename: spec for spec in CONTEXT_FILES}
    for name in ("MEMORY.md", "EVOLUTION.md"):
        assert specs[name].selector == "cdf" and specs[name].inject_budget_tokens is None


def test_a_supersedes_relation_never_hides_an_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "app-data"))
    conn = connect_rw()
    path = tmp_path / "SwarmWS/.context/MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Memory\n\n## Guidelines\n- [guideline] **Old** tabs. <!-- cand:o.1 -->\n"
                    "- [guideline] **New** spaces. <!-- cand:n.1 -->\n", encoding="utf-8")
    conn.execute("INSERT INTO ingestion_relations(subject_anchor,predicate,object_anchor,created,inactive) "
                 "VALUES ('.context/MEMORY.md#Guidelines#cand:n.1','supersedes','.context/MEMORY.md#Guidelines#cand:o.1','now',0)")
    conn.commit()
    sel = select_context_file(path, estimate=len, conn=conn)
    conn.close()
    assert "Old" in sel.text and "New" in sel.text
