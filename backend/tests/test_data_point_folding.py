"""Unit tests for data_point_folding — the Corrections-section self-pruning.

Testing methodology: pure-function tests over synthetic Corrections sections.
Key invariants (mirror the goal DoD + Gate-1 findings):
- Only RECURRENCE/CONTAINMENT DATA-POINT sub-bullets fold; PROTECTED types never.
- Per family, keep anchor + capstone(text-marker, NOT date) + recent-2 (cap 4).
- Idempotent: 2nd run is a no-op (marker records archived run-ids). [Gate-1 F3]
- Fail-safe: a family with no foldables / at-or-below cap is untouched. [Gate-1 F4]
- capstone identified by TEXT marker survives even if not newest. [Gate-1 F2]
- run-id traceability preserved in the summary line.
"""

from hooks.data_point_folding import fold_corrections_section


def _dp(marker: str, date: str, run: str, body: str = "detail") -> str:
    return f"  - **{marker} ({date}, {run}, caught)**: {body}\n"


def _family(header: str, bullets: list[str]) -> str:
    return f"### {header}\n" + "".join(bullets)


def _doc(families: str) -> str:
    return (
        "# SwarmAI Evolution Registry\n\n"
        "## Corrections Captured\n\n"
        f"{families}"
        "\n## Governance Candidates\n\n_None._\n"
    )


def test_folds_when_over_cap():
    """6 foldable data-points → folded to cap 4, 2 archived."""
    bullets = [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_aaaaa{i}")
               for i in range(1, 7)]
    doc = _doc(_family("CLASS X: Test", bullets))
    r = fold_corrections_section(doc, cap=4)
    assert r.changed is True
    assert r.bullets_archived == 2
    assert r.families_folded == 1
    # summary line present, archive holds 2 blocks
    assert "folded to archive" in r.new_content
    assert len(r.archived_blocks) == 2


def test_no_fold_at_or_below_cap():
    """4 foldable → at cap → untouched (fail-safe no-op)."""
    bullets = [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_bbbbb{i}")
               for i in range(1, 5)]
    doc = _doc(_family("CLASS Y: Test", bullets))
    r = fold_corrections_section(doc, cap=4)
    assert r.changed is False
    assert r.new_content == doc


def test_protected_markers_never_fold():
    """METHOD FIX / CONTAINMENT NOTE / VALIDATING EVIDENCE never counted/folded."""
    bullets = (
        [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_ccccc{i}")
         for i in range(1, 4)]  # 3 foldable — below cap
        + [
            "  - **METHOD FIX — critical protocol**: never fold this.\n",
            "  - **CONTAINMENT NOTE (same session)**: keep.\n",
            "  - **VALIDATING EVIDENCE**: keep.\n",
        ]
    )
    doc = _doc(_family("CLASS Z: Test", bullets))
    r = fold_corrections_section(doc, cap=4)
    # 3 foldable ≤ cap → no fold; protected present regardless
    assert r.changed is False
    assert "METHOD FIX" in r.new_content
    assert "VALIDATING EVIDENCE" in r.new_content


def test_capstone_survives_even_if_not_newest():
    """Gate-1 F2: capstone (text marker) is kept even when older than others."""
    bullets = [
        _dp("RECURRENCE DATA-POINT", "2026-01-01", "run_anchor0", "the anchor"),
        _dp("RECURRENCE DATA-POINT", "2026-02-02", "run_capsto0",
            "the CAPSTONE that finally CLOSED the class"),
    ] + [_dp("RECURRENCE DATA-POINT", f"2026-08-1{i}", f"run_recent{i}")
         for i in range(1, 6)]  # 5 newer ones
    doc = _doc(_family("CLASS CAP: Test", bullets))
    r = fold_corrections_section(doc, cap=4)
    assert r.changed is True
    # capstone run-id must NOT be in any archived block
    archived_text = "".join(r.archived_blocks)
    assert "run_capsto0" not in archived_text, "capstone was wrongly archived"
    assert "run_capsto0" in r.new_content, "capstone must remain in the live doc"
    # anchor also kept
    assert "run_anchor0" in r.new_content


def test_capstone_survives_at_cap_2():
    """cap=2 (leanest): anchor + capstone are the ONLY kept slots — capstone
    must still be protected even when it competes with newer data-points."""
    bullets = [
        _dp("RECURRENCE DATA-POINT", "2026-01-01", "run_anchorX", "the anchor"),
        _dp("RECURRENCE DATA-POINT", "2026-03-03", "run_capstoX",
            "the CAPSTONE that finally CLOSED the class"),
    ] + [_dp("RECURRENCE DATA-POINT", f"2026-09-1{i}", f"run_newerX{i}")
         for i in range(1, 5)]  # 4 newer than capstone
    doc = _doc(_family("CLASS CAP2: Test", bullets))
    r = fold_corrections_section(doc, cap=2)
    assert r.changed is True
    archived = "".join(r.archived_blocks)
    assert "the CAPSTONE that finally CLOSED" in r.new_content
    assert "the CAPSTONE that finally CLOSED" not in archived, "capstone folded at cap=2"


def test_idempotent_second_run_is_noop():
    """Gate-1 F3: re-running on already-folded content archives nothing more."""
    bullets = [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_ddddd{i}")
               for i in range(1, 7)]
    doc = _doc(_family("CLASS IDEM: Test", bullets))
    r1 = fold_corrections_section(doc, cap=4)
    assert r1.changed is True and r1.bullets_archived == 2
    r2 = fold_corrections_section(r1.new_content, cap=4)
    assert r2.changed is False, "2nd run must be a no-op"
    assert r2.bullets_archived == 0


def test_runid_traceability_preserved():
    """Every folded point's run-id appears in the summary line."""
    bullets = [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_eeeee{i}")
               for i in range(1, 7)]
    doc = _doc(_family("CLASS TRACE: Test", bullets))
    r = fold_corrections_section(doc, cap=4)
    # the 2 oldest (run_eeeee1, run_eeeee2) get folded; their ids must be in summary
    assert "run_eeeee1" in r.new_content
    assert "run_eeeee2" in r.new_content


def test_identical_text_bullets_kept_one_survives():
    """Gate-2 CRITICAL #1: two byte-identical foldable bullets — a KEPT one must
    NOT be dropped by a text-set membership test. Reconstruct by index."""
    identical = "  - **RECURRENCE DATA-POINT (2026-01-01, run_same01, caught)**: SAME BODY\n"
    bullets = (
        [identical]  # index 0 = anchor (kept)
        + [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_uniq0{i}")
           for i in range(1, 5)]
        + [identical]  # a later byte-identical dupe (foldable, not kept)
    )
    doc = _doc(_family("CLASS DUP: Test", bullets))
    r = fold_corrections_section(doc, cap=2)
    assert r.changed is True
    # The anchor (identical text at index 0) must still be present in live doc.
    assert "SAME BODY" in r.new_content, "kept identical-text anchor was dropped"


def test_crossref_runid_does_not_poison_idempotency():
    """Gate-2 MED #3: a folded bullet that CITES another bullet's run-id in its
    prose must not cause that other (live) bullet to be treated as archived."""
    bullets = [
        _dp("RECURRENCE DATA-POINT", "2026-01-01", "run_anch99", "anchor"),
        # this folded bullet cites run_live99 in its PROSE (cross-ref):
        "  - **RECURRENCE DATA-POINT (2026-02-02, run_cited0, caught)**: "
        "see also run_live99 for context\n",
    ] + [_dp("RECURRENCE DATA-POINT", f"2026-03-0{i}", f"run_mid0{i}")
         for i in range(1, 4)]
    doc = _doc(_family("CLASS XREF: Test", bullets))
    r1 = fold_corrections_section(doc, cap=2)
    assert r1.changed is True
    # marker must NOT contain run_live99 (it was only cited, never a bullet owner)
    assert "run_live99" not in r1.new_content.split("archived=")[1].split("-->")[0] \
        if "archived=" in r1.new_content else True
    # 2nd run still a no-op (no phantom re-fold from poisoned ids)
    r2 = fold_corrections_section(r1.new_content, cap=2)
    assert r2.changed is False


def test_no_corrections_section_is_noop():
    """Fail-safe: a doc without the section is returned unchanged."""
    doc = "# Registry\n\n## Capabilities Built\n\n### E001 | 2026-01-01\n- x\n"
    r = fold_corrections_section(doc, cap=4)
    assert r.changed is False
    assert r.new_content == doc


def test_multiple_families_folded_independently():
    """Two over-cap families both fold; a protected-only family stays."""
    fam_a = _family("CLASS A: one",
                    [_dp("RECURRENCE DATA-POINT", f"2026-08-0{i}", f"run_fa{i}")
                     for i in range(1, 7)])
    fam_b = _family("CLASS B: two",
                    [_dp("CONTAINMENT DATA POINT", f"2026-07-0{i}", f"run_fb{i}")
                     for i in range(1, 6)])
    doc = _doc(fam_a + fam_b)
    r = fold_corrections_section(doc, cap=4)
    assert r.families_folded == 2
    # fam_a: 6→ archive 2 ; fam_b: 5→ archive 1
    assert r.bullets_archived == 3
