"""Memory compliance API endpoint.

Exposes DailyActivity extraction metrics for observability.

Key public symbols:

- ``router``  — FastAPI APIRouter mounted at ``/api``.
- ``GET /api/memory-compliance``  — Returns extraction metrics.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])

# Will be set during app startup via set_compliance_tracker()
_compliance_tracker = None


def set_compliance_tracker(tracker) -> None:
    """Inject the ComplianceTracker instance at startup."""
    global _compliance_tracker
    _compliance_tracker = tracker


@router.get("/memory-compliance")
async def get_memory_compliance():
    """Return DailyActivity extraction compliance metrics."""
    if _compliance_tracker is None:
        return {"metrics": [], "retention_days": 30}
    metrics = _compliance_tracker.get_metrics()
    return {
        "metrics": [m.to_dict() for m in metrics],
        "retention_days": 30,
    }
