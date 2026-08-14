"""SSOT entry-formatter tests (run_3cb6b9ae Cycle-2, gap #1).

Validates that format_memory_entry is the single source of truth for a MEMORY
entry's on-disk shape, that it is idempotent (build-time only, never double-wraps),
and that the distillation write doors (_canonicalize_entry, _write_coe_registry)
route through it so NO door emits a bare `- {date}: {text}` (the malformed-entry
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
    # Meta line matches the 4-field metadata parser.
    assert _META_RE.match(meta_line), f"meta line not 4-field: {meta_line!r}"
    assert "source:auto" in meta_line
    assert "last:2026-08-14" in meta_line


def test_format_memory_entry_unknown_type_falls_back():
    from core.ddd_entry_lifecycle import format_memory_entry, DEFAULT_TYPE, _ENTRY_RE
    entry_line, _ = format_memory_entry("not_a_type", "t", "b", "2026-08-14")
    assert _ENTRY_RE.match(entry_line).group(1) == DEFAULT_TYPE


def test_format_memory_entry_no_runid_omits_provenance_comma():
    from core.ddd_entry_lifecycle import format_memory_entry
    entry_line, _ = format_memory_entry("guideline", "t", "b", "2026-08-14")
    assert "(2026-08-14)" in entry_line
    assert ", None" not in entry_line


def test_canonicalize_entry_idempotent_on_canonical_input():
    """An already-canonical `- [type] **title** — ...` entry must pass through
    UNCHANGED (build-time-only / writer≠parser — never double-wrap)."""
    from hooks.distillation_hook import DistillationTriggerHook
    canonical = (
        "- [pitfall] **already canonical** — a durable rule (2026-08-14, run_x)\n"
        "  <!-- ref:0 | last:2026-08-14 | decay:active | source:auto -->"
    )
    out = DistillationTriggerHook._canonicalize_entry(canonical, "Pitfalls")
    assert out == canonical, "canonical entry was rewritten (not idempotent)"


def test_canonicalize_entry_fixes_bare_date_entry():
    """The bug shape `- {date}: {text}` (+ Detail line) must become canonical
    `- [type] **title** — body (date)` + 4-field meta, and NEVER retain the bare form."""
    from hooks.distillation_hook import DistillationTriggerHook
    from core.ddd_entry_lifecycle import _ENTRY_RE, _META_RE
    enriched = (
        "- 2026-08-14: Always verify a claim against source before asserting it\n"
        "  Detail: DailyActivity/2026-08-14.md, commit abc1234."
    )
    out = DistillationTriggerHook._canonicalize_entry(enriched, "Guidelines")
    lines = out.split("\n")
    assert _ENTRY_RE.match(lines[0]), f"first line not canonical: {lines[0]!r}"
    assert _META_RE.match(lines[1]), f"second line not 4-field meta: {lines[1]!r}"
    # No bare `- {date}: {text}` shape anywhere.
    import re
    assert not any(re.match(r"^- \d{4}-\d{2}-\d{2}: ", ln) for ln in lines), (
        "a bare `- {date}: text` line survived canonicalization"
    )
    # Provenance Detail line preserved as a continuation.
    assert any("Detail:" in ln for ln in lines), "provenance Detail line lost"


def test_canonicalize_entry_bold_title_body_not_doubled():
    """Gate-2 BUG-1 (run_3cb6b9ae): a DA lesson is normally `**Title** — body`.
    Canonicalization must split title from body on the em-dash, NOT emit a doubled
    `- [type] **Title** — **Title** — body`. This is the COMMON path the original
    test blind-spotted (it used a no-bold, no-dash sentence)."""
    from hooks.distillation_hook import DistillationTriggerHook
    from core.ddd_entry_lifecycle import _ENTRY_RE
    enriched = (
        "- 2026-08-14: **Two credential chains** — always try IAM role then env var\n"
        "  Detail: DailyActivity/2026-08-14.md, commit abc1234."
    )
    out = DistillationTriggerHook._canonicalize_entry(enriched, "Guidelines")
    first = out.split("\n")[0]
    m = _ENTRY_RE.match(first)
    assert m, f"not canonical: {first!r}"
    assert m.group(2) == "Two credential chains", f"title wrong: {m.group(2)!r}"
    # The title must NOT appear twice, and no stray `**...** —` in the body.
    assert first.count("**Two credential chains**") == 1, (
        f"title DOUBLED (BUG-1): {first!r}"
    )
    assert "— **" not in first, f"stray bold-dash in body (BUG-1 doubling): {first!r}"
    # Body preserved after the dash.
    assert "always try IAM role then env var" in first


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


def test_coe_registry_writes_canonical_entries(tmp_path, monkeypatch):
    """_write_coe_registry must emit `- [pitfall] **topic** — ...` + 4-field meta,
    not the old bare `- {date}: **topic** — status` (no [type], no meta)."""
    from hooks.distillation_hook import DistillationTriggerHook
    from core.ddd_entry_lifecycle import _ENTRY_RE
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_text("# Memory\n\n## COE Registry\n\n## Open Threads\n- one\n")

    hook = DistillationTriggerHook()
    # entries: list[(file_date, signal, topic)]
    entries = [
        ("2026-08-14", "candidate", "streaming stalls at 60% context"),
        ("2026-08-14", "resolution", "streaming stalls at 60% context"),
    ]
    hook._write_coe_registry(memory_path, entries)

    result = memory_path.read_text()
    # Find the COE entry line.
    coe_lines = [ln for ln in result.splitlines()
                 if ln.strip().startswith("- ") and "streaming stalls" in ln]
    assert coe_lines, "COE entry not written"
    assert _ENTRY_RE.match(coe_lines[0]), (
        f"COE entry not canonical (missing [type]/**title**): {coe_lines[0]!r}"
    )
    assert "[pitfall]" in coe_lines[0]
    # 4-field metadata present right after.
    idx = result.splitlines().index(coe_lines[0])
    assert "<!-- ref:0" in result.splitlines()[idx + 1], "COE entry missing 4-field meta"
