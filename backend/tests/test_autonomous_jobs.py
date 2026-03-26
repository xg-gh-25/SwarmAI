"""Tests for the autonomous jobs API router.

Validates status derivation, category mapping, and the endpoint
returning correct data from the unified job system (backend/jobs/).
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from schemas.autonomous_job import AutonomousJobCategory, AutonomousJobStatus

from routers.autonomous_jobs import (
    _derive_status,
    list_autonomous_jobs,
)
from jobs.models import Job, JobType, JobState, SchedulerState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_job(id: str, name: str = "", enabled: bool = True, category: str = "system") -> Job:
    return Job(
        id=id,
        name=name or id,
        type=JobType.SIGNAL_FETCH,
        schedule="0 8 * * *",
        enabled=enabled,
        category=category,
    )


def _make_state(jobs_dict: dict[str, dict] | None = None) -> SchedulerState:
    """Create a SchedulerState with optional per-job state."""
    state = SchedulerState()
    if jobs_dict:
        for jid, jdata in jobs_dict.items():
            js = JobState(**jdata)
            state.jobs[jid] = js
    return state


# ---------------------------------------------------------------------------
# _derive_status
# ---------------------------------------------------------------------------


class TestDeriveStatus:
    """Status derivation from enabled flag + failure count."""

    def test_disabled_job_is_paused(self):
        assert _derive_status(False, 0) == AutonomousJobStatus.PAUSED

    def test_enabled_no_failures_is_running(self):
        assert _derive_status(True, 0) == AutonomousJobStatus.RUNNING

    def test_circuit_breaker_3_failures_is_error(self):
        assert _derive_status(True, 3) == AutonomousJobStatus.ERROR

    def test_one_failure_stays_running(self):
        assert _derive_status(True, 1) == AutonomousJobStatus.RUNNING

    def test_two_failures_stays_running(self):
        assert _derive_status(True, 2) == AutonomousJobStatus.RUNNING

    def test_five_failures_is_error(self):
        assert _derive_status(True, 5) == AutonomousJobStatus.ERROR

    def test_disabled_with_failures_still_paused(self):
        """Disabled status takes precedence over failures."""
        assert _derive_status(False, 10) == AutonomousJobStatus.PAUSED


# ---------------------------------------------------------------------------
# Full endpoint — list_autonomous_jobs
# ---------------------------------------------------------------------------


class TestListAutonomousJobs:
    @pytest.mark.asyncio
    async def test_empty_when_load_fails(self):
        """Gracefully returns empty list on import/load error."""
        with patch("jobs.scheduler.load_jobs", side_effect=ImportError("no module")):
            result = await list_autonomous_jobs()
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_system_jobs(self):
        jobs = [_make_job("sig", "Signal Fetch"), _make_job("tune", "Self Tune")]
        state = _make_state({
            "sig": {
                "last_run": datetime(2026, 3, 23, 14, 7, tzinfo=timezone.utc),
                "last_status": "success",
                "total_runs": 12,
                "consecutive_failures": 0,
            },
        })

        with patch("jobs.scheduler.load_jobs", return_value=jobs), \
             patch("jobs.scheduler.load_state", return_value=state):
            result = await list_autonomous_jobs()

        assert len(result) == 2
        sig = result[0]
        assert sig.id == "sig"
        assert sig.name == "Signal Fetch"
        assert sig.status == AutonomousJobStatus.RUNNING
        assert sig.category == AutonomousJobCategory.SYSTEM
        assert sig.total_runs == 12
        assert sig.last_run_at is not None

        tune = result[1]
        assert tune.id == "tune"
        assert tune.total_runs == 0
        assert tune.last_status == "never"

    @pytest.mark.asyncio
    async def test_disabled_job_shows_paused(self):
        jobs = [_make_job("inbox", "Morning Inbox", enabled=False, category="user")]
        state = _make_state()

        with patch("jobs.scheduler.load_jobs", return_value=jobs), \
             patch("jobs.scheduler.load_state", return_value=state):
            result = await list_autonomous_jobs()

        assert len(result) == 1
        assert result[0].status == AutonomousJobStatus.PAUSED
        assert result[0].category == AutonomousJobCategory.USER_DEFINED

    @pytest.mark.asyncio
    async def test_failing_job_shows_error(self):
        jobs = [_make_job("broken", "Broken Job")]
        state = _make_state({
            "broken": {
                "last_status": "failed",
                "total_runs": 5,
                "consecutive_failures": 3,
            },
        })

        with patch("jobs.scheduler.load_jobs", return_value=jobs), \
             patch("jobs.scheduler.load_state", return_value=state):
            result = await list_autonomous_jobs()

        assert result[0].status == AutonomousJobStatus.ERROR
        assert result[0].consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_user_category_mapping(self):
        jobs = [
            _make_job("j1", category="user"),
            _make_job("j2", category="user"),  # Job model only allows "system"|"user"
            _make_job("j3", category="system"),
        ]
        state = _make_state()

        with patch("jobs.scheduler.load_jobs", return_value=jobs), \
             patch("jobs.scheduler.load_state", return_value=state):
            result = await list_autonomous_jobs()

        assert result[0].category == AutonomousJobCategory.USER_DEFINED
        assert result[1].category == AutonomousJobCategory.USER_DEFINED
        assert result[2].category == AutonomousJobCategory.SYSTEM
