"""Agent jobs nobody watches: the declared tools are the only tools, Bash runs in the CLI
sandbox, the CLI must accept the settings first, and unread safety fields are gone.

Method: drive the REAL ``executor._handle_agent_task`` / ``_send_slack_dm`` with only the
leaf boundaries replaced (the CLI subprocess, auth and credential probes, the platform
and PATH lookups, the learner and result writes), then read the CLI command it built.
What the CLI then enforces was measured on the real CLI 2.1.302 (macOS): denied reads,
denied writes, hidden variables, the settings check; see the run's TEST report.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import protected_paths as pp

_DONE = json.dumps({"is_error": False, "result": "done"})
_DOCTOR_OK = "Claude Code doctor\n\nNo installation issues found.\n"


def _job(**safety):
    from jobs.models import Job, JobSafety
    return Job(id="j", name="J", type="agent_task", schedule="0 * * * *", enabled=True,
               config={"prompt": "hi"}, safety=JobSafety(**safety))


def _run(job, *, system="Darwin", which=lambda name: f"/usr/bin/{name}",
         version=(2, 1, 302), doctor_out=_DOCTOR_OK, mcp=None):
    from jobs import executor
    from jobs.models import SchedulerState

    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        if cmd[-1] == "doctor":
            out = doctor_out(cmd) if callable(doctor_out) else doctor_out
        else:
            out = _DONE
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    with patch("jobs.executor._resolve_claude_cli", return_value="/usr/bin/claude"), \
         patch("jobs.executor._check_claude_auth", return_value=None), \
         patch("jobs.executor._get_aws_credentials", return_value={}), \
         patch("jobs.executor._aws_settings", return_value={}), \
         patch("jobs.executor._load_mcp_config", return_value=mcp or {}), \
         patch("jobs.executor._cli_version", return_value=version), \
         patch("jobs.executor._resolve_job_model", return_value="us.anthropic.claude-opus-5-5[1m]"), \
         patch("jobs.executor._get_learner", return_value=MagicMock()), \
         patch("jobs.executor._write_job_result", return_value=None), \
         patch("jobs.executor.platform.system", return_value=system), \
         patch("jobs.executor.shutil.which", side_effect=which), \
         patch("jobs.executor.subprocess.run", side_effect=fake_run):
        result = executor._handle_agent_task(job, SchedulerState())
    cli = [c for c in calls if c[-1] != "doctor"]
    return result, (cli[0] if cli else None), calls


def _flag(cmd, name):
    return cmd[cmd.index(name) + 1] if name in cmd else None


def _settings(cmd):
    return json.loads(_flag(cmd, "--settings"))


def test_declared_builtin_tools_become_tools_flag():
    _, cmd, _ = _run(_job(allowed_tools=["Read", "Bash(git *)", "mcp__slack-mcp__post_message"]))
    assert _flag(cmd, "--tools") == "Read,Bash"  # the only built-in tools it has
    assert "mcp__slack-mcp__post_message" in _flag(cmd, "--allowedTools")
    _, cmd, _ = _run(_job(allowed_tools=["mcp__slack-mcp__post_message"]))
    assert _flag(cmd, "--tools") == ""  # MCP only: no built-in tool at all
    _, cmd, _ = _run(_job())
    assert "--tools" not in cmd  # nothing declared: the CLI's default set ...
    assert _settings(cmd)["sandbox"]["enabled"] is True  # ... still sandboxed


def test_slack_dm_spawn_has_no_builtin_tools():
    from jobs import executor
    calls: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_DONE, stderr="")

    with patch("jobs.executor._send_slack_dm_webhook", return_value=False), \
         patch("jobs.executor._send_slack_dm_bot_api", return_value=False), \
         patch("jobs.executor._resolve_claude_cli", return_value="/usr/bin/claude"), \
         patch("jobs.executor._load_mcp_config", return_value={"mcpServers": {"slack-mcp": {}}}), \
         patch("jobs.executor._get_slack_dm_channel", return_value="D123"), \
         patch("jobs.executor._cli_version", return_value=(2, 1, 302)), \
         patch("jobs.executor._resolve_job_model", return_value="us.anthropic.claude-opus-5-5[1m]"), \
         patch("jobs.executor.subprocess.run", side_effect=fake_run):
        executor._send_slack_dm("hello")
    cmd = calls[-1]
    assert _flag(cmd, "--tools") == ""


def test_every_agent_task_settings_enable_sandbox():
    _, cmd, _ = _run(_job(allowed_tools=["Bash", "Read"]))
    s = _settings(cmd)
    sb = s["sandbox"]
    assert sb["enabled"] is True and sb["allowUnsandboxedCommands"] is False
    deny = set(s["permissions"]["deny"])
    creds = pp.credential_rules()
    assert {f"Read({r})" for r in creds} <= deny  # credential stores unreadable ...
    assert {f"Edit({r})" for r in creds} <= deny  # ... and unwritable
    assert {f"Edit({r})" for r in pp.runs_later_rules()} <= deny  # nothing that runs later
    assert {f"Edit({r})" for r in pp.protected_write_rules()} <= deny
    import tempfile
    from jobs.paths import SWARMWS
    # the workspace and the temp dir only: never the app data dir (skills, run worktrees)
    assert sb["filesystem"]["allowWrite"] == [str(SWARMWS), tempfile.gettempdir()]
    hidden = {e["name"] for e in sb["credentials"]["envVars"] if e["mode"] == "deny"}
    assert set(pp.CREDENTIAL_ENV_VARS) <= hidden
    assert {"AWS_SECRET_ACCESS_KEY", "AWS_BEARER_TOKEN_BEDROCK", "GH_TOKEN"} <= hidden


def test_file_tools_cannot_write_the_app_data_dir():
    """Write/Edit obey only deny rules (the sandbox's allowWrite binds Bash alone), so a
    job's deny list covers every entry of the app data dir but the workspace."""
    from config import get_app_data_dir, get_swarmws
    data = Path(get_app_data_dir())
    Path(get_swarmws()).mkdir(parents=True, exist_ok=True)  # the workspace is one of its entries
    (data / "skills" / "x").mkdir(parents=True, exist_ok=True)
    (data / "user-mcp-servers.json").write_text("{}")
    _, cmd, _ = _run(_job(allowed_tools=["Read", "Write"]))
    deny = set(_settings(cmd)["permissions"]["deny"])
    assert f"Edit({pp.rule_path(data / 'skills')}/**)" in deny
    assert f"Edit({pp.rule_path(data / 'user-mcp-servers.json')})" in deny
    assert "Edit(~/.claude/**)" in deny
    ws = pp.rule_path(Path(get_swarmws()))
    assert f"Edit({ws}/**)" not in deny and f"Edit({ws})" not in deny  # the workspace stays writable


def test_allow_network_shapes_sandbox_network():
    _, cmd, _ = _run(_job(allowed_tools=["Bash"], allow_network=False))
    assert _settings(cmd)["sandbox"]["network"] == {"allowedDomains": [], "strictAllowlist": True}
    _, cmd, _ = _run(_job(allowed_tools=["Bash"], allow_network=True))
    assert _settings(cmd)["sandbox"]["network"] == {"allowedDomains": ["*"]}


def test_no_sandbox_platform_withholds_bash():
    for kwargs, why in (({"system": "Windows"}, "Windows"),
                        ({"system": "Linux", "which": lambda name: None}, "bubblewrap"),
                        ({"version": (2, 1, 290)}, "older than 2.1.301"),
                        ({"version": None}, "older than 2.1.301")):
        result, cmd, _ = _run(_job(allowed_tools=["Bash", "Read"]), **kwargs)
        assert _flag(cmd, "--disallowedTools") == "Bash", kwargs
        assert "sandbox" not in _settings(cmd), kwargs
        assert result.summary.startswith("Bash withheld:") and why in result.summary, result.summary
    # a job without Bash needs no note
    result, cmd, _ = _run(_job(allowed_tools=["Read"]), system="Windows")
    assert "--disallowedTools" not in cmd and not result.summary.startswith("Bash withheld")
    # on a platform that can sandbox, Bash stays
    result, cmd, _ = _run(_job(allowed_tools=["Bash"]))
    assert "--disallowedTools" not in cmd and result.status == "success"


def test_rejected_settings_fail_the_job_before_it_runs():
    def doctor(cmd):
        name = Path(cmd[cmd.index("--settings") + 1]).name
        return ("Claude Code doctor\n\nInvalid settings\n"
                f"- /private/tmp/{name} › sandbox.network.tlsTerminate: Expected object\n\nRemote")
    result, cmd, calls = _run(_job(allowed_tools=["Bash"]), doctor_out=doctor)
    assert cmd is None and len(calls) == 1  # only the check ran, never the job
    assert result.status == "failed"
    assert result.error.startswith("cli_settings_rejected:") and "tlsTerminate" in result.error


def test_settings_check_fails_closed():
    from jobs import executor

    def check(stdout, rc=0):
        proc = subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")
        with patch("jobs.executor.subprocess.run", return_value=proc):
            return executor._cli_settings_problem("/x/claude", {}, {})

    with patch("jobs.executor.subprocess.run", side_effect=OSError("no such file")):
        assert "could not run" in executor._cli_settings_problem("/x/claude", {}, {})
    assert check(_DOCTOR_OK) is None
    assert "exited 1" in check(_DOCTOR_OK, rc=1)
    assert "nothing it could be read from" in check("")  # empty or reworded output
    # a problem in the person's own settings file never blocks a job
    other = "Claude Code doctor\n\nInvalid settings\n- /Users/x/.claude/settings.json › hooks: bad\n\n"
    assert check(other) is None
    # colour codes do not hide a problem in the job's own settings
    def ansi(cmd, *a, **kw):
        name = Path(cmd[cmd.index("--settings") + 1]).name
        return subprocess.CompletedProcess(cmd, 0, stdout=(
            "\x1b[1mClaude Code doctor\x1b[0m\n\n\x1b[31mInvalid settings\x1b[0m\n"
            f"- /tmp/{name} › sandbox: bad\n"), stderr="")
    with patch("jobs.executor.subprocess.run", side_effect=ansi):
        assert "sandbox: bad" in executor._cli_settings_problem("/x/claude", {}, {})


def test_only_named_mcp_servers_start():
    mcp = {"mcpServers": {"slack-mcp": {"command": "s"}, "builder-mcp": {"command": "b"}}}
    seen = {}
    real_dump = json.dump

    def capture(obj, fp, *a, **kw):
        if isinstance(obj, dict) and "mcpServers" in obj:
            seen["servers"] = sorted(obj["mcpServers"])
        return real_dump(obj, fp, *a, **kw)

    with patch("jobs.executor.json.dump", side_effect=capture):
        _, cmd, _ = _run(_job(allowed_tools=["Read", "mcp__slack-mcp__post_message"]), mcp=mcp)
    assert seen["servers"] == ["slack-mcp"] and "--mcp-config" in cmd
    seen.clear()
    with patch("jobs.executor.json.dump", side_effect=capture):
        _, cmd, _ = _run(_job(allowed_tools=["Read"]), mcp=mcp)
    assert "servers" not in seen and "--mcp-config" not in cmd  # names none: none start
    assert "--strict-mcp-config" in cmd  # never the user's own MCP config


def test_retired_safety_keys_warn(tmp_path, caplog, monkeypatch):
    from jobs import scheduler
    jobs_file = tmp_path / "user-jobs.yaml"
    jobs_file.write_text(
        "jobs:\n- id: old\n  name: Old\n  type: agent_task\n  schedule: 0 * * * *\n"
        "  config: {prompt: hi}\n  safety: {allow_write: true, allow_send: false, allowed_tools: [Read]}\n")
    monkeypatch.setattr(scheduler, "USER_JOBS_FILE", jobs_file, raising=False)
    with caplog.at_level(logging.WARNING, logger=scheduler.logger.name):
        jobs = scheduler.load_jobs()
    old = [j for j in jobs if j.id == "old"]
    assert old and old[0].safety.allowed_tools == ["Read"]
    assert not hasattr(old[0].safety, "allow_write")
    msgs = [r.getMessage() for r in caplog.records if "'old'" in r.getMessage()]
    assert msgs and "safety.allow_write" in msgs[0] and "safety.allow_send" in msgs[0]


def test_executor_comments_name_tools_and_sandbox():
    src = (Path(__file__).resolve().parents[1] / "jobs" / "executor.py").read_text()
    assert "Safety is enforced by --allowedTools" not in src
    assert "Per-job tool restriction via --allowedTools" not in src
