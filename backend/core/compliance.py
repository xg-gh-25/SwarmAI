"""Memory compliance tracking for DailyActivity extraction observability.

Tracks whether DailyActivity extractions are happening consistently so
that enforcement failures are visible and debuggable.

Key public symbols:

- ``DailyMetrics``       — Dataclass holding per-day extraction metrics.
- ``ComplianceTracker``  — In-memory store with 30-day retention.
                           No lock needed — hook execution is sequential.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30


@dataclass
class DailyMetrics:
    """Metrics for a single day."""

    date: str  # YYYY-MM-DD
    sessions_processed: int = 0
    files_written: int = 0
    failures: int = 0
    failure_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "date": self.date,
            "sessions_processed": self.sessions_processed,
            "files_written": self.files_written,
            "failures": self.failures,
            "failure_reasons": list(self.failure_reasons),
        }


class ComplianceTracker:
    """Tracks DailyActivity extraction metrics.

    In-memory store with 30-day retention.  Thread safety note: hook
    execution within a single ``fire_post_session_close()`` call is
    sequential, but multiple session closes can run concurrently (e.g.,
    TTL expiry in the cleanup loop vs explicit delete).  This is safe
    in CPython due to the GIL, but not formally thread-safe.
    """

    def __init__(self) -> None:
        self._metrics: dict[str, DailyMetrics] = {}

    def _get_today(self) -> DailyMetrics:
        """Get or create metrics for today."""
        today = date.today().isoformat()
        if today not in self._metrics:
            self._metrics[today] = DailyMetrics(date=today)
            self._prune_old()
        return self._metrics[today]

    def record_success(self, session_id: str) -> None:
        """Record a successful DailyActivity extraction."""
        m = self._get_today()
        m.sessions_processed += 1
        m.files_written += 1
        logger.debug("Compliance: success for session %s", session_id)

    def record_failure(self, session_id: str, reason: str) -> None:
        """Record a failed DailyActivity extraction."""
        m = self._get_today()
        m.sessions_processed += 1
        m.failures += 1
        m.failure_reasons.append(reason)
        logger.warning(
            "Compliance: failure for session %s: %s", session_id, reason
        )

    def get_metrics(self, days: int = 30) -> list[DailyMetrics]:
        """Return metrics for the most recent N days, sorted newest first."""
        sorted_keys = sorted(self._metrics.keys(), reverse=True)[:days]
        return [self._metrics[k] for k in sorted_keys]

    def _prune_old(self) -> None:
        """Remove metrics older than RETENTION_DAYS."""
        cutoff = (date.today() - timedelta(days=RETENTION_DAYS)).isoformat()
        stale = [k for k in self._metrics if k < cutoff]
        for k in stale:
            del self._metrics[k]
