"""Chat's [DDD:<project>] lines say where to read (knowledge addressing §2.6, §6.3 test
10): an entry by its title and file:line, a section by its heading line -- not a bare
"TECH.md § Architecture" naming a 110K section in a file the cwd may not contain."""
from pathlib import Path

from core.session_router import _ddd_hit_line

TECH = "# TECH\n\n## Architecture\n\nintro\n\n- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"


def test_entry_hit_gives_title_and_path_line(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    hit = {"doc": "TECH.md", "section": "Architecture",
           "content": "- [pitfall] **Quokka rule** — never feed quokkas after midnight."}
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, hit)
    path = base / "2-understanding" / "TECH.md"
    assert line.startswith("- ") and "Quokka rule" in line and f"{path}:7 (§ Architecture)" in line


def test_section_hit_gives_heading_line(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, {"doc": "TECH.md", "section": "Architecture"})
    assert line == f"- § Architecture — {base / '2-understanding' / 'TECH.md'}:3 (§ Architecture)"


def test_unknown_file_falls_back_to_doc_and_section():
    assert _ddd_hit_line("P", None, {}, {"doc": "TECH.md", "section": "X"}) == "- **TECH.md** § X"


def test_whitespace_or_comment_first_content_never_crashes(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(TECH, encoding="utf-8")
    assert "§ Architecture" in _ddd_hit_line("P", base, {"TECH.md": TECH},
                                             {"doc": "TECH.md", "section": "Architecture", "content": "  \n "})
    line = _ddd_hit_line("P", base, {"TECH.md": TECH}, {"doc": "TECH.md", "section": "Architecture",
                                                        "content": "<!-- ref:0 -->\nplain words"})
    assert "<!--" not in line


def test_locator_finds_a_repeated_line_in_its_own_section():
    from core.recall_multi import _locator
    text = ("# T\n\n## A\n\n```\nalpha code\n```\n\n---\n\n## B\n\n```\nbeta code\n```\n\n---\n\n"
            "- **dup** — same\n- **dup** — same\n\nlast block\n")
    loc = _locator("T.md", Path("/p/T.md"), text)
    assert loc("B", "```\nbeta code\n```") == "/p/T.md:13 (§ B)"
    assert loc("B", "---") == "/p/T.md:17 (§ B)"          # not section A's ---
    assert loc("B", "") == "/p/T.md:11 (§ B)"              # nothing to match: the heading


BIG = ("# TECH\n\n## Architecture\n\nintro paragraph\n\n"
       "- [decision] **Wombat cache** — the wombat cache is flushed hourly.\n"
       "  <!-- ref:0 | last:none | decay:active -->\n"
       "- [pitfall] **Quokka rule** — never feed quokkas after midnight.\n"
       "- plain bullet about platypus burrows\n")


def _ddd(tmp_path):
    base = tmp_path / "Projects" / "P"
    (base / "2-understanding").mkdir(parents=True)
    (base / "2-understanding" / "TECH.md").write_text(BIG, encoding="utf-8")
    return base


def test_a_section_hit_points_at_its_best_entry(tmp_path):
    """Recall returns most chat hits as a whole section (no content); the pointer named
    the section heading -- in SwarmAI a 100K section (E2E after deploy, §2.6)."""
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": BIG}, "quokkas midnight")
    line = _ddd_hit_line("P", base, {"TECH.md": BIG}, hit)
    assert line == f"- Quokka rule — {base / '2-understanding' / 'TECH.md'}:9 (§ Architecture)"


def test_a_plain_bullet_title_has_no_doubled_marker(tmp_path):
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": BIG}, "platypus burrows")
    assert _ddd_hit_line("P", base, {"TECH.md": BIG}, hit).startswith("- plain bullet about platypus")


def test_no_matching_entry_keeps_the_heading_pointer(tmp_path):
    from core.session_router import _best_entry_of_section
    base = _ddd(tmp_path)
    hit = {"doc": "TECH.md", "section": "Architecture"}
    assert _best_entry_of_section(hit, base, {"TECH.md": BIG}, "zebra") is hit
    assert _best_entry_of_section({"doc": "x.md", "section": "(whole file)"}, base, {}, "q")["doc"] == "x.md"


def test_the_injected_block_names_entries_not_sections(tmp_path, monkeypatch):
    """Through _inject_ddd_for_active_project with a section-level recall hit."""
    import types
    from core import recall_multi, session_router as sr
    base = _ddd(tmp_path)
    monkeypatch.setattr(recall_multi, "recall_all", lambda *a, **k: types.SimpleNamespace(
        buckets={"ddd": [{"doc": "TECH.md", "section": "Architecture", "score": 1.0}]}))
    monkeypatch.setattr(recall_multi, "_load_ddd_docs_text", lambda p: (base, {"TECH.md": BIG}))
    out: list = []
    sr._inject_ddd_for_active_project(types.SimpleNamespace(system_prompt=""),
                                      "why never feed quokkas after midnight", ("P", "signal3_keyword"),
                                      deliver=out.append)
    block = "".join(out)
    assert "Quokka rule" in block and "TECH.md:9 (§ Architecture)" in block
    assert "- § Architecture" not in block


def test_the_entry_is_taken_from_the_hit_section_only(tmp_path):
    from core.session_router import _best_entry_of_section
    text = BIG + "\n## Other\n\n- quokkas midnight quokkas midnight quokkas\n"
    base = _ddd(tmp_path)
    (base / "2-understanding" / "TECH.md").write_text(text, encoding="utf-8")
    hit = _best_entry_of_section({"doc": "TECH.md", "section": "Architecture"}, base,
                                 {"TECH.md": text}, "quokkas midnight")
    assert "Quokka rule" in hit["content"]
