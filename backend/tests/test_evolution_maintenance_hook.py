"""Unit tests for the EvolutionMaintenanceHook.

Tests entry parsing, deprecation logic, pruning logic, and changelog
writing against synthetic EVOLUTION.md content.

Testing methodology: unit tests with temp files.
Key invariants:
- Active entries idle >30 days with 0 usage → deprecated
- Deprecated entries with 0 usage → pruned (removed from file)
- All actions logged to EVOLUTION_CHANGELOG.jsonl
- Entries with usage_count > 0 are never deprecated or pruned
- File locking is used for prune operations
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hooks.evolution_maintenance_hook import (
    EvolutionMaintenanceHook,
    _parse_entries,
    _get_field,
)
from core.session_hooks import HookContext


# Helper: date string N days ago
def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


# Synthetic EVOLUTION.md content
def _make_evolution_md(entries: list[dict]) -> str:
    """Build a minimal EVOLUTION.md with given entries in Capabilities Built."""
    lines = [
        "# SwarmAI Evolution Registry\n",
        "## Capabilities Built\n",
    ]
    for e in entries:
        lines.append(
            f"### {e['id']} | reactive | skill | {e['date']}\n"
            f"- **Name**: {e.get('name', 'Test')}\n"
            f"- **Description**: {e.get('desc', 'Test entry')}\n"
            f"- **Usage Count**: {e.get('usage', 0)}\n"
            f"- **Status**: {e.get('status', 'active')}\n\n"
        )
    lines.append("## Optimizations Learned\n\n_None._\n")
    lines.append("## Corrections Captured\n\n_None._\n")
    lines.append("## Competence Learned\n\n_None._\n")
    lines.append("## Failed Evolutions\n\n_None._\n")
    return "".join(lines)


def _make_context(tmp: Path) -> HookContext:
    return HookContext(
        session_id="test-session",
        agent_id="default",
        message_count=10,
        session_start_time="2026-03-01T00:00:00Z",
        session_title="Test",
    )


class TestParseEntries:
    """Tests for _parse_entries helper."""

    def test_parses_single_entry(self):
        content = _make_evolution_md([
            {"id": "E001", "date": "2026-01-01", "usage": 3, "status": "active"},
        ])
        entries = _parse_entries(content, "Capabilities Built")
        assert len(entries) == 1
        assert entries[0]["id"] == "E001"
        assert entries[0]["usage_count"] == 3
        assert entries[0]["status"] == "active"

    def test_parses_multiple_entries(self):
        content = _make_evolution_md([
            {"id": "E001", "date": "2026-01-01"},
            {"id": "E002", "date": "2026-02-01"},
        ])
        entries = _parse_entries(content, "Capabilities Built")
        assert len(entries) == 2

    def test_empty_section_returns_empty(self):
        content = "# Title\n\n## Capabilities Built\n\n_None._\n\n## Other\n"
        entries = _parse_entries(content, "Capabilities Built")
        assert entries == []

    def test_missing_section_returns_empty(self):
        entries = _parse_entries("# Just a title\n", "Capabilities Built")
        assert entries == []


class TestGetField:
    """Tests for _get_field helper."""

    def test_extracts_field(self):
        block = "### E001\n- **Name**: Test\n- **Usage Count**: 5\n"
        assert _get_field(block, "Usage Count") == "5"
        assert _get_field(block, "Name") == "Test"

    def test_missing_field_returns_none(self):
        block = "### E001\n- **Name**: Test\n"
        assert _get_field(block, "Usage Count") is None


def _write_recent_evolution_state(ctx_dir: Path) -> None:
    """Write a recent .evolution_last_run.

    NOTE (run_6ac3fc0b): the hook no longer runs the evolution cycle
    (_maybe_run_evolution was removed — the cycle is now triggered solely by
    the scheduled evolution-cycle job), so this is no longer required to keep
    tests fast. Retained as a harmless no-op precondition for the governance
    tests below; the hook ignores this file entirely now.
    """
    state_file = ctx_dir / ".evolution_last_run"
    state_file.write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
    )


class TestEvolutionMaintenanceHook:
    """Integration tests for the full hook lifecycle."""

    @pytest.mark.asyncio
    async def test_deprecates_idle_entry(self, tmp_path):
        """Active entry idle >30 days with 0 usage → deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(45), "usage": 0, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "deprecated" in content

        log_lines = changelog.read_text().strip().split("\n")
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry["action"] == "deprecate"
        assert entry["id"] == "E001"

    @pytest.mark.asyncio
    async def test_skips_entry_with_usage(self, tmp_path):
        """Active entry with usage_count > 0 is never deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(45), "usage": 5, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "deprecated" not in content.split("## Optimizations")[0]
        assert changelog.read_text().strip() == ""

    @pytest.mark.asyncio
    async def test_prunes_deprecated_entry(self, tmp_path):
        """Deprecated entry with 0 usage and old date → removed."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(60), "usage": 0, "status": "deprecated"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "E001" not in content

        log_lines = changelog.read_text().strip().split("\n")
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry["action"] == "prune"

    @pytest.mark.asyncio
    async def test_no_context_dir_is_noop(self, tmp_path):
        """Missing .context directory → silent no-op."""
        hook = EvolutionMaintenanceHook(context_dir=tmp_path / "nonexistent")
        await hook.execute(_make_context(tmp_path))
        # No crash = pass

    @pytest.mark.asyncio
    async def test_recent_entry_untouched(self, tmp_path):
        """Entry created 5 days ago → not deprecated."""
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        _write_recent_evolution_state(ctx_dir)
        evo = ctx_dir / "EVOLUTION.md"
        changelog = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        changelog.write_text("")

        evo.write_text(_make_evolution_md([
            {"id": "E001", "date": _days_ago(5), "usage": 0, "status": "active"},
        ]))

        hook = EvolutionMaintenanceHook(context_dir=ctx_dir, deprecation_days=30)
        await hook.execute(_make_context(tmp_path))

        content = evo.read_text()
        assert "active" in content.split("## Optimizations")[0]
        assert changelog.read_text().strip() == ""


class TestEvolutionWeeklyTriggerRemoved:
    """The weekly evolution CYCLE trigger was REMOVED from this hook (run_6ac3fc0b).

    The old TestEvolutionWeeklyTrigger class (test_evolution_runs_after_7_days,
    test_evolution_skips_if_recent, test_evolution_runs_if_no_state_file,
    test_evolution_with_valid_skills_dir) drove hook._maybe_run_evolution — a
    method that no longer exists. A ~5-min cycle on the 180s-budget session-close
    hook timed out before advancing .evolution_last_run and re-triggered every
    session. The cycle is now triggered SOLELY by the scheduled `evolution-cycle`
    job. Decoupling behavior is covered by tests/test_evolution_cycle_decoupling.py:
      - TestHookDoesNotRunEvolutionCycle (hook no longer runs the cycle)
      - TestScheduledJobTimeoutHeadroom (scheduled job can actually finish it)
    """

    def test_maybe_run_evolution_is_gone(self):
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        assert not hasattr(EvolutionMaintenanceHook, "_maybe_run_evolution")


class TestFoldWritesToMonthlyShard:
    """Sub-change 2: fold archives to a MONTHLY shard .context/EVOLUTION-archive-{YYYY-MM}.md
    (mirrors the proven MEMORY pattern context_health_hook:2126), NOT the fixed
    legacy EVOLUTION-archive.md. Legacy file is left untouched (pre-2026-08 history)."""

    def _evolution_with_foldable_family(self) -> str:
        # A Corrections family with 3 foldable DATA-POINTs (cap=2 → 1 archived).
        return (
            "# SwarmAI Evolution Registry\n\n"
            "## Corrections Captured\n\n"
            "### CLASS X: Test Family\n"
            "- **RECURRENCE DATA-POINT (2026-08-01, run_a)**: first anchor point one.\n"
            "- **RECURRENCE DATA-POINT (2026-08-02, run_b)**: second point two.\n"
            "- **RECURRENCE DATA-POINT (2026-08-03, run_c)**: third point three.\n"
        )

    def test_shard_helper_returns_monthly_name(self):
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        from datetime import date
        hook = EvolutionMaintenanceHook(context_dir=Path("/tmp/x"))
        shard = hook._evolution_archive_shard(date(2026, 8, 14))
        assert shard == "EVOLUTION-archive-2026-08.md"

    def test_fold_writes_monthly_shard_not_legacy(self, tmp_path):
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        ctx = tmp_path / ".context"
        ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        evo.write_text(self._evolution_with_foldable_family())
        changelog = ctx / "EVOLUTION_CHANGELOG.jsonl"
        hook = EvolutionMaintenanceHook(context_dir=ctx)
        content = evo.read_text()
        hook._fold_corrections(evo, content, changelog)
        # New monthly shard exists and holds the archived data-point.
        shards = list(ctx.glob("EVOLUTION-archive-*.md"))
        assert shards, "fold must write a monthly EVOLUTION-archive-YYYY-MM.md shard"
        shard_body = shards[0].read_text()
        # fold keeps anchor+recent per cap=2, archives the middle data-point(s) —
        # assert a real DATA-POINT block landed, not which specific one.
        assert "RECURRENCE DATA-POINT" in shard_body
        assert "folded" in shard_body
        # Legacy fixed-name file must NOT be created by fold anymore.
        assert not (ctx / "EVOLUTION-archive.md").exists(), \
            "fold must not write the legacy fixed-name archive"


class TestSizeValve:
    """Sub-change 5: system-prompt size control. EVOLUTION.md is injected in full
    every session; over EVOLUTION_ARCHIVE_THRESHOLD (20K tok) the valve moves the
    LOWEST-value entries to the monthly shard (recall-backed cold storage, not
    deletion) so the always-injected core stays bounded. Evergreen core (Pattern/
    Durable tell/CAPSTONE/METHOD FIX/CLASS parent) is HARD-PROTECTED — never moved."""

    def test_is_evergreen_resident_by_marker_or_recency(self):
        # NEW判准 (XG 2026-08-14): resident = marker-bearing OR dated within the recency
        # window. The OLD blanket "any correction structure is evergreen" rule is GONE —
        # it made the whole Corrections region un-archivable and defeated the valve.
        from datetime import datetime, timezone
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        h = EvolutionMaintenanceHook(context_dir=Path("/tmp/x"))
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        # (a) marker-bearing → resident regardless of age.
        assert h._is_evergreen("### C001 | 2026-03-13\n- **Pattern**: x\n- **Durable tell**: y", now)
        assert h._is_evergreen("### CLASS A: Confidence\n- **Pattern**: skip-process\n- chain...", now)
        assert h._is_evergreen("- **METHOD FIX — dive protocol**: observe first", now)
        assert h._is_evergreen("- **CAPSTONE (2026-08-06)**: the write-side rule", now)
        # (b) recent (within 14d of 2026-08-20) marker-less correction → resident.
        assert h._is_evergreen("### C049 | 2026-08-11 [Bias A]\n- **Correction**: recent, no marker", now)
        # OLD marker-less correction (dated, >14d) → NO LONGER evergreen → archive-eligible.
        assert not h._is_evergreen("### C001 | 2026-03-13\n- **Correction**: old, no marker", now)
        # A marker-less ### CLASS with no date → not resident (recall-backed if evicted).
        assert not h._is_evergreen("### CLASS A: Confidence → Skip Process\n- chain...", now)
        # A plain O-Reference one-liner → NOT evergreen (evictable low-value).
        assert not h._is_evergreen("- O001 (CDP), O002 (DOM depth), O004 (nc -z > lsof)", now)

    def test_valve_noop_under_threshold(self, tmp_path):
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        ctx = tmp_path / ".context"
        ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        evo.write_text("# EVOLUTION\n\n## Optimizations Learned\n- **O003**: short.\n")
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo, threshold_tokens=15000)
        assert moved == 0, "under threshold → no eviction"
        assert not list(ctx.glob("EVOLUTION-archive-*.md")), "no shard written when under threshold"

    def test_hysteresis_evicts_down_to_target_not_just_under_trigger(self, tmp_path):
        # Hysteresis: valve triggers >20K but evicts DOWN TO the 15K low watermark,
        # leaving a 5K headroom band — NOT stopping the moment it crosses under 20K
        # (that left ~89 tok headroom → thrash). Build an >20K file dominated by a
        # large low-value Reference blob; assert final <= target(15K), not ~20K.
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        from core.context_directory_loader import ContextDirectoryLoader
        ctx = tmp_path / ".context"; ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        big = "\n".join(f"- O{200+i} occasionally-used opt {i} " + "pad " * 50 for i in range(360))
        evo.write_text(
            "# EVOLUTION\n\n## Corrections Captured\n"
            "### C1 | 2026-08-01\n- **Pattern**: keep me\n- **Durable tell**: keep me too\n\n"
            "## Optimizations Learned\n\n"
            "**Reference (triggered occasionally, archived for lookup):**\n" + big + "\n"
        )
        start = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert start > 20000, f"fixture must exceed trigger (got {start})"
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo)  # default watermarks: trigger 20K / target 15K
        final = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert moved > 0
        assert final <= 15000, f"must evict down to target 15K, not stop ~20K (final {final})"
        # evergreen correction + pattern/tell survive
        body = evo.read_text()
        assert "**Pattern**: keep me" in body and "**Durable tell**" in body

    def test_hysteresis_no_evict_in_headroom_band(self, tmp_path):
        # A file sitting in the 15–20K headroom band on entry must be a NO-OP — the
        # valve triggers only ABOVE 20K, so new entries have room to land without
        # re-triggering every session (the anti-thrash guarantee).
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        from core.context_directory_loader import ContextDirectoryLoader
        ctx = tmp_path / ".context"; ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        band = "\n".join(f"- O{300+i} opt {i} " + "pad " * 50 for i in range(150))
        evo.write_text("# EVOLUTION\n\n## Optimizations Learned\n- **Active:**\n" + band + "\n")
        tok = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert 15000 < tok <= 20000, f"fixture must sit in 15-20K band (got {tok})"
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo)
        assert moved == 0, "in-band file must NOT be evicted (headroom preserved, no thrash)"
        assert not list(ctx.glob("EVOLUTION-archive-*.md"))

    def test_watermark_clamp_prevents_inversion(self, tmp_path):
        # Gate-1 check-4: a threshold override BELOW the default target must clamp the
        # target down (target = min(target, threshold)) so the watermarks never invert.
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        from core.context_directory_loader import ContextDirectoryLoader
        ctx = tmp_path / ".context"; ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        blob = "\n".join(f"- O{400+i} opt {i} " + "pad " * 30 for i in range(120))
        evo.write_text("# EVOLUTION\n\n## Optimizations Learned\n- **Active:**\n" + blob + "\n")
        h = EvolutionMaintenanceHook(context_dir=ctx)
        # Small override threshold=6000 (< default target 15000): must clamp target to 6000
        # and evict down to <=6000, NOT try to reach 15000 (which would invert → no-op).
        moved = h._size_evict(evo, threshold_tokens=6000)
        final = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert moved > 0 and final <= 6000, f"clamped target must drive eviction to <=6K (final {final})"

    def test_valve_evicts_low_value_preserves_evergreen(self, tmp_path):
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        ctx = tmp_path / ".context"
        ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        # Build an over-threshold file: 1 evergreen correction (pattern+tell) that MUST
        # stay + a large low-value Reference blob that should be evicted first.
        big_ref = "\n".join(
            f"- O{100+i} (some occasionally-triggered optimization lesson number {i} "
            + "padding " * 60 + ")" for i in range(200)
        )
        evergreen = (
            "## Corrections Captured\n"
            "### C049 | 2026-08-11 [Bias A]\n"
            "- **Correction**: a load-bearing correction.\n"
            "- **Pattern**: improve-before-justify.\n"
            "- **Durable tell**: the clarity-excitement IS the signal.\n\n"
        )
        evo.write_text(
            "# EVOLUTION\n\n" + evergreen
            + "## Optimizations Learned\n\n"
            "**Reference (triggered occasionally, archived for lookup):**\n"
            + big_ref + "\n"
        )
        from core.context_directory_loader import ContextDirectoryLoader
        before = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert before > 15000, f"fixture must exceed threshold (got {before})"
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo, threshold_tokens=15000)
        assert moved > 0, "over threshold → evicts low-value"
        body = evo.read_text()
        # Evergreen correction + its pattern/tell SURVIVE.
        assert "load-bearing correction" in body
        assert "**Pattern**: improve-before-justify" in body
        assert "**Durable tell**" in body
        # Low-value Reference content moved to the monthly shard (recall-backed).
        shards = list(ctx.glob("EVOLUTION-archive-*.md"))
        assert shards, "evicted content must land in a monthly shard"
        assert "occasionally-triggered optimization lesson" in shards[0].read_text()

    def test_valve_never_evicts_below_core_even_if_over(self, tmp_path):
        # If ONLY evergreen core remains and it's still over threshold, the valve
        # does NOT evict core — it stops (P6: raise-the-cap signal, never cut core).
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        ctx = tmp_path / ".context"
        ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        core_blob = "\n".join(
            f"### C{i:03d} | 2026-08-01\n- **Pattern**: p{i} " + "judgment " * 60 + "\n"
            f"- **Durable tell**: t{i}\n" for i in range(80)
        )
        evo.write_text("# EVOLUTION\n\n## Corrections Captured\n" + core_blob)
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo, threshold_tokens=15000)
        body = evo.read_text()
        # Every evergreen correction still present — none evicted despite over-threshold.
        assert body.count("**Pattern**") == 80, "core corrections must never be evicted"
        assert moved == 0, "valve must move 0 when only core remains over-threshold"

    def test_valve_never_splits_and_archives_old_marker_less_whole(self, tmp_path):
        """REGRESSION (entry-splitting) + NEW判准 (XG 2026-08-14): the valve bounds
        corrections as WHOLE ### blocks and NEVER splits a multi-line entry. Under the
        NEW rule an OLD marker-less correction is archive-eligible (recall-backed) — but
        it must be moved as a WHOLE BLOCK (header + all its bullets together), never
        split. A recent OR marker-bearing correction stays resident. Dates are fixed and
        far in the past so recency is deterministic (not wall-clock fragile)."""
        from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
        from core.context_directory_loader import ContextDirectoryLoader
        ctx = tmp_path / ".context"
        ctx.mkdir()
        evo = ctx / "EVOLUTION.md"
        # RESIDENT: a marker-bearing correction (Pattern/tell skeleton) — old date, but
        # stays because of the marker.
        resident = (
            "## Corrections Captured\n"
            "### C001 | 2026-03-13 [Bias A] — a marker-bearing correction\n"
            "- **Correction**: the full body of C001 that must never be split off.\n"
            "- **Pattern**: keep-me-by-marker.\n"
            "- **Durable tell**: the marker keeps the skeleton resident.\n\n"
        )
        # ARCHIVE-ELIGIBLE: many OLD (2026-03) marker-less DATA-POINT/Cxxx blocks — under
        # the OLD blanket rule these were un-archivable; now they archive WHOLE.
        old_blocks = "\n".join(
            f"### DATA-POINT — old observation {i} [2026-03-10]\n"
            f"- **correction** — body {i} " + "padding " * 40 + "\n"
            f"- **Status** — resolved {i}\n" for i in range(150)
        )
        evo.write_text("# EVOLUTION\n\n" + resident + old_blocks + "\n")
        start = ContextDirectoryLoader.estimate_tokens(evo.read_text())
        assert start > 15000
        h = EvolutionMaintenanceHook(context_dir=ctx)
        moved = h._size_evict(evo, threshold_tokens=15000)
        body = evo.read_text()
        final = ContextDirectoryLoader.estimate_tokens(body)
        # (1) The valve ACTUALLY archived (the bug fix: old marker-less blocks ARE evictable now).
        assert moved > 0, "old marker-less corrections must now be archive-eligible"
        assert final <= 15000, f"valve must reach target now that Corrections is evictable (got {final})"
        # (2) The marker-bearing correction stayed resident, WHOLE.
        assert "the full body of C001 that must never be split off" in body, "marker-bearing stays"
        assert "keep-me-by-marker" in body
        # (3) NEVER-SPLIT: evicted blocks went to the shard as WHOLE units — a header and
        # its bullets travel together; no orphaned bullet is left behind or split off.
        shards = list(ctx.glob("EVOLUTION-archive-*.md"))
        assert shards, "eviction must write a shard"
        shard_text = shards[0].read_text()
        # every evicted DATA-POINT header in the shard carries its own body bullet (whole block)
        import re as _re
        evicted_ids = _re.findall(r"old observation (\d+)", shard_text)
        assert evicted_ids, "at least one whole DATA-POINT block archived"
        for i in evicted_ids:
            assert f"body {i} " in shard_text, f"block {i} archived WHOLE (header+body together, never split)"
        # no half-block left in the live file (a header without its body, or vice-versa)
        for i in evicted_ids:
            assert f"old observation {i} " not in body, f"evicted block {i} fully removed from live (no split)"
