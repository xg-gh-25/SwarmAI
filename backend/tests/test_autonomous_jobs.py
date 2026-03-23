"""Tests for the autonomous jobs API router.

Validates YAML parsing, state merging, status derivation, and the endpoint
returning correct data when the job scheduler directory exists vs doesn't.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from schemas.autonomous_job import AutonomousJobCategory, AutonomousJobResponse, AutonomousJobStatus


# ---------------------------------------------------------------------------
# Import the router module directly to test helpers
# ---------------------------------------------------------------------------

from routers.autonomous_jobs import (
    _derive_status,
    _load_jobs_yaml,
    _load_state,
    _map_category,
    list_autonomous_jobs,
)


# ---------------------------------------------------------------------------
# _derive_status
# ---------------------------------------------------------------------------


class TestDeriveStatus:
    """Status derivation from job definition + runtime state."""

    def test_disabled_job_is_paused(self):
        assert _derive_status({"enabled": False}, None) == AutonomousJobStatus.PAUSED

    def test_enabled_no_state_is_running(self):
        assert _derive_status({"enabled": True}, None) == AutonomousJobStatus.RUNNING

    def test_enabled_missing_key_defaults_running(self):
        assert _derive_status({}, None) == AutonomousJobStatus.RUNNING

    def test_circuit_breaker_3_failures_is_error(self):
        state = {"consecutive_failures": 3, "last_status": "failed"}
        assert _derive_status({"enabled": True}, state) == AutonomousJobStatus.ERROR

    def test_one_failure_stays_running(self):
        """Single transient failure should NOT show error."""
        state = {"consecutive_failures": 1, "last_status": "failed"}
        assert _derive_status({"enabled": True}, state) == AutonomousJobStatus.RUNNING

    def test_two_failures_stays_running(self):
        state = {"consecutive_failures": 2, "last_status": "failed"}
        assert _derive_status({"enabled": True}, state) == AutonomousJobStatus.RUNNING

    def test_healthy_after_recovery(self):
        state = {"consecutive_failures": 0, "last_status": "success"}
        assert _derive_status({"enabled": True}, state) == AutonomousJobStatus.RUNNING


# ---------------------------------------------------------------------------
# _map_category
# ---------------------------------------------------------------------------


class TestMapCategory:
    def test_system(self):
        assert _map_category("system") == AutonomousJobCategory.SYSTEM

    def test_user(self):
        assert _map_category("user") == AutonomousJobCategory.USER_DEFINED

    def test_user_defined(self):
        assert _map_category("user_defined") == AutonomousJobCategory.USER_DEFINED

    def test_unknown_defaults_system(self):
        assert _map_category("whatever") == AutonomousJobCategory.SYSTEM


# ---------------------------------------------------------------------------
# _load_jobs_yaml
# ---------------------------------------------------------------------------


class TestLoadJobsYaml:
    def test_no_files_returns_empty(self, tmp_path):
        with patch("routers.autonomous_jobs._JOBS_FILE", tmp_path / "nope.yaml"), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope2.yaml"):
            assert _load_jobs_yaml() == []

    def test_loads_system_jobs(self, tmp_path):
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(yaml.dump({"jobs": [
            {"id": "j1", "name": "Job 1", "type": "signal_fetch", "schedule": "0 * * * *"},
        ]}))
        with patch("routers.autonomous_jobs._JOBS_FILE", jobs_file), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope.yaml"):
            result = _load_jobs_yaml()
            assert len(result) == 1
            assert result[0]["id"] == "j1"

    def test_deduplicates_across_files(self, tmp_path):
        system = tmp_path / "jobs.yaml"
        user = tmp_path / "user-jobs.yaml"
        system.write_text(yaml.dump({"jobs": [{"id": "dup", "name": "System"}]}))
        user.write_text(yaml.dump({"jobs": [{"id": "dup", "name": "User"}]}))
        with patch("routers.autonomous_jobs._JOBS_FILE", system), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", user):
            result = _load_jobs_yaml()
            assert len(result) == 1
            assert result[0]["name"] == "System"  # system wins

    def test_corrupt_yaml_returns_empty(self, tmp_path):
        bad_file = tmp_path / "jobs.yaml"
        bad_file.write_text(": invalid: yaml: [[[")
        with patch("routers.autonomous_jobs._JOBS_FILE", bad_file), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope.yaml"):
            result = _load_jobs_yaml()
            assert result == []

    def test_skips_jobs_without_id(self, tmp_path):
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(yaml.dump({"jobs": [
            {"name": "No ID"},
            {"id": "ok", "name": "Has ID"},
        ]}))
        with patch("routers.autonomous_jobs._JOBS_FILE", jobs_file), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope.yaml"):
            result = _load_jobs_yaml()
            assert len(result) == 1
            assert result[0]["id"] == "ok"


# ---------------------------------------------------------------------------
# _load_state
# ---------------------------------------------------------------------------


class TestLoadState:
    def test_no_file_returns_empty(self, tmp_path):
        with patch("routers.autonomous_jobs._STATE_FILE", tmp_path / "nope.json"):
            assert _load_state() == {}

    def test_loads_valid_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_data = {"jobs": {"j1": {"total_runs": 5, "last_status": "success"}}}
        state_file.write_text(json.dumps(state_data))
        with patch("routers.autonomous_jobs._STATE_FILE", state_file):
            result = _load_state()
            assert result["jobs"]["j1"]["total_runs"] == 5

    def test_corrupt_json_returns_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not json at all {{{")
        with patch("routers.autonomous_jobs._STATE_FILE", state_file):
            assert _load_state() == {}


# ---------------------------------------------------------------------------
# Full endpoint
# ---------------------------------------------------------------------------


class TestListAutonomousJobs:
    @pytest.mark.asyncio
    async def test_empty_when_no_scheduler(self, tmp_path):
        with patch("routers.autonomous_jobs._JOBS_FILE", tmp_path / "nope.yaml"), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope2.yaml"):
            result = await list_autonomous_jobs()
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_jobs_with_state(self, tmp_path):
        jobs_file = tmp_path / "jobs.yaml"
        jobs_file.write_text(yaml.dump({"jobs": [
            {"id": "sig", "name": "Signal Fetch", "type": "signal_fetch",
             "schedule": "0 8,14,20 * * *", "enabled": True, "category": "system"},
            {"id": "inbox", "name": "Morning Inbox", "type": "agent_task",
             "schedule": "0 0 * * 1-5", "enabled": False, "category": "user"},
        ]}))
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"jobs": {
            "sig": {
                "last_run": "2026-03-23T14:07:19Z",
                "last_status": "success",
                "total_runs": 12,
                "consecutive_failures": 0,
            },
        }}))
        with patch("routers.autonomous_jobs._JOBS_FILE", jobs_file), \
             patch("routers.autonomous_jobs._USER_JOBS_FILE", tmp_path / "nope.yaml"), \
             patch("routers.autonomous_jobs._STATE_FILE", state_file):
            result = await list_autonomous_jobs()
            assert len(result) == 2

            sig = result[0]
            assert sig.id == "sig"
            assert sig.status == AutonomousJobStatus.RUNNING
            assert sig.total_runs == 12
            assert sig.last_run_at == "2026-03-23T14:07:19Z"

            inbox = result[1]
            assert inbox.id == "inbox"
            assert inbox.status == AutonomousJobStatus.PAUSED
            assert inbox.total_runs == 0
