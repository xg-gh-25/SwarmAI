"""An entry's metadata comment carries what aids recall and trust (source, taint), not
the dead operational fields (ref count, last, decay): design decision in the SwarmAI DDD
TECH ("DDD recall-aid tags stay; dead operational fields ... are dropped"), carried out
2026-11-02. The lifecycle reads both shapes (archives keep the old one); every writer
writes the new one; every boundary reader treats either as the entry's own last line."""
from __future__ import annotations

import pytest

NEW = "  <!-- source:S7 | taint:external -->"
OLD = "  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 | taint:external -->"


@pytest.mark.parametrize("meta", [NEW, OLD])
def test_both_shapes_are_an_entrys_metadata(meta):
    from core.ddd_entry_lifecycle import parse_entries, split_entries
    body = ("## Runtime Traps\n\n"
            f"- [pitfall] **First** One. <!-- cand:cand-a.unit -->\n{meta}\n"
            f"- [pitfall] **Second** Two. <!-- cand:cand-b.unit -->\n{meta}\n")
    entries = parse_entries(body)
    assert [(e.title, e.source, e.taint) for e in entries] == [("First", "S7", "external"),
                                                              ("Second", "S7", "external")]
    blocks = split_entries(body.split("\n", 2)[2])
    assert len(blocks) == 2 and all(meta.strip() in b for b in blocks)


def test_a_marker_comment_is_not_metadata():
    from core.ddd_entry_lifecycle import is_meta_line
    assert is_meta_line(NEW) and is_meta_line(OLD)
    assert not is_meta_line("  <!-- cand:cand-a.unit -->")
    assert not is_meta_line("  <!-- channel -->")
    assert not is_meta_line("  <!--  -->")


def test_every_writer_writes_the_new_shape():
    from core.ddd_entry_lifecycle import EntryMetadata, format_memory_entry
    from core.ddd_format import _meta_line, _Block
    _lead, meta = format_memory_entry("guideline", "T", "Body.", "2026-11-02", source="S7")
    assert meta.strip() == "<!-- source:S7 -->"
    assert "ref:" not in EntryMetadata(title="T", entry_type="pitfall", raw_text="x", source="S7").to_comment()
    line = _meta_line(_Block("entry", ["- [pitfall] **T** x (2026-10-01)"], 0), "2026-11-02")
    assert line.strip() == "<!-- source:manual -->"


@pytest.mark.parametrize("store,path", [("DDD", "Projects/P/2-understanding/TECH.md"),
                                        ("STEERING", ".context/STEERING.md")])
def test_the_cdf_writer_writes_the_new_shape(store, path):
    from core.ingestion import writer
    text = writer._render_block({"destination": {"store": store, "relative_path": path,
                                                  "section": "Runtime Traps"}},
                                "cand-a.unit", {"kind": "cognition", "type": "pitfall", "title": "T",
                                                "what": "Body of the entry."})
    assert "ref:" not in text and "decay:" not in text and "last:" not in text
    assert "<!-- source:" in text


def test_withdrawing_a_new_shape_entry_takes_its_metadata_line_too(tmp_path):
    from core.ingestion.governed_edit import _redaction_span
    p = tmp_path / "TECH.md"
    p.write_text("# TECH\n\n## Runtime Traps\n\n"
                 f"- [pitfall] **First** One. <!-- cand:cand-a.unit -->\n{NEW}\n"
                 f"- [pitfall] **Second** Two. <!-- cand:cand-b.unit -->\n{NEW}\n", encoding="utf-8")
    data = p.read_bytes()
    start, end, _ = _redaction_span(p, "TECH.md#Runtime Traps#cand:cand-a.unit")
    removed = data[start:end].decode()
    assert "First" in removed and NEW.strip() in removed and "Second" not in removed

