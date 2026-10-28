"""Tests for E2 Memory Promotion Upgrade: frequency gate + usage-based eviction.

Tests the compound loop: entries must appear in >=2 DailyActivity files to
promote (frequency gate), and section cap eviction removes lowest-usage
entries first (.memory-usage.json tracking).
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest



# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------




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




# ---------------------------------------------------------------------------
# AC5: Existing tests pass (run in VERIFY phase, not here)
# ---------------------------------------------------------------------------
