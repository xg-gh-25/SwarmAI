"""Library health handler — the weekly Knowledge/ cleanup-candidate scan.

Runs `core.library_health.scan_library_health` over Knowledge/ and persists the
report to `Knowledge/.library-health.json` (atomic write), so the Library overlay
can show a health section + one-click cleanup actions WITHOUT scanning on every
open. Pure read + a single JSON write; no LLM, no external calls, zero token cost.

The job NEVER mutates knowledge (no auto-archive, no auto-delete) — it only writes
the report. Actions are executed by the API layer on explicit user click, and
delete is confirm-gated there. Wired as job type 'library_health'.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..paths import SWARMWS

logger = logging.getLogger("swarm.jobs.library_health")


def _knowledge_dir() -> Path:
    return SWARMWS / "Knowledge"


def run_library_health() -> dict:
    """Scan Knowledge/ for cleanup candidates + persist the report. Returns a
    summary dict. Never raises — a scan/write failure is reported, not fatal (a
    scheduled job must not crash the loop)."""
    try:
        from core.library_health import scan_library_health, write_report_atomic
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"import failed: {exc}"}

    kdir = _knowledge_dir()
    if not kdir.is_dir():
        return {"status": "success", "findings": 0, "note": "no Knowledge/ dir"}

    try:
        report = scan_library_health(kdir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("library health scan failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    try:
        write_report_atomic(kdir, report)
    except OSError as exc:
        logger.warning("library health report write failed: %s", exc)
        return {"status": "error", "error": f"write failed: {exc}"}

    n = len(report.get("findings", []))
    logger.info("library health: %d findings (clean=%s)", n, report.get("clean"))
    return {
        "status": "success",
        "findings": n,
        "clean": report.get("clean", True),
    }
