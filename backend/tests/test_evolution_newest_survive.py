"""EVOLUTION keeps its newest corrections and never reuses an archived id.

Seen 2026-10-25: the size-valve moved the ten newest corrections to the monthly
shard (new corrections are written at the top of the section, and the valve broke
ties inside a value tier by document position), and the next distilled correction
took id C053 again because the next id was read from the live file only.
"""
from __future__ import annotations

import re
from pathlib import Path


def _old(i: int) -> str:
    return (f"### C{100 + i:03d} | 2026-03-{1 + i % 27:02d} [Bias B]\n"
            f"- **Correction**: old correction {i} " + "pad " * 60 + "\n"
            "- **Durable tell**: an old tell.\n\n")


def test_the_valve_moves_the_oldest_of_a_tier_first_not_the_top_of_the_section(tmp_path):
    from hooks.evolution_maintenance_hook import EvolutionMaintenanceHook
    from core.context_directory_loader import ContextDirectoryLoader
    ctx = tmp_path / ".context"; ctx.mkdir()
    evo = ctx / "EVOLUTION.md"
    newest = ("### C200 | 2026-10-25 [Bias A]\n- **Correction**: the newest correction, keep me.\n"
              "- **Durable tell**: written minutes ago.\n\n")
    # the newest is at the top, as the writer puts it; the rest is old, same tier (marker-bearing)
    evo.write_text("# EVOLUTION\n\n## Corrections Captured\n" + newest
                   + "".join(_old(i) for i in range(120)), encoding="utf-8")
    assert ContextDirectoryLoader.estimate_tokens(evo.read_text()) > 15000
    moved = EvolutionMaintenanceHook(context_dir=ctx)._size_evict(evo, threshold_tokens=15000)
    body = evo.read_text()
    assert moved > 0
    assert "the newest correction, keep me." in body
    # what went is the oldest: C100 (2026-03-01) left before a later-dated old block
    assert "### C100 " not in body


def test_a_new_correction_id_follows_the_archive_too(tmp_path):
    from hooks.distillation_hook import DistillationTriggerHook
    ctx = tmp_path / ".context"; ctx.mkdir()
    evo = ctx / "EVOLUTION.md"
    evo.write_text("# EVOLUTION\n\n## Corrections Captured\n"
                   "### C052 | 2026-09-12\n- **Correction**: live\n- **Status**: active\n\n",
                   encoding="utf-8")
    (ctx / "EVOLUTION-archive-2026-10.md").write_text(
        "# EVOLUTION Archive\n\n### C063 | 2026-10-25\n- **Correction**: archived\n", encoding="utf-8")
    DistillationTriggerHook()._write_corrections(evo, [("2026-10-26", "a new one")])
    ids = re.findall(r"^### (C\d+)", evo.read_text(encoding="utf-8"), re.MULTILINE)
    assert "C064" in ids and "C053" not in ids, ids
