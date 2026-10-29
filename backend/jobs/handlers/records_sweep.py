"""records_sweep — daily removal of run records untouched for 30 days and of their stale
temporary files: a Hive's own (core/hive_records.sweep) and the ones this machine took
back from its Hives (``sweep_host``). A no-op where there are none.

Public: ``run_records_sweep() -> dict`` (status success | error, plus counts).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_records_sweep() -> dict:
    try:
        from core import hive_records
        from core.project_registry import get_swarmws
        hive = hive_records.sweep(get_swarmws())            # a Hive's own records
        host = hive_records.sweep_host()                    # records this machine kept
        out = {"removed": hive["removed"] + host["removed"], "tmp": hive["tmp"] + host["tmp"]}
    except Exception as exc:  # noqa: BLE001 — a sweep failure is reported, never raised
        logger.exception("records sweep failed")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "success", **out}
