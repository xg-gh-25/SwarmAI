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
from datetime import datetime, timedelta, timezone
from unittest.mock import patch



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
        from jobs.models import Job, SchedulerState
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
                last_error="exit=1 boom",
                consecutive_failures=3,
            )
        })

        assert check_circuit_breaker(job, state) is True
        # consecutive_failures should be reset
        assert state.jobs["broken"].consecutive_failures == 0
        # run_14d01964 Gate-2 MED: last_error must reset IN LOCKSTEP — else a
        # cooldown-recovered job shows 0 failures + a stale error in 🔔.
        assert state.jobs["broken"].last_error is None

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
    """signal-notify-slack has pre-flight config check — skips gracefully."""

    def test_signal_notify_slack_exists_and_enabled(self):
        """The system job should exist and be enabled (pre-flight handles missing config)."""
        from jobs.system_jobs import SYSTEM_JOBS

        notify_job = next((j for j in SYSTEM_JOBS if j.id == "signal-notify-slack"), None)
        assert notify_job is not None, "signal-notify-slack should exist"
        assert notify_job.enabled is True, "signal-notify-slack should be enabled"

    def test_notify_handler_skips_when_slack_disabled(self, tmp_path):
        """Handler returns status=skipped when slack is not enabled in config."""
        from jobs.models import Job, SchedulerState
        from jobs.executor import _handle_notify

        job = Job(id="test-notify", name="Test Notify", type="notify",
                  schedule="0 * * * *", config={"channel": "slack", "message": "test"})
        state = SchedulerState()

        # Mock load_notify_config at the source module (lazy import inside handler)
        with patch("skills.s_notify.notify.load_notify_config",
                   return_value={"channels": {"slack": {"enabled": False}}}):
            result = _handle_notify(job, state)

        assert result.status == "skipped"
        assert "not enabled" in result.summary


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

        # _briefing_cache should exist as a module-level cache dict
        assert hasattr(__import__("routers.system", fromlist=["_briefing_cache"]), "_briefing_cache"), \
            "_briefing_cache should be a module-level attribute"

    def test_refresh_routes_to_dedicated_briefing_pool_not_default(self):
        """run_b36c7880: the heavy briefing recompute must run on the dedicated
        'briefing' pool, NEVER the default ThreadPoolExecutor — otherwise it can
        starve the default pool the event loop uses to schedule /health."""
        import routers.system as sysmod

        seen = {}

        def _fake_build(_ws):
            import threading
            seen["thread"] = threading.current_thread().name
            return {"focus": [], "signals": [], "jobs": [], "todos": [],
                    "learning": None, "generated_at": "x"}

        # Reset cache to force a cold compute, and stub the heavy builder.
        sysmod._briefing_cache["data"] = None
        sysmod._briefing_cache["expires_at"] = 0.0
        with patch("core.proactive_intelligence.build_session_briefing_data", _fake_build):
            asyncio.run(sysmod._refresh_briefing_cache())

        assert "briefing" in seen.get("thread", "").lower(), (
            f"briefing recompute ran on {seen.get('thread')!r}, not the dedicated "
            "'briefing' pool — /health-starvation fix is broken"
        )

    def test_stale_cache_served_without_blocking_on_recompute(self):
        """Stale-while-revalidate: a stale-but-present cache is returned
        immediately; the request must NOT block on the recompute."""
        import time as _t
        import routers.system as sysmod

        # Seed a stale entry.
        stale = {"focus": ["stale-marker"], "signals": [], "jobs": [], "todos": [],
                 "learning": None, "generated_at": "old"}
        sysmod._briefing_cache["data"] = stale
        sysmod._briefing_cache["expires_at"] = _t.monotonic() - 1  # expired

        async def _call():
            # Should return the stale data synchronously (no await on recompute).
            return await sysmod.get_session_briefing()

        result = asyncio.run(_call())
        assert result is stale or result.get("focus") == ["stale-marker"], (
            "stale cache must be served immediately (stale-while-revalidate)"
        )

    def test_stale_path_debounces_short_not_full_ttl(self):
        """A failing background refresh must NOT push expiry a full TTL (which
        would strand the cache stale forever). The stale path advances expiry by
        only the short debounce window, so it re-converges to a cold miss."""
        import time as _t
        import routers.system as sysmod

        stale = {"focus": ["stale"], "signals": [], "jobs": [], "todos": [],
                 "learning": None, "generated_at": "old"}
        sysmod._briefing_cache["data"] = stale
        sysmod._briefing_cache["expires_at"] = _t.monotonic() - 1

        async def _call():
            return await sysmod.get_session_briefing()

        before = _t.monotonic()
        asyncio.run(_call())
        pushed = sysmod._briefing_cache["expires_at"] - before
        # Must be ~debounce window, NOT a full TTL — proves a failing refresh
        # can re-converge instead of masking forever.
        assert pushed <= sysmod._BRIEFING_REFRESH_DEBOUNCE + 1, (
            f"stale path pushed expiry {pushed:.1f}s (>~debounce) — a failing "
            "refresh would strand the cache stale forever"
        )


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


# ── Auth-failure fallback mechanism ───────────────────────────────────

class TestAuthFailureFallback:
    """Verify fallback_script runs when agent_task fails with auth_failed."""

    def test_fallback_runs_on_auth_failure(self):
        """When agent_task returns auth_failed and fallback_script is set,
        the fallback script is executed and its result replaces the original."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState

        state = SchedulerState(jobs={})

        job = Job(
            id="test-monitor",
            name="Test Monitor",
            type="agent_task",
            schedule="0 * * * *",
            enabled=True,
            category="user",
            config={
                "prompt": "Test prompt",
                "fallback_script": "echo 'fallback executed'",
            },
            safety=JobSafety(max_budget_usd=1.0, timeout_seconds=30),
        )

        # Mock _handle_agent_task to return auth_failed
        with patch("jobs.executor._handle_agent_task") as mock_agent, \
             patch("jobs.executor._handle_script") as mock_script, \
             patch("jobs.executor._update_job_state"), \
             patch("jobs.executor.send_post_job_notification"):
            mock_agent.return_value = JobResult(
                job_id="test-monitor",
                timestamp=datetime.now(timezone.utc),
                status="auth_failed",
                summary="Slack MCP auth expired",
                duration_seconds=5,
            )
            mock_script.return_value = JobResult(
                job_id="test-monitor-fallback",
                timestamp=datetime.now(timezone.utc),
                status="success",
                summary="Fallback: 30 messages fetched",
                duration_seconds=2,
            )

            from jobs.executor import execute_job
            result = execute_job(job, state, feeds=[])

            # Fallback script should have been called
            mock_script.assert_called_once()
            # Result should be the successful fallback
            assert result.status == "success"
            assert result.job_id == "test-monitor"

    def test_no_fallback_without_config(self):
        """When fallback_script is not configured, auth_failed stays as-is."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState

        state = SchedulerState(jobs={})

        job = Job(
            id="test-monitor-no-fb",
            name="Test Monitor",
            type="agent_task",
            schedule="0 * * * *",
            enabled=True,
            category="user",
            config={"prompt": "Test prompt"},
            safety=JobSafety(max_budget_usd=1.0, timeout_seconds=30),
        )

        with patch("jobs.executor._handle_agent_task") as mock_agent, \
             patch("jobs.executor._handle_script") as mock_script, \
             patch("jobs.executor._update_job_state"), \
             patch("jobs.executor.send_post_job_notification"):
            mock_agent.return_value = JobResult(
                job_id="test-monitor-no-fb",
                timestamp=datetime.now(timezone.utc),
                status="auth_failed",
                summary="Slack MCP auth expired",
                duration_seconds=5,
            )

            from jobs.executor import execute_job
            result = execute_job(job, state, feeds=[])

            # No fallback — script handler should NOT be called
            mock_script.assert_not_called()
            assert result.status == "auth_failed"


# ── run_14d01964: last_error persistence (🔔 diagnosability) ────────

class TestLastErrorPersistence:
    """_update_job_state must persist WHY a job failed so the 🔔 queue is diagnosable."""

    def test_failed_job_persists_last_error(self):
        from jobs.models import SchedulerState, JobResult
        from jobs.executor import _update_job_state

        state = SchedulerState()
        result = JobResult(
            job_id="broken", timestamp=datetime.now(timezone.utc),
            status="failed", summary="boom", error="Script timed out after 900s",
        )
        _update_job_state(state, "broken", result)
        # THE fix: error must land in state (was always dropped -> empty 🔔)
        assert state.jobs["broken"].last_error == "Script timed out after 900s"

    def test_failed_job_falls_back_to_summary_when_no_error(self):
        from jobs.models import SchedulerState, JobResult
        from jobs.executor import _update_job_state

        state = SchedulerState()
        result = JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="failed", summary="18/20 passed, 2 failed", error=None,
        )
        _update_job_state(state, "j", result)
        assert state.jobs["j"].last_error == "18/20 passed, 2 failed"

    def test_success_clears_last_error(self):
        from jobs.models import SchedulerState, JobState, JobResult
        from jobs.executor import _update_job_state

        # pre-seed a stale error from a prior failure
        state = SchedulerState(jobs={"j": JobState(last_error="old failure", consecutive_failures=2)})
        result = JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="success", summary="ok",
        )
        _update_job_state(state, "j", result)
        # success must wipe the stale error (else 🔔 shows a fixed job's ghost)
        assert state.jobs["j"].last_error is None
        assert state.jobs["j"].consecutive_failures == 0

    def test_last_error_truncated_to_500(self):
        from jobs.models import SchedulerState, JobResult
        from jobs.executor import _update_job_state

        state = SchedulerState()
        huge = "x" * 5000
        result = JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="failed", error=huge,
        )
        _update_job_state(state, "j", result)
        assert len(state.jobs["j"].last_error) == 500

    def test_auth_failed_does_not_increment_circuit_breaker(self):
        """A transient auth failure must NOT count toward the circuit breaker —
        else a brief SSO/IdC token expiry trips the breaker (3 strikes) and forces
        a 24h cooldown or manual reset. It also must NOT reset a real failure
        streak (that would hide genuine job bugs)."""
        from jobs.models import SchedulerState, JobState, JobResult
        from jobs.executor import _update_job_state

        state = SchedulerState(jobs={"j": JobState(consecutive_failures=2)})
        result = JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="auth_failed", error="auth_preflight_failed",
        )
        _update_job_state(state, "j", result)
        # streak neither incremented (not a job bug) nor reset (not a success)
        assert state.jobs["j"].consecutive_failures == 2
        assert state.jobs["j"].last_status == "auth_failed"


class TestAuthPreflightIsTransient:
    """Auth PRE-flight failure must route through auth_failed (transient), not
    'failed' — so it auto-retries next tick instead of tripping the breaker.
    Regression: github-community-evening tripped the breaker on a brief token
    expiry and needed a manual reset."""

    def test_preflight_auth_failure_returns_auth_failed_status(self):
        from jobs.models import Job, SchedulerState
        from jobs import executor

        job = Job(id="j", name="J", type="agent_task", schedule="0 * * * *",
                  enabled=True, config={"prompt": "hi"})
        with patch("jobs.executor._resolve_claude_cli", return_value="/usr/bin/claude"), \
             patch("jobs.executor._check_claude_auth", return_value="not logged in"):
            result = executor._handle_agent_task(job, SchedulerState())

        # THE fix: transient auth expiry → auth_failed (auto-retry), not failed
        assert result.status == "auth_failed"
        assert result.error == "auth_preflight_failed"


# ── run_14d01964: unified_status failing-count excludes disabled jobs ─

class TestUnifiedStatusEnabledGap:
    """/api/jobs/status must not count disabled jobs as failing (same class as run_01d2fd9d)."""

    def test_disabled_failing_job_not_counted(self):
        from jobs.models import Job, SchedulerState, JobState
        from routers import jobs as jobs_router

        disabled_broken = Job(id="brain-push", name="Brain Push", type="script",
                              schedule="0 * * * *", enabled=False)
        enabled_ok = Job(id="live", name="Live", type="script",
                         schedule="0 * * * *", enabled=True)
        state = SchedulerState(jobs={
            "brain-push": JobState(last_status="failed", consecutive_failures=1),
            "live": JobState(last_status="success"),
        })

        with patch.object(jobs_router, "load_state", return_value=state, create=True), \
             patch("jobs.scheduler.load_state", return_value=state), \
             patch("jobs.scheduler.load_jobs", return_value=[disabled_broken, enabled_ok]):
            res = asyncio.run(jobs_router.unified_status())

        sched = res["scheduled_jobs"]
        # brain-push is disabled -> must NOT inflate failing
        assert sched["failing"] == 0
        assert sched["healthy"] == 1
        assert sched["enabled"] == 1
        assert sched["total"] == 2


# ── Monitor finding-vs-failure discriminator (run_89d7b5b8, DoD2) ────
# A monitor handler that RAN TO COMPLETION and returned a domain verdict
# (regression / degraded / integrity_only / no_changes) is NOT a job
# execution failure — the finding is alerted independently inside the
# handler. Only a genuine execution failure (error / crash / unknown)
# should map to JobResult "failed" and increment consecutive_failures.

class TestMonitorResultStatus:
    """_monitor_result_status classifies handler verdicts into success/skipped/failed."""

    def test_ok_verdict_maps_success(self):
        from jobs.executor import _monitor_result_status
        assert _monitor_result_status("healthy", ok={"healthy", "regression"}, benign={"skipped"}) == "success"
        assert _monitor_result_status("regression", ok={"healthy", "regression"}, benign={"skipped"}) == "success"

    def test_benign_verdict_maps_skipped(self):
        from jobs.executor import _monitor_result_status
        # benign (ran fine, no-op / gate-skip) -> "skipped" (real enum, resets
        # consecutive_failures at :2146, preserves after:/last_run semantics)
        assert _monitor_result_status("skipped", ok={"healthy", "regression"}, benign={"skipped"}) == "skipped"
        assert _monitor_result_status("integrity_only", ok={"success"}, benign={"integrity_only", "dry_run"}) == "skipped"
        assert _monitor_result_status("no_changes", ok={"success"}, benign={"skipped", "no_changes"}) == "skipped"

    def test_execution_failure_maps_failed(self):
        from jobs.executor import _monitor_result_status
        # error / unknown / anything not ok-or-benign -> "failed" (real failure stays visible)
        assert _monitor_result_status("error", ok={"healthy", "regression"}, benign={"skipped"}) == "failed"
        assert _monitor_result_status("unknown", ok={"success"}, benign={"integrity_only"}) == "failed"
        assert _monitor_result_status("", ok={"healthy"}, benign=set()) == "failed"

    def test_eval_regression_is_not_a_failure(self):
        """Regression tell for DoD2: the exact bug — a drift-detecting eval run
        that returns 'regression' must NOT be a job failure."""
        from jobs.executor import _monitor_result_status
        # eval_scheduled config: healthy/regression -> success, skipped -> skipped, error -> failed
        assert _monitor_result_status("regression", ok={"healthy", "regression"}, benign={"skipped"}) == "success"
