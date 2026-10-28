"""core/addressing: the unit table, the map, find and get (knowledge addressing §6.3,
tests 4 and 12). A fixture workspace stands in for SwarmWS (SWARM_WORKSPACE)."""
import os
from pathlib import Path

import pytest

from core import addressing as a
from core.ddd_entry_lifecycle import unit_id


TECH = """# TECH

## Architecture

- [principle] **Quokka rule** — never feed quokkas after midnight.
  <!-- ref:0 | last:none | decay:active | source:manual -->

- [x] done item about quokkas

## Runtime Traps

""" + "\n".join(f"- [pitfall] **Trap {i}** — filler line {i} about wombats." for i in range(400)) + """

### A subsection

- [pitfall] **Deep trap** — the event loop starves when a worker holds the GIL.
"""


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("SWARMWS", str(tmp_path))
    root = tmp_path / "Projects" / "P"
    und = root / "2-understanding"
    und.mkdir(parents=True)
    (und / "TECH.md").write_text(TECH, encoding="utf-8")
    (und / "PRODUCT.md").write_text("# PRODUCT\n\n## Vision\n\nA platypus for everyone.\n", encoding="utf-8")
    (und / "TECH-archive.md").write_text(
        "# TECH archive\n\n## Old\n\n- [decision] **Archived echidna** — retired knowledge.\n", encoding="utf-8")
    (und / "knowledge").mkdir()
    (und / "knowledge" / "deep.md").write_text("# Deep\n\nThe numbat corpus note.\n", encoding="utf-8")
    dc = root / "assets" / "data-source" / "data-contract"
    dc.mkdir(parents=True)
    (dc / "tables.md").write_text("# Tables\n\n- **bilby_table** — one row per bilby.\n", encoding="utf-8")
    (root / "3-gates").mkdir()
    (root / "4-capabilities" / "s_thing").mkdir(parents=True)
    (root / "bindings.yaml").write_text("repos: []\n", encoding="utf-8")
    (root / ".artifacts").mkdir()
    (root / ".artifacts" / "secret.md").write_text("- **run internals** kangaroo\n", encoding="utf-8")
    (und / "knowledge" / ".hidden.md").write_text("- **hidden** kangaroo\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("- **outside secret** kangaroo\n", encoding="utf-8")
    os.symlink(outside, und / "knowledge" / "link.md")
    return tmp_path, root


def test_unit_table_scope_is_docs_archives_corpus_and_data_contract(ws):
    _tmp, root = ws
    table = a.ddd_units("P")
    stores = {u.store for u in table.units}
    assert stores == {"doc", "archive", "corpus", "data-contract"}
    texts = "\n".join(u.text for u in table.units)
    assert "kangaroo" not in texts            # .artifacts, hidden file, symlink out of root


def test_find_reaches_archive_corpus_and_data_contract(ws):
    for word, store in (("echidna", "archive"), ("numbat", "corpus"), ("bilby_table", "data-contract")):
        found = a.find("P", word)
        assert found.units and found.units[0].store == store, word


def test_line_points_at_the_first_line_of_the_unit(ws):
    _tmp, root = ws
    for u in a.ddd_units("P").units:
        lines = Path(u.path).read_text(encoding="utf-8").splitlines()
        assert lines[u.line - 1].strip() == u.text.splitlines()[0].strip()


def test_id_is_unit_id_and_get_finds_it_by_id_and_by_path_line(ws):
    u = a.find("P", "quokkas midnight").units[0]
    assert u.id == unit_id(u.text)
    assert a.get("P", u.id).id == u.id
    assert a.get("P", f"{u.path}:{u.line}").id == u.id
    assert a.get("P", f"2-understanding/TECH.md:{u.line + 1}").id == u.id
    assert a.get("P", "deadbeef") is None
    assert a.get("P", "/etc/passwd:1") is None


def test_done_items_are_not_found_by_default(ws):
    assert all("done item" not in u.text for u in a.find("P", "done item quokkas").units)


def test_find_keeps_units_whole_and_names_the_rest(ws):
    found = a.find("P", "wombats filler", top=8, budget=200)
    assert all(u.text.endswith("wombats.") for u in found.units)
    assert len(found.units) + len(found.omitted) == 8


def test_map_lists_layout_lines_and_subsections(ws):
    _tmp, root = ws
    m = a.ddd_map("P")
    assert str(root) in m and "TECH.md" in m and "TECH-archive.md" in m
    tech = (root / "2-understanding" / "TECH.md").read_text(encoding="utf-8").splitlines()
    line = next(i + 1 for i, l in enumerate(tech) if l == "## Runtime Traps")
    assert f"L{line} ## Runtime Traps" in m
    assert "### A subsection" in m             # Runtime Traps is over SUBHEAD_OVER
    assert "deep.md" in m and "data-source" in m and "s_thing" in m and "bindings.yaml" in m
    assert "knowledge find --project P" in m
    assert "kangaroo" not in m and "link.md" not in m and ".artifacts" not in m


def test_map_budget_takes_whole_lines_and_says_where_the_rest_is(ws):
    m = a.ddd_map("P", budget=900)
    assert len(m) <= 900 + 200
    assert "more lines not shown" in m and "knowledge map --project P" in m


def test_brief_map_is_small(ws):
    m = a.ddd_map("P", size="brief")
    assert "## " not in m and len(m) < 1200 and "knowledge find" in m


def test_no_ddd_and_unknown_project_never_raise(ws, tmp_path):
    tmp, _root = ws
    (tmp / "Projects" / "Empty").mkdir()
    assert a.ddd_units("Empty").units == []
    assert "no DDD documents" in a.ddd_map("Empty")
    assert a.ddd_map("Nope") == "" and a.ddd_units("../etc").units == []
    assert a.find("Nope", "x").units == []


def test_unreadable_doc_is_skipped_alone(ws):
    _tmp, root = ws
    (root / "2-understanding" / "PROJECT.md").write_bytes(b"\xff\xfe\x00 not utf8 \x80")
    table = a.ddd_units("P")
    assert any("PROJECT.md" in s for s in table.skipped)
    assert any(u.doc == "TECH.md" for u in table.units)


def test_old_layout_project_resolves(ws):
    tmp, _root = ws
    old = tmp / "Projects" / "Old"
    old.mkdir()
    (old / "TECH.md").write_text("# T\n\n## S\n\n- **Old layout dingo** — still found.\n", encoding="utf-8")
    assert a.find("Old", "dingo").units[0].doc == "TECH.md"


def test_heading_is_never_part_of_a_unit_and_recall_ids_are_in_the_table(ws):
    _tmp, root = ws
    (root / "2-understanding" / "PROJECT.md").write_text(
        "# PROJECT\n\n## Current Focus\nShip the wallaby feature first.\n\n## Open Items\n- **Wallaby** — open.\n",
        encoding="utf-8")
    table = a.ddd_units("P")
    assert all(not u.text.lstrip().startswith("#") for u in table.units if u.doc == "PROJECT.md")
    ids = {u.id for u in table.units}
    from core.recall_multi import recall_ddd
    from core.run_protocol.run_knowledge import item_id
    for purpose in ("requirement", "design", "review"):
        for h in recall_ddd("wallaby quokkas wombats platypus", "P", purpose):
            if h["kind"] in ("entry", "block"):
                assert item_id(h["content"]) in ids, (purpose, h["content"][:60])
