"""
Tests for job system bug fixes (run_3ef0fe67).

Covers:
  AC1: Failed jobs appear in JSONL with correct status
  AC2: _write_job_result accepts actual status parameter
  AC3: Circuit breaker auto-resets after 24h cooldown
  AC3b: Exponential half-open retry (1h, 2h, 4h ... <=24h), re-trip, lockstep
  AC4: signal-notify-slack disabled when config missing
  AC5-7: Briefing endpoint perf (async, cache, tail-read)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
        # The crash should be persisted to JSONL. Since run_d383e9a0, a first
        # failure ALSO writes a single-failure alert row (P0-3), so the file may
        # hold >1 line — find the crash RESULT row among them (not assume 1 line).
        assert jsonl_path.exists(), "JSONL file should exist after crash"
        entries = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        crash_rows = [e for e in entries if e.get("status") == "failed" and "boom" in e.get("summary", "")]
        assert crash_rows, "the crash result row must be persisted to JSONL"


# ── AC3: Circuit breaker 24h auto-reset ─────────────────────────────

class TestCircuitBreakerAutoReset:
    """Circuit breaker should auto-reset after 24h cooldown."""

    def test_circuit_breaker_blocks_within_24h(self):
        """3+ failures inside the half-open backoff (1h at 3 failures) block.

        (Was 2h ago under the old flat 24h cooldown; with the exponential
        half-open retry a 3-failure streak gets one retry after 1h — see
        TestCircuitBreakerBackoff.)
        """
        from jobs.models import Job, SchedulerState, JobState
        from jobs.scheduler import check_circuit_breaker

        job = Job(id="broken", name="Broken", type="script", schedule="0 * * * *")
        state = SchedulerState(jobs={
            "broken": JobState(
                last_run=datetime.now(timezone.utc) - timedelta(minutes=30),
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


# ── Circuit breaker exponential half-open retry ─────────────────────


def _tripped_state(job_id: str, failures: int, hours_ago: float, error: str = "exit=1 boom"):
    from jobs.models import SchedulerState, JobState
    return SchedulerState(jobs={
        job_id: JobState(
            last_run=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            last_status="failed",
            last_error=error,
            consecutive_failures=failures,
        )
    })


class TestCircuitBreakerBackoff:
    """Tripped breaker allows one retry after 1h, 2h, 4h ... (cap 24h).

    Methodology: drive the REAL check_circuit_breaker + executor
    _update_job_state on a SchedulerState; only the clock offset of last_run
    and the write-alert side effect are controlled.
    """

    def test_backoff_schedule_doubles_and_caps_at_24h(self):
        from jobs.scheduler import circuit_breaker_backoff
        hours = [circuit_breaker_backoff(n) / timedelta(hours=1) for n in range(3, 10)]
        assert hours == [1, 2, 4, 8, 16, 24, 24]
        # Below threshold is still the base (never negative / fractional).
        assert circuit_breaker_backoff(0) == timedelta(hours=1)
        # Huge streaks don't overflow.
        assert circuit_breaker_backoff(10_000) == timedelta(hours=24)

    @pytest.mark.parametrize("failures,blocked_h,open_h", [
        (3, 0.9, 1.1), (4, 1.9, 2.1), (5, 3.9, 4.1), (7, 15.9, 16.1),
    ])
    def test_half_open_only_after_backoff(self, failures, blocked_h, open_h):
        from jobs.models import Job
        from jobs.scheduler import check_circuit_breaker
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")

        state = _tripped_state("j", failures, blocked_h)
        assert check_circuit_breaker(job, state) is False

        state = _tripped_state("j", failures, open_h)
        assert check_circuit_breaker(job, state) is True
        # Half-open does NOT reset: counter and last_error stay together.
        js = state.jobs["j"]
        assert js.consecutive_failures == failures
        assert js.last_error == "exit=1 boom"

    def test_half_open_failure_retrips_with_doubled_backoff(self):
        from jobs.models import Job, JobResult
        from jobs.scheduler import check_circuit_breaker
        from jobs.executor import _update_job_state
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")
        state = _tripped_state("j", 3, 1.5)
        assert check_circuit_breaker(job, state) is True  # half-open

        now = datetime.now(timezone.utc)
        with patch("jobs.executor._write_failure_streak_alert"):
            _update_job_state(state, "j", JobResult(
                job_id="j", timestamp=now, status="failed",
                summary="Script timed out after 120s", error="timeout: stuck",
            ))
        js = state.jobs["j"]
        assert js.consecutive_failures == 4
        assert js.last_error == "timeout: stuck"
        # Re-tripped: blocked right after, and still blocked at 1.5h (needs 2h).
        assert check_circuit_breaker(job, state) is False
        js.last_run = now - timedelta(hours=1.5)
        assert check_circuit_breaker(job, state) is False
        js.last_run = now - timedelta(hours=2.1)
        assert check_circuit_breaker(job, state) is True

    def test_half_open_success_clears_counter_and_error_together(self):
        from jobs.models import Job, JobResult
        from jobs.scheduler import check_circuit_breaker
        from jobs.executor import _update_job_state
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")
        state = _tripped_state("j", 5, 4.5)
        assert check_circuit_breaker(job, state) is True
        _update_job_state(state, "j", JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="success", summary="ok",
        ))
        js = state.jobs["j"]
        assert js.consecutive_failures == 0
        assert js.last_error is None

    @pytest.mark.parametrize("failures", [3, 5, 7, 8, 12])
    def test_full_reset_after_24h_for_any_streak(self, failures):
        """Reset is checked before half-open: a 3..7 streak idle 25h must reset,
        not merely half-open with the stale counter."""
        from jobs.models import Job
        from jobs.scheduler import check_circuit_breaker
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")
        state = _tripped_state("j", failures, 25)
        assert check_circuit_breaker(job, state) is True
        js = state.jobs["j"]
        assert js.consecutive_failures == 0
        assert js.last_error is None

    def test_capped_streak_blocks_until_24h(self):
        from jobs.models import Job
        from jobs.scheduler import check_circuit_breaker
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")
        state = _tripped_state("j", 9, 23.5)
        assert check_circuit_breaker(job, state) is False
        assert state.jobs["j"].consecutive_failures == 9
        assert state.jobs["j"].last_error == "exit=1 boom"

    def test_naive_last_run_is_treated_as_utc(self):
        from jobs.models import Job, SchedulerState, JobState
        from jobs.scheduler import check_circuit_breaker
        job = Job(id="j", name="J", type="script", schedule="0 * * * *")
        naive = (datetime.now(timezone.utc) - timedelta(hours=1.5)).replace(tzinfo=None)
        state = SchedulerState(jobs={"j": JobState(
            last_run=naive, last_status="failed", consecutive_failures=3,
        )})
        assert check_circuit_breaker(job, state) is True


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
        # NEW behavior (adversarial HIGH fix): the auth streak IS tracked separately
        # so a persistent auth failure can escalate instead of retrying invisibly.
        assert state.jobs["j"].consecutive_auth_failures == 1

    def test_persistent_auth_failure_escalates_to_visible_blocking(self):
        """Adversarial HIGH: a PERMANENT auth failure (revoked IdC, wrong region)
        must not retry invisibly forever — past the threshold it surfaces as a
        BLOCKING Need-You card, distinct from a code circuit-break."""
        from jobs.models import SchedulerState, JobState, JobResult
        from jobs.executor import _update_job_state, _FAILURE_ALERT_THRESHOLD
        from core.attention_authority import _collect_jobs, TIER_BLOCKING
        from jobs import scheduler as sched

        state = SchedulerState()
        # Drive N consecutive auth failures.
        for _ in range(_FAILURE_ALERT_THRESHOLD):
            _update_job_state(state, "stuck", JobResult(
                job_id="stuck", timestamp=datetime.now(timezone.utc),
                status="auth_failed", error="auth_preflight_failed",
            ))
        assert state.jobs["stuck"].consecutive_auth_failures == _FAILURE_ALERT_THRESHOLD
        # It must now appear as a BLOCKING attention item (was structurally invisible
        # before the fix: _collect_jobs only checked consecutive_failures).
        with patch("jobs.scheduler.load_state", return_value=state), \
             patch("jobs.scheduler.load_jobs", return_value=[]):
            items = _collect_jobs()
        auth_items = [i for i in items if i.id == "job_auth:stuck"]
        assert len(auth_items) == 1
        assert auth_items[0].tier == TIER_BLOCKING
        assert "Auth broken" in auth_items[0].title

    def test_recovery_clears_auth_streak(self):
        """A real success wipes the auth streak (so a recovered job stops nagging)."""
        from jobs.models import SchedulerState, JobState, JobResult
        from jobs.executor import _update_job_state

        state = SchedulerState(jobs={"j": JobState(consecutive_auth_failures=3)})
        _update_job_state(state, "j", JobResult(
            job_id="j", timestamp=datetime.now(timezone.utc),
            status="success", summary="ok",
        ))
        assert state.jobs["j"].consecutive_auth_failures == 0

    def test_auth_streak_alert_wording_is_not_a_failure_lie(self, tmp_path):
        """2nd-round adversarial HIGH: the streak alert is rendered verbatim in the
        briefing. An AUTH streak must NOT be labeled a code 'failure' nor claim a
        '24h cooldown' (auth jobs retry hourly, no cooldown)."""
        import json as _json
        from jobs.models import JobResult
        from jobs import executor

        alert_path = tmp_path / "alerts.jsonl"
        with patch.object(executor, "JOB_RESULTS_JSONL", alert_path):
            executor._write_failure_streak_alert(
                "stuck", 3,
                JobResult(job_id="stuck", timestamp=datetime.now(timezone.utc),
                          status="auth_failed", error="auth_preflight_failed"),
                is_auth=True,
            )
        entry = _json.loads(alert_path.read_text().strip())
        summary = entry["summary"].lower()
        assert entry["status"] == "auth_failed"          # not "failed"
        assert "auth" in summary
        # must NOT repeat the failure-path lie "in 24h cooldown — auto-retry after
        # cooldown expires"; saying "no cooldown" is the correct opposite.
        assert "24h cooldown" not in summary
        assert "no cooldown" in summary


class TestAuthRetryIsBounded:
    """2nd-round adversarial MED: a PERMANENTLY dead auth job must not fast-retry
    hourly forever. Fast-retry is gated on the streak being below threshold; past
    it, the job stops churning and relies on the BLOCKING card + normal cron."""

    def test_fast_retry_stops_once_auth_streak_hits_threshold(self):
        from jobs.models import Job, SchedulerState, JobState
        from jobs.executor import _FAILURE_ALERT_THRESHOLD
        from jobs import scheduler as sched

        # RECENT last_run + a once-daily cron whose slot is NOT now → the cron gate
        # would NOT fire on its own, so `is_job_due` can only be True via the
        # auth fast-retry branch. This isolates the gate we're testing.
        now = datetime.now(timezone.utc)
        recent = now - timedelta(minutes=5)
        # pick a cron hour that is definitely not the current hour
        cron_hour = (now.hour + 6) % 24
        job = Job(id="j", name="J", type="agent_task",
                  schedule=f"0 {cron_hour} * * *",
                  enabled=True, config={"prompt": "x"})

        # below threshold → fast-retry ON (transient auth blip auto-retries)
        transient = SchedulerState(jobs={"j": JobState(
            last_status="auth_failed", last_run=recent,
            consecutive_auth_failures=_FAILURE_ALERT_THRESHOLD - 1)})
        assert sched.is_job_due(job, transient) is True

        # at threshold → fast-retry OFF (no hourly churn; BLOCKING card carries it,
        # job falls back to its normal cron cadence until auth is fixed)
        stuck = SchedulerState(jobs={"j": JobState(
            last_status="auth_failed", last_run=recent,
            consecutive_auth_failures=_FAILURE_ALERT_THRESHOLD)})
        assert sched.is_job_due(job, stuck) is False


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


# ── run_d383e9a0: Community job P0 fixes (retry / single-failure alert) ──

class TestAgentTaskRetry:
    """P0-1: bounded in-tick retry for retryable agent_task failures (opt-in via
    JobSafety.retryable, default False for idempotency safety)."""

    def test_jobsafety_retryable_defaults_false(self):
        """AC1: retryable is opt-in — a job without it declared is NOT retried."""
        from jobs.models import JobSafety
        assert JobSafety().retryable is False

    def test_retryable_job_retries_once_on_timeout(self):
        """AC1: retryable=True + first attempt timeout → retry once → success."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        job = Job(id="rt", name="Retryable", type="agent_task", schedule="0 * * * *",
                  safety=JobSafety(retryable=True))
        state = SchedulerState()
        calls = {"n": 0}

        def fake_handle(j, s):
            calls["n"] += 1
            if calls["n"] == 1:
                return JobResult(job_id=j.id, timestamp=datetime.now(timezone.utc),
                                 status="failed", error="timeout", summary="Timeout after 480s")
            return JobResult(job_id=j.id, timestamp=datetime.now(timezone.utc),
                             status="success", summary="ok")

        with patch("jobs.executor._handle_agent_task", side_effect=fake_handle):
            result = ex.execute_job(job, state, feeds=[])
        assert calls["n"] == 2, "should have retried exactly once"
        assert result.status == "success"

    def test_non_retryable_job_not_retried(self):
        """AC1: retryable=False (default) + timeout → NO retry (handler called once)."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        job = Job(id="nrt", name="NonRetryable", type="agent_task", schedule="0 * * * *",
                  safety=JobSafety(retryable=False))
        state = SchedulerState()
        calls = {"n": 0}

        def fake_handle(j, s):
            calls["n"] += 1
            return JobResult(job_id=j.id, timestamp=datetime.now(timezone.utc),
                             status="failed", error="timeout", summary="Timeout after 480s")

        with patch("jobs.executor._handle_agent_task", side_effect=fake_handle):
            result = ex.execute_job(job, state, feeds=[])
        assert calls["n"] == 1, "non-retryable must NOT retry"
        assert result.status == "failed"

    def test_retryable_job_does_not_retry_auth_failed(self):
        """AC1: auth_failed is NOT a retryable class even when retryable=True
        (it has its own next-tick fast-retry path)."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        job = Job(id="af", name="AuthFail", type="agent_task", schedule="0 * * * *",
                  safety=JobSafety(retryable=True))
        state = SchedulerState()
        calls = {"n": 0}

        def fake_handle(j, s):
            calls["n"] += 1
            return JobResult(job_id=j.id, timestamp=datetime.now(timezone.utc),
                             status="auth_failed", summary="auth pre-check failed")

        with patch("jobs.executor._handle_agent_task", side_effect=fake_handle):
            result = ex.execute_job(job, state, feeds=[])
        assert calls["n"] == 1, "auth_failed must NOT go through the timeout-retry path"
        assert result.status == "auth_failed"

    def test_retry_rechecks_admission_gate(self):
        """Gate-2 O-1: the retry must re-check the RAM/budget/daily gate before a
        second spawn — if the gate declines, NO retry (keep original failure)."""
        from jobs.models import Job, JobResult, JobSafety, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        job = Job(id="rg", name="RetryGate", type="agent_task", schedule="0 * * * *",
                  safety=JobSafety(retryable=True))
        state = SchedulerState()
        calls = {"n": 0}

        def fake_handle(j, s):
            calls["n"] += 1
            return JobResult(job_id=j.id, timestamp=datetime.now(timezone.utc),
                             status="failed", error="timeout", summary="Timeout after 480s")

        # First attempt runs (gate open on call #1), then the RAM gate declines
        # the retry (call #2) → the retry must NOT spawn.
        ram_calls = {"n": 0}
        def fake_ram():
            ram_calls["n"] += 1
            return None if ram_calls["n"] == 1 else "deferred: memory pressure"

        with patch("jobs.executor._handle_agent_task", side_effect=fake_handle), \
             patch("jobs.executor._check_spawn_ram_budget", side_effect=fake_ram):
            result = ex.execute_job(job, state, feeds=[])
        assert calls["n"] == 1, "retry must NOT spawn a second subprocess when the gate declines"
        assert result.status == "failed"


class TestSingleFailureAlert:
    """P0-3: a single true failure writes an alert immediately (not only at
    consecutive_failures>=3), with non-streak text (no 'consecutive'/'cooldown')."""

    def test_single_failure_writes_alert(self, tmp_path):
        """AC2: one failed run → alert entry in JSONL, text does NOT claim a streak."""
        from jobs.models import JobResult, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        jsonl = tmp_path / ".job-results.jsonl"
        state = SchedulerState()
        result = JobResult(job_id="solo", timestamp=datetime.now(timezone.utc),
                           status="failed", error="timeout", summary="Timeout after 480s")
        with patch("jobs.executor.JOB_RESULTS_JSONL", jsonl):
            ex._update_job_state(state, "solo", result)
        assert jsonl.exists(), "single failure must write an alert entry"
        entries = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
        alerts = [e for e in entries if "ALERT" in e.get("job_name", "")]
        assert len(alerts) == 1, "exactly one single-failure alert"
        summary = alerts[0]["summary"].lower()
        assert "consecutive" not in summary, "single-failure text must not claim a streak"
        assert "24h cooldown" not in summary, "single failure is not in cooldown"

    def test_streak_alert_still_fires_at_threshold(self, tmp_path):
        """AC2 (regression): the >=3 streak alert still uses streak text."""
        from jobs.models import JobResult, JobState, SchedulerState
        from datetime import datetime, timezone
        import jobs.executor as ex

        jsonl = tmp_path / ".job-results.jsonl"
        state = SchedulerState()
        state.jobs["strk"] = JobState(consecutive_failures=2)  # this failure makes it 3
        result = JobResult(job_id="strk", timestamp=datetime.now(timezone.utc),
                           status="failed", error="boom", summary="crash")
        with patch("jobs.executor.JOB_RESULTS_JSONL", jsonl):
            ex._update_job_state(state, "strk", result)
        entries = [json.loads(l) for l in jsonl.read_text().splitlines() if l.strip()]
        streak_alerts = [e for e in entries if "consecutive" in e.get("summary", "").lower()]
        assert len(streak_alerts) == 1, "streak alert at threshold=3 must still fire with streak text"
