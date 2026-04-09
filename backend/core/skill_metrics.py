"""Skill Metrics Store — tracks skill invocations, outcomes, and user satisfaction.

Provides aggregated statistics for skill evolution decisions: which skills
need improvement (high correction rate, low success rate) and which are
performing well.

Key public symbols:

- ``SkillMetricsStore``    — Main class: record invocations, query stats.
- ``SkillStats``           — Dataclass for aggregated per-skill statistics.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SkillStats:
    """Aggregated statistics for a single skill."""
    skill_name: str
    invocation_count: int
    success_rate: float         # 0.0-1.0
    avg_duration: float         # seconds
    correction_rate: float      # 0.0-1.0 (user_satisfaction='correction' / total)
    last_used: str              # ISO date


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS skill_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    invocation_date TEXT NOT NULL,
    session_id TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('success', 'partial', 'failure', 'abandoned')),
    duration_seconds REAL DEFAULT 0.0,
    user_satisfaction TEXT DEFAULT 'unknown' CHECK(user_satisfaction IN ('correction', 'accepted', 'unknown'))
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_skill_metrics_name ON skill_metrics(skill_name);
"""


class SkillMetricsStore:
    """Tracks skill invocation metrics in a local SQLite database.

    Opens its own ``sqlite3`` connection with WAL mode and busy_timeout
    to match the main DB layer's settings.  This is intentional: the
    metrics table is append-heavy and read-infrequently, so a dedicated
    connection avoids contention with the main async DB pool.  All
    public methods are thread-safe via ``_lock``.
    """

    def __init__(self, db_path: Path) -> None:
        """Initialize with path to SQLite DB. Creates table if not exists."""
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    def record(
        self,
        skill_name: str,
        session_id: str,
        outcome: str,
        duration_seconds: float,
        user_satisfaction: str = "unknown",
    ) -> None:
        """Record a skill invocation.

        Args:
            skill_name: Name of the skill invoked.
            session_id: Session identifier.
            outcome: One of 'success', 'partial', 'failure', 'abandoned'.
            duration_seconds: How long the invocation took.
            user_satisfaction: One of 'correction', 'accepted', 'unknown'.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO skill_metrics (skill_name, invocation_date, session_id, "
                "outcome, duration_seconds, user_satisfaction) VALUES (?, ?, ?, ?, ?, ?)",
                (skill_name, date.today().isoformat(), session_id, outcome,
                 duration_seconds, user_satisfaction),
            )
            self._conn.commit()

    def get_stats(self, skill_name: str) -> SkillStats | None:
        """Get aggregated stats for a skill. Returns None if no data."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) as cnt,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
                    AVG(duration_seconds) as avg_dur,
                    SUM(CASE WHEN user_satisfaction = 'correction' THEN 1 ELSE 0 END) as corrections,
                    MAX(invocation_date) as last_used
                FROM skill_metrics
                WHERE skill_name = ?
                """,
                (skill_name,),
            ).fetchone()

        if row is None or row[0] == 0:
            return None

        cnt, successes, avg_dur, corrections, last_used = row
        return SkillStats(
            skill_name=skill_name,
            invocation_count=cnt,
            success_rate=successes / cnt if cnt > 0 else 0.0,
            avg_duration=avg_dur or 0.0,
            correction_rate=corrections / cnt if cnt > 0 else 0.0,
            last_used=last_used or "",
        )

    def get_all_stats(self) -> list[SkillStats]:
        """Get stats for all tracked skills in a single atomic query."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    skill_name,
                    COUNT(*) as cnt,
                    SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successes,
                    AVG(duration_seconds) as avg_dur,
                    SUM(CASE WHEN user_satisfaction = 'correction' THEN 1 ELSE 0 END) as corrections,
                    MAX(invocation_date) as last_used
                FROM skill_metrics
                GROUP BY skill_name
                """
            ).fetchall()
        results = []
        for row in rows:
            name, cnt, successes, avg_dur, corrections, last_used = row
            if cnt > 0:
                results.append(SkillStats(
                    skill_name=name,
                    invocation_count=cnt,
                    success_rate=successes / cnt,
                    avg_duration=avg_dur or 0.0,
                    correction_rate=corrections / cnt,
                    last_used=last_used or "",
                ))
        return results

    def get_evolution_candidates(self) -> list[str]:
        """Skills with correction_rate > 0.3 OR success_rate < 0.7 AND invocation_count >= 5."""
        all_stats = self.get_all_stats()
        candidates = []
        for s in all_stats:
            if s.invocation_count < 5:
                continue
            if s.correction_rate > 0.3 or s.success_rate < 0.7:
                candidates.append(s.skill_name)
        return candidates
