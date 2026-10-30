"""Headless-job CLI results: an API error is never a success; a too-old CLI never runs.

Covers:
  - ``jobs.cli_result`` pure helpers (version parse, per-model CLI floor, API-error
    classification into auth / transient / error)
  - ``model_registry.min_cli_version_for`` across short / Bedrock / ``[1m]`` forms
  - the job CLI launch sites: ``executor._handle_agent_task`` and
    ``executor._send_slack_dm`` (CLI tier)

Method: drive the REAL handlers with only the leaf boundaries replaced (the CLI
subprocess, credential/auth probes, the estimation learner's file write).
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from jobs.cli_result import cli_api_error, cli_too_old_message, parse_cli_version


# ── pure helpers ────────────────────────────────────────────────────────

class TestPureHelpers:
    def test_parse_cli_version(self):
        assert parse_cli_version("2.1.302 (Claude Code)\n") == (2, 1, 302)
        assert parse_cli_version("garbage") is None
        assert parse_cli_version("") is None

    def test_min_cli_version_for_all_model_forms(self):
        from model_registry import min_cli_version_for

        for form in ("claude-opus-5-5", "us.anthropic.claude-opus-5-5", "us.anthropic.claude-opus-5-5[1m]"):
            assert min_cli_version_for(form) == (2, 1, 280), form
        assert min_cli_version_for("claude-opus-4-8") is None
        assert min_cli_version_for(None) is None

    def test_too_old_message(self):
        msg = cli_too_old_message((2, 1, 190), "us.anthropic.claude-opus-5-5[1m]")
        assert msg and "2.1.190" in msg and "2.1.280" in msg and "claude-opus-5-5" in msg
        assert cli_too_old_message((2, 1, 280), "claude-opus-5-5") is None
        assert cli_too_old_message(None, "claude-opus-5-5") is None, "unknown version must not block"
        assert cli_too_old_message((1, 0, 0), "claude-opus-4-8") is None

    @pytest.mark.parametrize("output,kind", [
        ({"is_error": True, "result": "API Error: 400 Claude Code 2.1.190 does not support this model"}, "error"),
        ({"is_error": False, "result": "API Error: 429 Too many requests"}, "transient"),
        ({"is_error": True, "result": "API Error: 403 The security token included in the request is expired"}, "auth"),
        ({"is_error": True, "result": "", "errors": ["overloaded_error"]}, "transient"),
        ({"is_error": True, "result": "API Error: 403 AccessDeniedException: You don't have access to the model"}, "error"),
        ({"is_error": True, "result": "API Error: 400 Input exceeds 500 tokens"}, "error"),
        ({"is_error": True, "result": "API Error: 503 Service Unavailable"}, "transient"),
        ({"is_error": True, "subtype": "error_max_turns", "result": ""}, "error"),
        ({"is_error": True, "subtype": "error_during_execution", "result": "", "errors": ["API Error: 529 overloaded"]}, "transient"),
        ({"is_error": True, "result": "API Error: Connection error."}, "transient"),
    ])
    def test_api_error_kinds(self, output, kind):
        got = cli_api_error(output)
        assert got is not None and got[0] == kind

    def test_normal_result_is_not_an_error(self):
        assert cli_api_error({"is_error": False, "result": "Done: 3 items"}) is None
        assert cli_api_error({"result": "Report about API Error handling"}) is None
        assert cli_api_error("not a dict") is None


# ── executor._handle_agent_task ─────────────────────────────────────────

def _job():
    from jobs.models import Job
    return Job(id="j", name="J", type="agent_task", schedule="0 * * * *",
               enabled=True, config={"prompt": "hi"})


def _run_agent_task(cli_stdout: dict, *, rc: int = 0, version=(2, 1, 302)):
    from jobs import executor
    from jobs.models import SchedulerState

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=json.dumps(cli_stdout), stderr="")

    learner = MagicMock()
    with patch("jobs.executor._resolve_claude_cli", return_value="/usr/bin/claude"), \
         patch("jobs.executor._check_claude_auth", return_value=None), \
         patch("jobs.executor._get_aws_credentials", return_value={}), \
         patch("jobs.executor._load_mcp_config", return_value={}), \
         patch("jobs.executor._cli_version", return_value=version), \
         patch("jobs.executor._resolve_job_model", return_value="us.anthropic.claude-opus-5-5[1m]"), \
         patch("jobs.executor._get_learner", return_value=learner), \
         patch("jobs.executor._write_job_result", return_value=None), \
         patch("jobs.executor.subprocess.run", side_effect=fake_run):
        result = executor._handle_agent_task(_job(), SchedulerState())
    return result, calls


class TestAgentTask:
    def test_exit0_api_error_is_failed_not_success(self):
        result, _ = _run_agent_task({"is_error": True, "subtype": "success",
                                     "result": "API Error: 400 model not supported"})
        assert result.status == "failed"
        assert "API Error: 400" in result.summary
        assert result.error.startswith("api_error: API Error: 400")

    def test_transient_api_error_is_retryable(self):
        from jobs.executor import _is_retryable_failure

        result, _ = _run_agent_task({"is_error": True, "result": "API Error: 529 overloaded"})
        assert result.status == "failed" and result.error.startswith("api_transient")
        assert _is_retryable_failure(result) is True

    def test_auth_api_error_routes_to_auth_failed(self):
        result, _ = _run_agent_task({"is_error": True, "result": "API Error: 403 security token expired"})
        assert result.status == "auth_failed"

    def test_no_spend_cap_is_passed_and_an_error_result_fails(self):
        # no --max-budget-usd on the command; an error result is a failure, never "partial"
        result, calls = _run_agent_task({"is_error": True, "subtype": "error_max_budget_usd",
                                         "result": "", "num_turns": 4, "total_cost_usd": 1.5})
        assert result.status == "failed"
        assert not any("--max-budget-usd" in c for call in calls for c in call)

    def test_success_still_success(self):
        result, _ = _run_agent_task({"is_error": False, "result": "All good"})
        assert result.status == "success"

    def test_too_old_cli_fails_before_spawning(self):
        result, calls = _run_agent_task({"result": "x"}, version=(2, 1, 190))
        assert result.status == "failed"
        assert "2.1.190" in result.summary and "2.1.280" in result.summary
        assert result.error.startswith("cli_too_old") and "2.1.280" in result.error
        assert not any("--print" in c for c in calls), "must not start the run"


# ── executor._send_slack_dm (CLI tier) ──────────────────────────────────

class TestSlackCliTier:
    def _send(self, stdout: str, version=(2, 1, 302)):
        from jobs import executor

        calls: list[list[str]] = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        with patch("jobs.executor._send_slack_dm_webhook", return_value=False), \
             patch("jobs.executor._send_slack_dm_bot_api", return_value=False), \
             patch("jobs.executor._resolve_claude_cli", return_value="/usr/bin/claude"), \
             patch("jobs.executor._load_mcp_config", return_value={"mcpServers": {"slack-mcp": {}}}), \
             patch("jobs.executor._get_slack_dm_channel", return_value="D123"), \
             patch("jobs.executor._cli_version", return_value=version), \
             patch("jobs.executor._resolve_job_model", return_value="us.anthropic.claude-opus-5-5[1m]"), \
             patch("jobs.executor.subprocess.run", side_effect=fake_run):
            return executor._send_slack_dm("hello"), calls

    def test_api_error_text_is_not_sent(self):
        ok, _ = self._send("API Error: 400 model not supported")
        assert ok is False

    def test_too_old_cli_is_not_run(self):
        ok, calls = self._send("sent", version=(2, 1, 190))
        assert ok is False and not any("--print" in c for c in calls)

    def test_normal_send_still_true(self):
        ok, _ = self._send("Message sent.")
        assert ok is True
