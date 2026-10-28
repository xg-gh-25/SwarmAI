"""unit_id: one identity for a recall unit, stable across metadata rewrites.

Knowledge addressing design §2.1, §6.3 test 1. The pipeline used to hash an entry's raw
text, and a unit includes the metadata comment under the entry, so each decay pass that
rewrote ``ref:`` / ``last:`` gave the entry a new id.
"""
from core.ddd_entry_lifecycle import split_blocks, split_blocks_at, unit_id
from core.run_protocol import run_knowledge as rk

ENTRY = "- [pitfall] **Quokka rule**: never feed quokkas after midnight. (2026-10-01, run_abc)"


def test_metadata_rewrite_keeps_the_id():
    before = ENTRY + "\n  <!-- ref:0 | last:none | decay:active | source:manual -->"
    after = ENTRY + "\n  <!-- ref:3 | last:2026-10-28 | decay:dormant | source:manual -->"
    assert unit_id(before) == unit_id(after) == unit_id(ENTRY)


def test_maturity_marker_does_not_change_the_id():
    marked = ENTRY + "\n<!-- maturity: growing | sources: 2 | verified: true -->"
    assert unit_id(marked) == unit_id(ENTRY)


def test_two_writer_formats_of_one_lesson_share_an_id():
    cultivated = "- [pitfall] **Quokka rule** — never feed quokkas after midnight (2026-10-01, run_x, judged)"
    writeback = "- **2026-10-02** (session 1a2b3c4d): Quokka rule — never feed quokkas after midnight"
    assert unit_id(cultivated) == unit_id(writeback)


def test_a_real_edit_changes_the_id():
    assert unit_id(ENTRY) != unit_id(ENTRY.replace("midnight", "noon"))


def test_blocks_ignore_whitespace_and_case_only():
    assert unit_id("A table\n| x | y |") == unit_id("a   table  \n| X | Y |")
    assert unit_id("A table") != unit_id("Another table")


def test_run_knowledge_item_id_is_unit_id():
    assert rk.item_id(ENTRY + "\n<!-- ref:9 | last:x | decay:active -->") == unit_id(ENTRY)


def test_split_blocks_at_gives_each_unit_its_first_line():
    body = ("intro paragraph\nstill intro\n\n"
            "- [guideline] **One** first\n  <!-- ref:0 | last:none | decay:active -->\n\n"
            "```\ncode\n\n- not an entry\n```\n\n"
            "- **Two** second\n")
    lines = body.splitlines()
    units = split_blocks_at(body)
    assert [(k, t) for k, t, _ in units] == split_blocks(body)
    for _kind, text, line in units:
        assert lines[line].strip() == text.splitlines()[0].strip()
    assert [k for k, _, _ in units] == ["block", "entry", "block", "entry"]
