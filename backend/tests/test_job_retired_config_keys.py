"""What the job system does with the pieces the Inbox replaced (design
docs/design/2026-11-20-inbox.md §10).

- An agent_task whose MCP auth fails returns auth_failed: no ``fallback_script`` runs.
- A user job whose config still carries a retired key (``fallback_script``,
  ``fallback_cwd``, ``output_as_signals``) loads with one warning naming the job and the
  key; the key is ignored, never silently dropped.
- A user job that names a removed job type (signal_fetch / signal_digest / self_tune) is
  skipped with a warning; the other jobs load.
- A state.json written before the change (with the signal buffer) still loads.
- A ``type: notify`` job still sends its static message; ``source: signal_digest``
  formats nothing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import yaml

import jobs.scheduler as sch
from jobs.models import Job, JobResult, JobSafety, SchedulerState


@pytest.fixture
def user_jobs(tmp_path, monkeypatch):
    path = tmp_path / "user-jobs.yaml"
    monkeypatch.setattr(sch, "USER_JOBS_FILE", path)
    monkeypatch.setattr("core.app_role.is_worker", lambda: False)

    def write(jobs: list[dict]) -> None:
        path.write_text(yaml.safe_dump({"jobs": jobs}), encoding="utf-8")
    return write


def _agent_job(config: dict) -> Job:
    return Job(id="watch", name="Watch", type="agent_task", schedule="0 * * * *",
               category="user", config={"prompt": "p", **config},
               safety=JobSafety(timeout_seconds=30))


def test_auth_failure_runs_no_fallback_script():
    from jobs.executor import execute_job

    job = _agent_job({"fallback_script": "echo fallback", "fallback_cwd": "/tmp"})
    auth_failed = JobResult(job_id="watch", timestamp=datetime.now(timezone.utc),
                            status="auth_failed", summary="MCP auth expired", duration_seconds=1)
    with patch("jobs.executor._handle_agent_task", return_value=auth_failed), \
         patch("jobs.executor._handle_script") as script, \
         patch("jobs.executor._check_daily_agent_limit", return_value=None), \
         patch("jobs.executor._check_spawn_ram_budget", return_value=None), \
         patch("jobs.executor._update_job_state"), \
         patch("jobs.executor.send_post_job_notification"):
        result = execute_job(job, SchedulerState())
    script.assert_not_called()
    assert result.status == "auth_failed"
    assert result.job_id == "watch"


def test_retired_key_warns_once_and_keeps_job(user_jobs, caplog):
    user_jobs([{"id": "watch", "name": "Watch", "type": "agent_task", "schedule": "0 * * * *",
                "config": {"prompt": "p", "fallback_script": "python -m x"}}])
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.scheduler"):
        jobs = sch.load_jobs()
    job = next(j for j in jobs if j.id == "watch")
    assert job.config["prompt"] == "p"
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "watch" in r.getMessage()]
    assert len(warnings) == 1
    assert "fallback_script" in warnings[0]


def test_job_without_retired_keys_logs_nothing(user_jobs, caplog):
    user_jobs([{"id": "plain", "name": "Plain", "type": "script", "schedule": "0 * * * *",
                "config": {"command": "true"}}])
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.scheduler"):
        jobs = sch.load_jobs()
    assert any(j.id == "plain" for j in jobs)
    assert not [r for r in caplog.records if "plain" in r.getMessage()]


@pytest.mark.parametrize("removed", ["signal_fetch", "signal_digest", "self_tune"])
def test_removed_job_type_is_skipped(user_jobs, caplog, removed):
    user_jobs([{"id": "old", "name": "Old", "type": removed, "schedule": "0 * * * *"},
               {"id": "keep", "name": "Keep", "type": "script", "schedule": "0 * * * *",
                "config": {"command": "true"}}])
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.scheduler"):
        ids = {j.id for j in sch.load_jobs()}
    assert "keep" in ids and "old" not in ids
    assert any("old" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_job_after_a_missing_job_warns(user_jobs, caplog):
    user_jobs([{"id": "after-digest", "name": "After", "type": "script", "schedule": "after:signal-digest",
                "config": {"command": "true"}},
               {"id": "after-ok", "name": "Ok", "type": "script", "schedule": "after:weekly-maintenance",
                "config": {"command": "true"}}])
    with caplog.at_level(logging.WARNING, logger="swarm.jobs.scheduler"):
        ids = {j.id for j in sch.load_jobs()}
    assert {"after-digest", "after-ok"} <= ids
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("after-digest" in m and "signal-digest" in m for m in msgs)
    assert not any("after-ok" in m for m in msgs)


def test_no_signal_system_jobs():
    from jobs.system_jobs import SYSTEM_JOB_IDS
    assert SYSTEM_JOB_IDS.isdisjoint(
        {"signal-fetch", "signal-digest", "weekly-rollup", "signal-notify-slack", "self-tune"})


def test_old_state_with_signal_buffer_loads(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "jobs": {"weekly-maintenance": {"last_status": "success", "total_runs": 3}},
        "raw_signals": [{"feed_id": "f", "title": "t", "url": "https://x"}],
        "dedup_cache": ["https://x"],
    }), encoding="utf-8")
    monkeypatch.setattr(sch, "STATE_FILE", path)
    state = sch.load_state()
    assert state.jobs["weekly-maintenance"].total_runs == 3
    assert "raw_signals" not in state.model_dump()


def _notify_job(config: dict) -> Job:
    return Job(id="ping", name="Ping", type="notify", schedule="0 * * * *",
               config={"channel": "slack", **config})


def test_notify_sends_its_static_message():
    from jobs.executor import _handle_notify
    with patch("skills.s_notify.notify.load_notify_config",
               return_value={"channels": {"slack": {"enabled": True}}}), \
         patch("jobs.executor._send_slack_dm", return_value=True) as send:
        result = _handle_notify(_notify_job({"message": "hello"}), SchedulerState())
    send.assert_called_once_with("hello")
    assert result.status == "success"


def test_notify_signal_digest_source_formats_nothing():
    import jobs.executor as ex
    assert not hasattr(ex, "_format_signal_digest_message")
    with patch("skills.s_notify.notify.load_notify_config",
               return_value={"channels": {"slack": {"enabled": True}}}), \
         patch("jobs.executor._send_slack_dm", return_value=True) as send:
        result = ex._handle_notify(_notify_job({"source": "signal_digest"}), SchedulerState())
    send.assert_not_called()
    assert result.status == "skipped"
