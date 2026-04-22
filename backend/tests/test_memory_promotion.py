"""Tests for E2 Memory Promotion Upgrade: frequency gate + usage-based eviction.

Tests the compound loop: entries must appear in >=2 DailyActivity files to
promote (frequency gate), and section cap eviction removes lowest-usage
entries first (.memory-usage.json tracking).
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from hooks.distillation_hook import DistillationTriggerHook


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hook():
    return DistillationTriggerHook()


@pytest.fixture
def daily_dir(tmp_path):
    """Create a DailyActivity directory with sample files."""
    da_dir = tmp_path / "Knowledge" / "DailyActivity"
    da_dir.mkdir(parents=True)
    return da_dir


@pytest.fixture
def memory_dir(tmp_path):
    """Create .context directory with MEMORY.md."""
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True)
    return ctx


def _write_da_file(da_dir: Path, date_str: str, body: str) -> Path:
    """Write a DailyActivity file."""
    f = da_dir / f"{date_str}.md"
    f.write_text(f"---\ndate: \"{date_str}\"\n---\n{body}", encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# AC1: Entries in only 1 DailyActivity file NOT promoted
# AC2: Entries in >=2 DailyActivity files ARE promoted
# ---------------------------------------------------------------------------


class TestFrequencyGate:
    """Test _passes_frequency_gate on DistillationTriggerHook."""

    def test_entry_in_one_file_rejected(self, hook, daily_dir):
        """AC1: Entry appearing in only 1 DA file should NOT pass the gate."""
        _write_da_file(daily_dir, "2026-04-20", "## 10:00 | abc123\n- Implemented voice input feature")
        _write_da_file(daily_dir, "2026-04-21", "## 10:00 | def456\n- Fixed memory bug")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        result = hook._passes_frequency_gate(
            "2026-04-20: **Voice input feature shipped**",
            da_files,
        )
        assert result is False, "Entry in only 1 file should be rejected"

    def test_entry_in_two_files_accepted(self, hook, daily_dir):
        """AC2: Entry appearing in >=2 DA files should pass the gate."""
        _write_da_file(daily_dir, "2026-04-20", "## 10:00 | abc123\n- Voice input feature progress")
        _write_da_file(daily_dir, "2026-04-21", "## 10:00 | def456\n- Voice input feature completed")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        result = hook._passes_frequency_gate(
            "2026-04-21: **Voice input feature shipped**",
            da_files,
        )
        assert result is True, "Entry in >=2 files should be accepted"

    def test_short_entry_passes_through(self, hook, daily_dir):
        """Entries too short to fingerprint (< 2 significant words) pass through."""
        _write_da_file(daily_dir, "2026-04-20", "## 10:00 | abc\n- short")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        # Single significant word → fingerprint too small → passes
        result = hook._passes_frequency_gate("OK", da_files)
        assert result is True, "Short entries should pass through"

    def test_empty_da_files_passes_through(self, hook):
        """No DA files = gate should pass (don't block on empty history)."""
        result = hook._passes_frequency_gate(
            "2026-04-20: Important decision", []
        )
        assert result is True, "Empty DA list should pass through"

    def test_cold_start_single_file_passes(self, hook, daily_dir):
        """AC: With only 1 DA file, gate passes unconditionally (cold start safety)."""
        _write_da_file(daily_dir, "2026-04-20", "## 10:00 | abc\n- Made daemon-first design decision")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        result = hook._passes_frequency_gate(
            "2026-04-20: **Daemon-first design** — always assume daemon is primary",
            da_files,
        )
        assert result is True, "Cold start (1 file) should pass unconditionally"

    def test_rich_entry_different_wording_accepted(self, hook, daily_dir):
        """Entries with many words should still match when DA describes same topic differently."""
        _write_da_file(daily_dir, "2026-04-20", "Implemented OOM cascade fix with proactive RSS threshold")
        _write_da_file(daily_dir, "2026-04-21", "Fixed memory pressure using OOM cascade approach")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        result = hook._passes_frequency_gate(
            "2026-04-21: **OOM Cascade Fix shipped** — 3 bugs fixed: retry bypass, threshold below steady state, cost model",
            da_files,
        )
        assert result is True, "Rich entry should match with capped threshold"

    def test_three_files_accepted(self, hook, daily_dir):
        """Entry in 3+ files passes easily."""
        for i in range(3):
            d = f"2026-04-{20+i}"
            _write_da_file(daily_dir, d, f"## 10:00 | s{i}\n- OOM cascade fix progress")

        da_files = [
            {"path": f, "date": f.stem, "body": f.read_text(encoding="utf-8")}
            for f in sorted(daily_dir.glob("*.md"))
        ]

        result = hook._passes_frequency_gate(
            "2026-04-22: **OOM cascade fix shipped**", da_files
        )
        assert result is True


# ---------------------------------------------------------------------------
# AC3: .memory-usage.json tracks memory key references
# ---------------------------------------------------------------------------


class TestMemoryUsageTracking:
    """Test _track_memory_usage in ContextHealthHook."""

    def test_tracks_key_references(self, tmp_path, daily_dir):
        """AC3: Should find [RC04], [KD05] etc. in DailyActivity and write counts."""
        from hooks.context_health_hook import ContextHealthHook

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir(parents=True, exist_ok=True)

        today = date.today()
        _write_da_file(
            daily_dir,
            today.isoformat(),
            "Agent cited [RC04] and [KD05] in the response.\n"
            "Also referenced [RC04] again and [LL07].",
        )

        hook = ContextHealthHook()
        hook._track_memory_usage(tmp_path)

        usage_path = ctx_dir / ".memory-usage.json"
        assert usage_path.exists(), ".memory-usage.json should be created"

        usage = json.loads(usage_path.read_text())
        assert usage.get("RC04", 0) >= 1, "RC04 should be tracked"
        assert usage.get("KD05", 0) >= 1, "KD05 should be tracked"
        assert usage.get("LL07", 0) >= 1, "LL07 should be tracked"

    def test_ignores_old_files(self, tmp_path, daily_dir):
        """Should only scan files within the last 7 days."""
        from hooks.context_health_hook import ContextHealthHook

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir(parents=True, exist_ok=True)

        old_date = (date.today() - timedelta(days=14)).isoformat()
        _write_da_file(daily_dir, old_date, "Agent cited [RC99].")

        hook = ContextHealthHook()
        hook._track_memory_usage(tmp_path)

        usage_path = ctx_dir / ".memory-usage.json"
        if usage_path.exists():
            usage = json.loads(usage_path.read_text())
            assert usage.get("RC99", 0) == 0, "Old file refs should be ignored"

    def test_accumulates_across_files(self, tmp_path, daily_dir):
        """Multiple DA files should accumulate counts."""
        from hooks.context_health_hook import ContextHealthHook

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir(parents=True, exist_ok=True)

        today = date.today()
        _write_da_file(daily_dir, today.isoformat(), "Used [KD05] here.")
        yesterday = (today - timedelta(days=1)).isoformat()
        _write_da_file(daily_dir, yesterday, "Referenced [KD05] again.")

        hook = ContextHealthHook()
        hook._track_memory_usage(tmp_path)

        usage = json.loads((ctx_dir / ".memory-usage.json").read_text())
        assert usage.get("KD05", 0) >= 2, "Should accumulate across files"


# ---------------------------------------------------------------------------
# AC4: Section cap eviction removes lowest-usage entries first
# ---------------------------------------------------------------------------


class TestUsageBasedEviction:
    """Test _enforce_section_caps uses usage data for eviction order."""

    def test_evicts_lowest_usage_first(self, tmp_path):
        """AC4: When cap exceeded, zero-usage entries evicted before high-usage ones."""
        memory_path = tmp_path / "MEMORY.md"
        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir(parents=True, exist_ok=True)

        # Create MEMORY.md with 5 entries (cap is typically 30, we'll override)
        entries = [
            "- [RC01] 2026-04-01: Old but heavily used entry",
            "- [RC02] 2026-04-02: Never used entry A",
            "- [RC03] 2026-04-03: Moderately used entry",
            "- [RC04] 2026-04-04: Never used entry B",
            "- [RC05] 2026-04-05: Recently used entry",
        ]
        memory_path.write_text(
            "## Recent Context\n\n" + "\n".join(entries) + "\n",
            encoding="utf-8",
        )

        # Write usage data: RC01=10, RC03=3, RC05=5, RC02=0, RC04=0
        usage = {"RC01": 10, "RC03": 3, "RC05": 5}
        (ctx_dir / ".memory-usage.json").write_text(json.dumps(usage))

        # Evict to cap of 3 (remove 2 entries)
        # Should remove RC02 and RC04 (zero usage), keep RC01, RC03, RC05
        import hooks.distillation_hook as dh
        original_caps = dh.SECTION_CAPS.copy()
        try:
            dh.SECTION_CAPS["Recent Context"] = 3
            DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)
        finally:
            dh.SECTION_CAPS.update(original_caps)

        result = memory_path.read_text(encoding="utf-8")
        assert "[RC01]" in result, "High-usage entry should survive"
        assert "[RC03]" in result, "Moderate-usage entry should survive"
        assert "[RC05]" in result, "Recent-usage entry should survive"
        assert "[RC02]" not in result, "Zero-usage entry should be evicted"
        assert "[RC04]" not in result, "Zero-usage entry should be evicted"

    def test_falls_back_to_oldest_without_usage_data(self, tmp_path):
        """Without .memory-usage.json, eviction falls back to oldest-first."""
        memory_path = tmp_path / "MEMORY.md"

        entries = [
            "- [RC01] 2026-04-01: Oldest",
            "- [RC02] 2026-04-02: Middle",
            "- [RC03] 2026-04-03: Newest",
        ]
        memory_path.write_text(
            "## Recent Context\n\n" + "\n".join(entries) + "\n",
            encoding="utf-8",
        )

        import hooks.distillation_hook as dh
        original_caps = dh.SECTION_CAPS.copy()
        try:
            dh.SECTION_CAPS["Recent Context"] = 2
            DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)
        finally:
            dh.SECTION_CAPS.update(original_caps)

        result = memory_path.read_text(encoding="utf-8")
        # Without usage data, should fall back to original behavior (keep newest)
        assert "[RC01]" in result, "First entry should survive (prepend = newest at top)"
        assert "[RC02]" in result, "Second entry should survive"


# ---------------------------------------------------------------------------
# AC5: Existing tests pass (run in VERIFY phase, not here)
# ---------------------------------------------------------------------------
