"""Per-stage timing derivation + REPORT.md duration correctness (run_fbf97252).

WHAT IS TESTED
--------------
The pipeline could not answer "which stage consumed the time". Three defects,
each with a test class here:

1. ``_try_generate_metrics`` did a bare ``write_text`` on METRICS.json, so
   completing a run DESTROYED whatever ``run-observe`` had accumulated
   (``stage_timing``/``think_depth``/``profile_decision``). Measured before the
   fix: 481 completed runs with no ``stage_timing`` vs 3 with, and every run
   still carrying ``wall_minutes`` was cancelled/abandoned/paused — i.e. it had
   never reached the destructive write. → ``TestMetricsMerge``.
2. ``REPORT.md`` rendered ``Duration: N/A`` for *every* run, because
   ``cmd_run_report`` requires ``completed_at`` while ``completed_at`` is only
   written by the ``--status completed`` branch that INSTRUCTIONS.md ran
   *afterwards* (measured: 466 of 466 N/A reports were generated before
   ``completed_at`` existed). Fixed by regenerating the report inside the
   completion branch, plus an ``updated_at`` fallback for genuinely in-flight
   runs. → ``TestCompletionRegeneratesReport`` / ``TestInFlightDurationFallback``.
3. No surface showed per-stage elapsed. Derived read-only from the artifact
   ``created`` timestamps already in manifest.json. → ``TestStagePublishTimes``
   / ``TestTimingSectionRendering``.

METHODOLOGY / KEY PROPERTIES
----------------------------
Fixtures are deliberately shaped to have TEETH against the specific tautology
traps a Gate-1 review identified:

* The in-flight duration fixture OMITS ``completed_at`` — a fixture that *sets*
  it passes against the unmodified code and proves nothing.
* Coverage is asserted with a MIXED-cause fixture (missing artifact_id, id absent
  from the manifest, unparseable timestamp, one good stage) so a hardcoded
  ``len(stages)`` denominator cannot pass.
* Ordering is asserted with the slowest stage placed LAST in ``stages[]`` but
  FIRST by timestamp — an array-order implementation fails. ``stages[]`` is
  append-order and is out of canonical order in 11.9% of real runs.
* Negative intervals are asserted as *unknown*, never rendered. 34 real runs
  would otherwise print a negative elapsed (worst -37.4 min) because a
  re-published artifact legitimately re-points ``artifact_id`` to a newer id.
* The malformed-manifest test writes genuinely invalid bytes rather than mocking
  a raise, so the real ``json`` parse path executes.
* ``TestTwoAttributeArgs`` constructs exactly ``SimpleNamespace(project, run_id)``
  — mirroring ``ui_actions.py``'s in-process daemon call, which passes only those
  two attributes. It is the only guard against a production-only AttributeError.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

BASE = datetime(2026, 9, 7, 10, 0, 0, tzinfo=timezone.utc)


def _iso(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _write_manifest(ws, project: str, entries: list[dict]) -> None:
    """Write .artifacts/manifest.json with the real production shape."""
    art_dir = ws / "Projects" / project / ".artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "manifest.json").write_text(
        json.dumps({
            "project": project,
            "pipeline_state": "deliver",
            "updated_at": _iso(0),
            "artifacts": entries,
        }, indent=2),
        encoding="utf-8",
    )


def _write_run(ws, project: str, run_id: str, run_data: dict):
    run_dir = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(run_data, indent=2), encoding="utf-8")
    return run_dir


def _artifact(art_id: str, created: str) -> dict:
    return {
        "id": art_id,
        "created": created,
        "file": f"{art_id}.json",
        "producer": "s_autonomous-pipeline",
        "summary": "x",
        "type": "evaluation",
        "superseded_by": None,
    }


def _stage(name: str, art_id: str | None = None, **extra) -> dict:
    rec = {"stage": name, "status": "completed", "stage_doc_consumed": True}
    if art_id is not None:
        rec["artifact_id"] = art_id
    rec.update(extra)
    return rec


# ── 1. METRICS.json merge (AC8) ──────────────────────────────────────────────


# ── 2. per-stage timing derivation (AC1/AC3/AC10) ────────────────────────────


# ── 4. completion regenerates the report (AC2 root fix) ──────────────────────


# ── 6. Gate-caught gaps: named bottleneck, cycles annotation, tz mixing ──────


# ── 7. Gate-2 caught: honest skip reporting + shape-hostile inputs ────────────
#
# These cover two defects an adversarial review found that the earlier tests
# could not see:
#   1. `cmd_run_update` reported `report_refreshed: True` even when the nested
#      `cmd_run_report` SKIPPED (hand-written report). The skip signal was a
#      stdout print, and the wrapper redirects stdout to a discarded buffer —
#      so "did not raise" was being read as "did refresh". A caller parsing the
#      envelope got a false positive about a write that never happened.
#   2. `_stage_publish_times` promised "never raises" but four on-disk shapes
#      raised TypeError, and because the call site is OUTSIDE a try, the whole
#      REPORT.md failed to generate — not just the timing block.


# ── 8. Gate-2 caught: the escape's second call site + honest denominators ─────
#
# Gate 2 (fresh-context adversarial) found three things the earlier tests could
# not see, all verified against source before fixing:
#   1. UnicodeDecodeError is a ValueError, NOT an OSError — one non-UTF-8 byte in
#      manifest.json escaped the guard and killed the ENTIRE report (exit 1, no
#      REPORT.md), breaking the "never raises" contract.
#   2. The `|`/newline escape protected the TABLE row but the "Slowest stage:"
#      PROSE line interpolated the raw name one line below the comment
#      explaining why that is dangerous — so a name could still forge a row.
#   3. With exactly one measured interval the share is unconditionally "100% of
#      measured time", which READS as a bottleneck finding while being pure
#      arithmetic (23 real runs are that shape).


# ── 9. Adversarial pass 3: the guards whose WRITERS were never exercised ──────
#
# A third adversarial pass ran 48 mutations against this file and proved 6 tests
# vacuous. The common shape: the FIXTURE pre-set the condition, so the production
# code that establishes it was never reached. Most load-bearing was the
# `report_autogenerated` stamp — the SOLE writer of the flag that protects a
# hand-written REPORT.md from being overwritten. Deleting the stamp block
# entirely kept all 32 tests green, i.e. the data-loss guard's only writer was
# unprotected. These tests exercise the WRITERS, not just the pre-seeded state.


# ── 10. Meta-review: the derived gaps must FEED the aggregate, not just render ─
#
# The meta-review found the sharper half of the AC8 defect: the analytics minutes
# column reads `stage_timing`, which only exists when an agent remembers to call
# `run-observe stage_start`/`stage_end`. Measured on the real corpus: 34 of 659
# METRICS.json carry it, and per-stage wall_minutes counts are evaluate=8,
# think=1, plan=1 — so with INSUFFICIENT_N=3 the new column would render a number
# for exactly ONE stage, forever, while the per-run derivation computed the same
# durations and threw them away into markdown.
#
# So completion PERSISTS the derived gaps (`derived_stage_gaps`) and the
# aggregator prefers them: one channel, fed by a code path every run passes,
# retroactive over every run that has artifacts.


class TestAggregatorPrefersDerivedGaps:
    """analyze_stage_efficiency must read the fed channel, not only the unfed one."""

    def test_derived_gaps_feed_the_minutes_columns(self):
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "scripts"))
        import pipeline_analytics as pa

        # THREE runs, so INSUFFICIENT_N=3 is satisfied and a number must render.
        corpus = [
            {"run_id": f"R{i}", "project": "P", "profile": "research",
             "status": "completed", "stage_tokens": {"think": 1000},
             "derived_stage_gaps": {"think": {"wall_minutes": 30.0}}}
            for i in range(3)
        ]
        eff = pa.analyze_stage_efficiency(corpus)
        assert eff["stages"]["think"].get("avg_minutes") == 30.0, (
            "the aggregator ignored derived_stage_gaps, so the minutes columns stay "
            f"empty even though every run now carries the data: {eff['stages']['think']}"
        )
        assert eff["stages"]["think"]["duration_sample_count"] == 3

    def test_observe_stage_timing_still_wins_when_present(self):
        """observe's explicit measurement is more precise — it must not be lost."""
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "scripts"))
        import pipeline_analytics as pa

        corpus = [
            {"run_id": f"R{i}", "project": "P", "profile": "research",
             "status": "completed", "stage_tokens": {"build": 1000},
             "stage_timing": {"build": {"wall_minutes": 12.0}},
             "derived_stage_gaps": {"build": {"wall_minutes": 99.0}}}
            for i in range(3)
        ]
        eff = pa.analyze_stage_efficiency(corpus)
        assert eff["stages"]["build"]["avg_minutes"] == 12.0, (
            "the derived gap (which includes inter-stage time) overwrote observe's "
            f"precise in-stage measurement: {eff['stages']['build']}"
        )
