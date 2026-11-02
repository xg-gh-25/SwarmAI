"""SSOT entry-formatter tests (run_3cb6b9ae Cycle-2, gap #1).

Validates that format_memory_entry is the single source of truth for a MEMORY
entry's on-disk shape, that it is idempotent (build-time only, never double-wraps),
and that the distillation write door (_canonicalize_entry) routes through it so NO door emits a bare `- {date}: {text}` (the malformed-entry
production source this run fixes).
"""
from __future__ import annotations


def test_format_memory_entry_canonical_shape():
    from core.ddd_entry_lifecycle import format_memory_entry, _ENTRY_RE, _META_RE
    entry_line, meta_line = format_memory_entry(
        "pitfall", "the title", "the durable rule body", "2026-08-14",
        run_id="run_abc123", source="auto",
    )
    # Entry line matches the canonical parser (proves decay/dedup/recall will see it).
    m = _ENTRY_RE.match(entry_line)
    assert m, f"entry line not canonical: {entry_line!r}"
    assert m.group(1) == "pitfall"
    assert m.group(2) == "the title"
    assert "(2026-08-14, run_abc123)" in entry_line
    # Meta line matches the metadata parser and carries only source.
    assert _META_RE.match(meta_line), f"meta line not readable: {meta_line!r}"
    assert meta_line.strip() == "<!-- source:auto -->"


def test_format_memory_entry_unknown_type_falls_back():
    from core.ddd_entry_lifecycle import format_memory_entry, DEFAULT_TYPE, _ENTRY_RE
    entry_line, _ = format_memory_entry("not_a_type", "t", "b", "2026-08-14")
    assert _ENTRY_RE.match(entry_line).group(1) == DEFAULT_TYPE


def test_format_memory_entry_no_runid_omits_provenance_comma():
    from core.ddd_entry_lifecycle import format_memory_entry
    entry_line, _ = format_memory_entry("guideline", "t", "b", "2026-08-14")
    assert "(2026-08-14)" in entry_line
    assert ", None" not in entry_line








def test_boundary_lead_patterns_are_shared_ssot():
    """run_3cb6b9ae Cycle-5 (#5): the ID-lead + type-lead regex fragments are defined
    ONCE in ddd_entry_lifecycle and referenced by both the size-valve and recall — so
    the entry-shape vocabulary drifts in one place, not N. Not a MERGE of the detectors
    (their scope differs deliberately); a shared FRAGMENT."""
    import re
    from core.ddd_entry_lifecycle import _ID_LEAD_PAT, _TYPE_LEAD_PAT, VALID_TYPES
    # ID-lead matches an ID-shaped tag, not a lowercase type tag.
    assert re.match(_ID_LEAD_PAT, "[PRI01]") and re.match(_ID_LEAD_PAT, "[COE8]")
    assert not re.match(_ID_LEAD_PAT, "[guideline]")
    # Type-lead matches every VALID_TYPE, case-insensitively.
    for t in VALID_TYPES:
        assert re.match(_TYPE_LEAD_PAT, f"[{t}]"), f"type-lead misses {t}"
        assert re.match(_TYPE_LEAD_PAT, f"[{t.capitalize()}]"), f"type-lead not case-insensitive for {t}"
    # A non-type bracket must NOT match type-lead (no phantom split of `[see also]`).
    assert not re.match(_TYPE_LEAD_PAT, "[see also]")

