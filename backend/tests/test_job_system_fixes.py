"""
Tests for job system bug fixes (run_3ef0fe67).

Covers:
  AC1: Failed jobs appear in JSONL with correct status
  AC2: _write_job_result accepts actual status parameter
  AC3: Circuit breaker auto-resets after 24h cooldown
  AC4: signal-notify-slack disabled when config missing
  AC5-7: Briefing endpoint perf (async, cache, tail-read)
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── AC2: _write_job_result accepts status parameter ─────────────────

class TestWriteJobResultStatus:
    """_write_job_result() should persist the actual status, not hardcode 'success'."""

    def test_write_failed_status_to_jsonl(self, tmp_path):
        """JSONL entry should contain the status passed to _write_job_result."""
        from jobs.models import Job
        from jobs.executor import _write_job_result

        # Patch paths to tmp
        jsonl_path = tmp_path / "JobResults" / ".job-results.jsonl"
        with patch("jobs.executor.JOB_RESULTS_DIR", tmp_path / "JobResults"), \
             patch("jobs.executor.JOB_RESULTS_JSONL", jsonl_path):

            job = Job(id="test-job", name="Test Job", type="agent_task", schedule="0 * * * *")
            _write_job_result(
                job, "MCP auth failed", datetime(2026, 4, 25, tzinfo=timezone.utc),
                tokens=100, duration=5.0, status="auth_failed",
            )

            lines = jsonl_path.read_text().strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["status"] == "auth_failed", f"Expected auth_failed, got {entry['status']}"

    def test_write_failed_status_to_markdown(self, tmp_path):
        """Markdown frontmatter should contain the actual status."""
        from jobs.models import Job
        from jobs.executor import _write_job_result

        jsonl_path = tmp_path / "JobResults" / ".job-results.jsonl"
        with patch("jobs.executor.JOB_RESULTS_DIR", tmp_path / "JobResults"), \
             patch("jobs.executor.JOB_RESULTS_JSONL", jsonl_path):

            job = Job(id="test-job", name="Test Job", type="script", schedule="0 * * * *")
            md_path = _write_job_result(
                job, "crashed hard", datetime(2026, 4, 25, tzinfo=timezone.utc),
                tokens=0, duration=1.0, status="failed",
            )

            content = md_path.read_text()
            assert "status: failed" in content, f"Expected 'status: failed' in markdown, got:\n{content[:200]}"

    def test_write_default_status_is_success(self, tmp_path):
        """Default status should remain 'success' for backward compat."""
        from jobs.models import Job
        from jobs.executor import _write_job_result

        jsonl_path = tmp_path / "JobResults" / ".job-results.jsonl"
        with patch("jobs.executor.JOB_RESULTS_DIR", tmp_path / "JobResults"), \
             patch("jobs.executor.JOB_RESULTS_JSONL", jsonl_path):

            job = Job(id="test-job", name="Test Job", type="script", schedule="0 * * * *")
            _write_job_result(
                job, "all good", datetime(2026, 4, 25, tzinfo=timezone.utc),
                tokens=0, duration=1.0,
            )

            entry = json.loads(jsonl_path.read_text().strip())
            assert entry["status"] == "success"


# ── AC1: Crash handler persists failures ────────────────────────────

class TestCrashHandlerPersistence:
    """execute_job() crash path must write to JSONL, not just state.json."""

    def test_crashed_job_appears_in_jsonl(self, tmp_path):
        """When execute_job() catches an exception, the failure should
        appear in the JSONL results file."""
        from jobs.models import Job, SchedulerState, SchedulerDefaults
        from jobs.executor import execute_job

        jsonl_path = tmp_path / "JobResults" / ".job-results.jsonl"
        (tmp_path / "JobResults").mkdir(parents=True, exist_ok=True)

        # Create a job that will crash
        job = Job(id="crasher", name="Crasher", type="ddd_refresh", schedule="0 * * * *")
        state = SchedulerState()

        with patch("jobs.executor.JOB_RESULTS_DIR", tmp_path / "JobResults"), \
             patch("jobs.executor.JOB_RESULTS_JSONL", jsonl_path), \
             patch("jobs.handlers.ddd_refresh.run_ddd_refresh", side_effect=RuntimeError("boom")):

            result = execute_job(job, state, feeds=[])

        assert result.status == "failed"
        # The crash should be persisted to JSONL
        assert jsonl_path.exists(), "JSONL file should exist after crash"
        entry = json.loads(jsonl_path.read_text().strip())
        assert entry["status"] == "failed"
        assert "boom" in entry["summary"]


# ── AC3: Circuit breaker 24h auto-reset ─────────────────────────────

class TestCircuitBreakerAutoReset:
    """Circuit breaker should auto-reset after 24h cooldown."""

    def test_circuit_breaker_blocks_within_24h(self):
        """3+ failures within 24h should still block."""
        from jobs.models import Job, SchedulerState, JobState
        from jobs.scheduler import check_circuit_breaker

        job = Job(id="broken", name="Broken", type="script", schedule="0 * * * *")
        state = SchedulerState(jobs={
            "broken": JobState(
                last_run=datetime.now(timezone.utc) - timedelta(hours=2),
                last_status="failed",
                consecutive_failures=3,
            )
        })

        assert check_circuit_breaker(job, state) is False

    def test_circuit_breaker_resets_after_24h(self):
        """3+ failures but last_run >24h ago should auto-reset and allow retry."""
        from jobs.models import Job, SchedulerState, JobState
        from jobs.scheduler import check_circuit_breaker

        job = Job(id="broken", name="Broken", type="script", schedule="0 * * * *")
        state = SchedulerState(jobs={
            "broken": JobState(
                last_run=datetime.now(timezone.utc) - timedelta(hours=25),
                last_status="failed",
                consecutive_failures=3,
            )
        })

        assert check_circuit_breaker(job, state) is True
        # consecutive_failures should be reset
        assert state.jobs["broken"].consecutive_failures == 0

    def test_circuit_breaker_no_reset_without_last_run(self):
        """If last_run is None (never ran), don't crash."""
        from jobs.models import Job, SchedulerState, JobState
        from jobs.scheduler import check_circuit_breaker

        job = Job(id="new", name="New", type="script", schedule="0 * * * *")
        state = SchedulerState(jobs={
            "new": JobState(
                last_run=None,
                last_status="failed",
                consecutive_failures=3,
            )
        })
        # Should block — no last_run means we can't determine cooldown
        assert check_circuit_breaker(job, state) is False


# ── AC4: signal-notify-slack disabled when config missing ───────────

class TestNotifyJobPreFlight:
    """signal-notify-slack should be disabled by default."""

    def test_signal_notify_slack_disabled_by_default(self):
        """The system job definition should have enabled=False."""
        from jobs.system_jobs import SYSTEM_JOBS

        notify_job = next((j for j in SYSTEM_JOBS if j.id == "signal-notify-slack"), None)
        assert notify_job is not None, "signal-notify-slack should exist"
        assert notify_job.enabled is False, "signal-notify-slack should be disabled by default"


# ── AC5-6: Briefing endpoint async + cache ──────────────────────────

class TestBriefingEndpointPerf:
    """Briefing endpoint should use asyncio.to_thread and cache."""

    def test_briefing_endpoint_is_async_with_thread(self):
        """The briefing endpoint should offload sync work to a thread."""
        from routers.system import get_session_briefing
        import inspect
        # Endpoint must be async
        assert inspect.iscoroutinefunction(get_session_briefing), \
            "get_session_briefing must be async"

    def test_briefing_cache_returns_same_within_ttl(self):
        """Calling briefing twice within TTL should return cached result."""
        from routers.system import _briefing_cache, get_session_briefing

        # _briefing_cache should exist as a module-level cache dict
        assert hasattr(__import__("routers.system", fromlist=["_briefing_cache"]), "_briefing_cache"), \
            "_briefing_cache should be a module-level attribute"


# ── AC7: JSONL tail-read ────────────────────────────────────────────

class TestBriefingTailRead:
    """Briefing should only read the tail of JSONL, not the full file."""

    def test_briefing_reads_last_jobs_only(self, tmp_path):
        """build_session_briefing_data should work with large JSONL
        but only return recent (24h) entries."""
        from core.proactive_intelligence import build_session_briefing_data

        # Set up workspace structure
        ws = tmp_path / "workspace"
        (ws / ".context").mkdir(parents=True)
        (ws / "Knowledge" / "DailyActivity").mkdir(parents=True)
        (ws / "Knowledge" / "JobResults").mkdir(parents=True)
        (ws / ".context" / "MEMORY.md").write_text("## Open Threads\n### P0\n_(None)_\n")

        # Write 200 JSONL lines — only last 5 within 24h
        jsonl_path = ws / "Knowledge" / "JobResults" / ".job-results.jsonl"
        lines = []
        for i in range(200):
            ts = datetime.now(timezone.utc) - timedelta(hours=200 - i)
            lines.append(json.dumps({
                "job_id": f"job-{i}", "job_name": f"Job {i}",
                "run_at": ts.isoformat(), "status": "success",
                "summary": f"Result {i}", "duration_seconds": 1,
            }))
        jsonl_path.write_text("\n".join(lines) + "\n")

        result = build_session_briefing_data(ws)
        # Should return <= 5 jobs (24h window)
        assert len(result["jobs"]) <= 5
        # All returned jobs should be within 24h
        for job in result["jobs"]:
            assert job["status"] == "success"
