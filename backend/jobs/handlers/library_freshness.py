"""Library mount freshness handler — the periodic job body.

Re-probes every registered mount's health (source-exists + edited-after-index)
and persists it, so the Library overlay's 🟢/🟡/🔴 dots stay accurate without a
manual check. Pure read + a health-column write; no LLM, no external calls.

Wired as job type 'library_freshness' (system_jobs.py) → executor dispatch.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("swarm.jobs.library_freshness")


def run_library_freshness() -> dict:
    """Re-probe + persist health for all mounts. Returns a summary dict.

    Never raises — a missing DB (no mounts ever registered) or a bad mount is
    reported, not fatal (a scheduled job must not crash the loop)."""
    try:
        from jobs.paths import DB_PATH
        from core.library_mounts import LibraryMounts, refresh_all_mounts
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"import failed: {exc}"}

    if not DB_PATH.exists():
        return {"status": "success", "scanned": 0, "note": "no data.db yet"}

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            store = LibraryMounts(conn)
            store.ensure_table()
            summary = refresh_all_mounts(store)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("library freshness sweep failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    summary["status"] = "success"
    logger.info(
        "library freshness: %d scanned (%d fresh, %d stale, %d missing)",
        summary.get("scanned", 0), summary.get("fresh", 0),
        summary.get("stale", 0), summary.get("missing", 0),
    )
    return summary
