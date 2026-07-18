"""Tests for DDD entry usage tracking (ddd_usage.py).

WHAT IS TESTED
--------------
The DDD access-decay hit-log: recall records which IMPROVEMENT.md entries it
actually surfaced (`record_ddd_hit`), and the decay engine consumes that log to
keep genuinely-used lessons alive (`load_ddd_usage`). Before this, DDD entries
decayed on AGE ALONE — a lesson referenced every session still rotted at 60d.

KEY PROPERTY (the Gate-1 Blocker-A regression, most important test)
-------------------------------------------------------------------
`entry_anchor_text()` MUST produce the SAME anchor from BOTH sides:
  - WRITE side feeds recall's `entry-hit['content']` — which INCLUDES the
    trailing `<!-- ref:N | last:... | decay:... -->` metadata line.
  - READ side feeds `EntryMetadata.raw_text` — which EXCLUDES that metadata line
    (parse_entries strips it).
If the normalizer doesn't strip metadata + the trailing `(date, run_id)` stamp,
the two anchors differ, the usage lookup misses every time, and the whole
feature is a SILENT no-op. test_anchor_equal_across_metadata is the guard.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


# ── AC1 (tracer bullet): shared anchor is metadata-invariant ────────────────
def test_anchor_equal_across_metadata():
    """The #1 regression: content (with <!-- -->) and raw_text (without) must
    normalize to the SAME anchor. Mutation: a naive `text[:80]` fails this."""
    from core.ddd_usage import entry_anchor_text

    raw_text = (
        "- [guideline] Verify-first is a TWO-WAY gate on a fix-all sweep — a bug "
        "report can be WRONG-FRAME just as often as real. (2026-07-18, run_2d3417d9)"
    )
    content_with_meta = (
        raw_text + "\n  <!-- ref:3 | last:2026-07-18 | decay:active | source:manual -->"
    )

    assert entry_anchor_text(content_with_meta) == entry_anchor_text(raw_text)
    # And it must be non-empty (a real anchor, not "" collapsing everything equal)
    assert entry_anchor_text(raw_text) != ""


def test_anchor_ignores_trailing_date_run_stamp():
    """Two entries identical except the (date, run_id) stamp anchor the same —
    the stamp is not part of the entry's identity."""
    from core.ddd_usage import entry_anchor_text

    a = "- [pitfall] Python except-clause ORDER is a data-loss trap (2026-07-18, run_aaaa1111)"
    b = "- [pitfall] Python except-clause ORDER is a data-loss trap (2026-07-01, run_bbbb2222)"
    assert entry_anchor_text(a) == entry_anchor_text(b)


def test_anchor_distinguishes_different_entries():
    """Different entries must NOT collapse to the same anchor (no false bumps)."""
    from core.ddd_usage import entry_anchor_text

    a = "- [guideline] A fail-closed generation gate surfaces the real coverage gap"
    b = "- [pitfall] A delete-the-corrupt-DB recovery must also delete the -wal/-shm sidecars"
    assert entry_anchor_text(a) != entry_anchor_text(b)


# ── AC2: recall hit persists to .ddd-usage.json ─────────────────────────────
def test_record_and_load_roundtrip(tmp_path, monkeypatch):
    from core import ddd_usage

    monkeypatch.setattr(ddd_usage, "get_projects_dir", lambda: tmp_path)
    proj = tmp_path / "SwarmAI"
    proj.mkdir()

    hit_date = date(2026, 7, 18)
    ddd_usage.record_ddd_hit("SwarmAI", "IMPROVEMENT.md", "What Failed",
                             "some entry anchor text", hit_date)

    usage = ddd_usage.load_ddd_usage("SwarmAI")
    key = "IMPROVEMENT.md|What Failed|some entry anchor text"
    assert key in usage
    assert usage[key] == hit_date


# ── AC3: bump saves a recently-hit old entry from dormancy ──────────────────
def test_bump_saves_recently_hit_old_entry():
    """An entry old enough to go dormant, but recently recall-hit, must NOT be
    marked dormant once its last_referenced is bumped to the hit date."""
    from core.ddd_entry_lifecycle import EntryMetadata, assess_decay

    today = date(2026, 7, 18)
    old = today - timedelta(days=90)   # past the 60d dormant threshold
    recent_hit = today - timedelta(days=5)

    entry = EntryMetadata(
        title="Verify-first two-way gate", entry_type="guideline",
        created_date=old, last_referenced=None, section="What Failed",
        raw_text="- [guideline] Verify-first two-way gate (2026-04-19, run_x)",
    )
    # Without a bump: it decays (control).
    transitions_before = assess_decay([entry], today)
    assert any(t.new_state == "dormant" for t in transitions_before)

    # Apply the usage bump (what Channel-8 does).
    entry.decay_state = "active"  # reset control mutation
    entry.last_referenced = recent_hit
    transitions_after = assess_decay([entry], today)
    assert not any(t.new_state == "dormant" for t in transitions_after)


# ── AC4: never-hit old entries still archive (decay not broken) ─────────────
def test_never_hit_old_entry_still_decays():
    from core.ddd_entry_lifecycle import EntryMetadata, assess_decay

    today = date(2026, 7, 18)
    ancient = today - timedelta(days=200)  # past archive threshold
    entry = EntryMetadata(
        title="stale lesson", entry_type="guideline",
        created_date=ancient, last_referenced=None, section="What Failed",
        raw_text="- [guideline] stale lesson (2026-01-01, run_x)",
    )
    transitions = assess_decay([entry], today)
    assert any(t.new_state in ("dormant", "archived") for t in transitions)


# ── AC5: cap evicts oldest, best-effort never raises ────────────────────────
def test_cap_evicts_oldest(tmp_path, monkeypatch):
    from core import ddd_usage

    monkeypatch.setattr(ddd_usage, "get_projects_dir", lambda: tmp_path)
    (tmp_path / "SwarmAI").mkdir()

    base = date(2026, 1, 1)
    # Write CAP + 10 anchors, each with an increasing date.
    n = ddd_usage._USAGE_CAP + 10
    for i in range(n):
        ddd_usage.record_ddd_hit("SwarmAI", "IMPROVEMENT.md", "S",
                                 f"anchor number {i}", base + timedelta(days=i))
    usage = ddd_usage.load_ddd_usage("SwarmAI")
    assert len(usage) <= ddd_usage._USAGE_CAP
    # The OLDEST (anchor number 0) must have been evicted; the newest kept.
    assert "IMPROVEMENT.md|S|anchor number 0" not in usage
    assert f"IMPROVEMENT.md|S|anchor number {n - 1}" in usage


def test_record_best_effort_never_raises(tmp_path, monkeypatch):
    """A write to an unwritable location must be swallowed — recall must never
    be blocked by the usage log (recall_multi.py:24 principle)."""
    from core import ddd_usage

    # Point at a projects dir whose project subdir does not exist and cannot be
    # created (parent is a file, not a dir) → any write attempt errors internally.
    bogus = tmp_path / "not_a_dir"
    bogus.write_text("i am a file, not a directory")
    monkeypatch.setattr(ddd_usage, "get_projects_dir", lambda: bogus)

    # Must NOT raise.
    ddd_usage.record_ddd_hit("SwarmAI", "IMPROVEMENT.md", "S", "x", date(2026, 7, 18))
    # And load from a broken location returns empty, not crash.
    assert ddd_usage.load_ddd_usage("SwarmAI") == {}
