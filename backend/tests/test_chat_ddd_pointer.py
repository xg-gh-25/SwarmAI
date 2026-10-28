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
