"""Library health handler — the weekly Knowledge/ tidy-up.

Runs `core.library_health.auto_archive` over Knowledge/: the raw logs
(JobResults; DailyActivity's are context_health's) older than 90 days are moved into Archives/ by Swarm
itself (a move, so reversible), and the report, with what was moved
(`auto_archived`), is persisted to `Knowledge/.library-health.json` (atomic write)
for the Library overlay. No LLM, no external calls, zero token cost.

The job never deletes anything: delete stays an explicit, confirm-gated API action.
Wired as job type 'library_health'.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import SWARMWS

logger = logging.getLogger("swarm.jobs.library_health")


def _knowledge_dir() -> Path:
    return SWARMWS / "Knowledge"


def run_library_health() -> dict:
    """Archive the old raw logs, persist the report. Returns a summary dict. Never raises — a scan/write failure is reported, not fatal (a
    scheduled job must not crash the loop)."""
    try:
        from core.library_health import auto_archive, write_report_atomic
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"import failed: {exc}"}

    kdir = _knowledge_dir()
    if not kdir.is_dir():
        return {"status": "success", "findings": 0, "note": "no Knowledge/ dir"}

    try:
        report = auto_archive(kdir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("library health pass failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    try:
        write_report_atomic(kdir, report)
    except OSError as exc:
        logger.warning("library health report write failed: %s", exc)
        return {"status": "error", "error": f"write failed: {exc}"}

    n = len(report.get("findings", []))
    archived = report.get("auto_archived", {}).get("count", 0)
    logger.info("library health: archived %d, %d findings (clean=%s)", archived, n, report.get("clean"))
    return {
        "status": "success",
        "findings": n,
        "archived": archived,
        "clean": report.get("clean", True),
    }
