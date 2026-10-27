"""DDD entry syntax: the `taint` metadata key and the one renderer (`render_ddd_unit`).

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.5, invariant I3: every type
the parser knows, plus a gap, survives render → parse_entries → inject_entry_metadata
with its type, full title, section, source and taint intact, and the metadata stays a
single comment line. Content read from a repo or another outside file is marked
`taint:external` so CDF's baseline can treat it as untrusted (CDF §9).
"""
from __future__ import annotations

import pytest

from core.ddd_entry_lifecycle import (
    VALID_TYPES,
    EntryMetadata,
    inject_entry_metadata,
    parse_entries,
    render_ddd_unit,
)

_LONG_TITLE = ("Minimum Python is 3.10 because the codebase uses structural pattern matching "
               "and PEP 604 unions everywhere, and the CI matrix dropped 3.9 in the last release")


def _entry(etype: str) -> dict:
    return {
        "type": etype,
        "title": _LONG_TITLE,
        "what": "The package requires Python 3.10 or newer.",
        "how": "python_requires >= 3.10 in pyproject.toml",
        "why": "match statements and X | Y unions",
        "rejected": "keeping 3.9 with typing.Union",
        "applicability": "installing on an older interpreter",
        "sources": ["pyproject.toml", "CHANGES.rst#2.2.0"],
    }


def _doc(rendered: str, section: str = "Recent Decisions") -> str:
    return f"# PROJECT\n\n## {section}\n\n{rendered}\n"


@pytest.mark.parametrize("etype", list(VALID_TYPES) + ["gap"])
def test_round_trip_keeps_type_title_section_source_taint(etype):
    text = render_ddd_unit(_entry(etype), date_str="2026-10-27", taint="external")
    entries = parse_entries(_doc(text))
    assert len(entries) == 1
    e = entries[0]
    assert e.entry_type == ("guideline" if etype == "gap" else etype)
    assert e.title == _LONG_TITLE, "the title is never truncated"
    assert e.section == "Recent Decisions"
    assert e.source == "bootstrap"
    assert e.taint == "external"


def test_untainted_entry_has_no_taint():
    text = render_ddd_unit(_entry("decision"), date_str="2026-10-27")
    (e,) = parse_entries(_doc(text))
    assert e.taint == ""
    assert "taint" not in text


def test_inject_keeps_taint_and_one_comment_line():
    content = _doc(render_ddd_unit(_entry("pitfall"), date_str="2026-10-27", taint="external"))
    (e,) = parse_entries(content)
    e.ref_count = 3
    updated = inject_entry_metadata(content, [e])
    assert updated.count("<!--") == 1
    (again,) = parse_entries(updated)
    assert (again.ref_count, again.taint, again.source) == (3, "external", "bootstrap")


def test_body_carries_the_answer_card_fields_and_sources():
    text = render_ddd_unit(_entry("decision"), date_str="2026-10-27")
    line = text.splitlines()[0]
    for part in ("**How:** python_requires", "**Why:** match statements",
                 "**Rejected:** keeping 3.9", "(sources: pyproject.toml; CHANGES.rst#2.2.0)"):
        assert part in line


def test_legacy_comment_is_byte_identical():
    e = EntryMetadata(title="t", source="auto")
    assert e.to_comment() == "  <!-- ref:0 | last:none | decay:active | source:auto -->"


def test_taint_with_supersession_parses():
    content = ("## What Failed\n\n- [pitfall] **old way** body\n"
               "  <!-- ref:1 | last:2026-10-01 | decay:active | source:bootstrap | taint:external"
               " | valid_until:2026-10-20 | superseded_by:new way -->\n")
    (e,) = parse_entries(content)
    assert (e.taint, e.superseded_by, e.source) == ("external", "new way", "bootstrap")
    assert e.to_comment().strip() == content.splitlines()[3].strip()


@pytest.mark.parametrize("bad", [
    {"type": "decision", "title": "has **bold** inside", "what": "x"},
    {"type": "decision", "title": "two\nlines", "what": "x"},
    {"type": "decision", "title": "  ", "what": "x"},
    {"type": "nonsense", "title": "t", "what": "x"},
    {"type": "decision", "title": "a | b", "what": "x"},
    {"type": "decision", "title": "t", "what": "   "},
    {"type": "decision", "title": "t", "what": "hide <!-- this"},
    {"type": "decision", "title": "t", "what": "x", "why": "ends -->"},
])
def test_render_refuses_what_the_parser_cannot_read_back(bad):
    with pytest.raises(ValueError):
        render_ddd_unit(bad, date_str="2026-10-27")


def test_render_refuses_an_unknown_taint():
    with pytest.raises(ValueError):
        render_ddd_unit(_entry("model"), date_str="2026-10-27", taint="user")
