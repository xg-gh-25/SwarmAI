"""Standalone entry point for the weekly evolution cycle.

Called by the ``evolution-cycle`` system job as a fallback when the
session-close hook hasn't fired (e.g. laptop closed for days).

Uses the same ``run_evolution_cycle()`` as the hook.  Idempotent via
the ``.evolution_last_run`` state file (7-day minimum interval checked
inside the hook — this script doesn't re-check, so it can force a run
when called from the scheduled job system).

Usage::

    python -m backend.jobs.run_evolution
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

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

    evals_dir = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / "SkillEvals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    # Add backend to sys.path for core imports
    sys.path.insert(0, str(backend_dir))

    from core.evolution_optimizer import run_evolution_cycle

    result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
    logger.info("Evolution cycle complete: %s", json.dumps(result.to_dict()))

    if hasattr(result, "health_report_path") and result.health_report_path:
        logger.info("Skill health report: %s", result.health_report_path)

    # Update last-run state file ONLY if cycle actually ran (not lock-rejected)
    if not result.errors:
        state_file = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / ".evolution_last_run"
        state_file.write_text(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), encoding="utf-8"
        )
    else:
        logger.info("Skipping state file update — cycle had errors: %s", result.errors)


if __name__ == "__main__":
    main()
