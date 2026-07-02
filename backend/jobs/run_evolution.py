"""Standalone entry point for the weekly evolution cycle.

The SOLE trigger for the mine→score→optimize cycle (run_6ac3fc0b). It used to
be a *fallback* for the session-close hook, but that hook trigger was removed:
a ~5-min cycle on the 180s-budget hook timed out before advancing state and
re-triggered every session. This script, invoked by the ``evolution-cycle``
system job (jobs/system_jobs.py), is now the only path.

Cadence is owned by the SCHEDULER (``job_state.last_run``, advanced on every
run incl. failure; cron_utils.is_cron_due catches up a missed weekly slot after
wake). This script does NOT re-check the 7-day interval — it runs the cycle
unconditionally when the scheduler fires it, and writes ``.evolution_last_run``
on success (that file is now consumed only by loops-health reporting, not for
triggering).

Usage::

    python -m backend.jobs.run_evolution
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .paths import CONTEXT_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    # Resolve paths
    backend_dir = Path(__file__).resolve().parent.parent
    skills_dir = backend_dir / "skills"
    if not skills_dir.is_dir():
        logger.error("Skills directory not found: %s", skills_dir)
        sys.exit(1)

    # Find transcripts directory: pass the base projects/ dir so
    # SessionMiner._iter_transcripts uses rglob("*.jsonl") to find
    # ALL transcripts across all project subdirectories (Gap 2 fix).
    transcripts_dir = Path.home() / ".claude" / "projects"

    evals_dir = CONTEXT_DIR / "SkillEvals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    # Add backend to sys.path for core imports
    sys.path.insert(0, str(backend_dir))

    from core.evolution_optimizer import run_evolution_cycle

    result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir, dry_run=False)
    logger.info("Evolution cycle complete: %s", json.dumps(result.to_dict()))

    if hasattr(result, "health_report_path") and result.health_report_path:
        logger.info("Skill health report: %s", result.health_report_path)

    # Update last-run state file ONLY if cycle actually ran (not lock-rejected)
    if not result.errors:
        state_file = CONTEXT_DIR / ".evolution_last_run"
        state_file.write_text(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
        )
    else:
        logger.info("Skipping state file update — cycle had errors: %s", result.errors)


if __name__ == "__main__":
    main()
