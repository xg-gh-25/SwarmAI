"""Background jobs and the daemon behave the same for every user and platform.

Questions:
- Does a headless CLI job (agent_task, CDF's S7 review) carry the saved Bedrock API key in
  API-key mode, without a chat session having put it into os.environ, and without the
  machine's sigv4 credentials? Does the AWS path stay as it was?
- Does the login-PATH probe use the person's own shell (never a hard-coded zsh), skip
  Windows, and leave PATH alone when the probe fails?
- Do the feature-map modules import where the Unix-only ``pwd`` module does not exist?
- Does the scheduler uninstall stay off launchctl outside macOS, and still use it on macOS?
- Does the workspace sentinel name every governed context file the prompt builder loads,
  and is a new Brain's AGENTS.md template plain English?
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core.app_config_manager import AppConfigManager
from jobs import executor

KEY = "ABSKexample-bedrock-api-key"
SIGV4 = {"AWS_ACCESS_KEY_ID": "ASIAMACHINE", "AWS_SECRET_ACCESS_KEY": "secret",
         "AWS_SESSION_TOKEN": "token"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from core import aws_account as aa
    mgr = AppConfigManager(config_path=tmp_path / "config.json")
    monkeypatch.setattr(AppConfigManager, "_instance", mgr)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    # a restarted daemon: no chat spawn has exported the key yet
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    for k in SIGV4:
        monkeypatch.delenv(k, raising=False)
    aa._reset_cache()
    yield mgr
    aa._reset_cache()


# ── 1. the Bedrock API key in the job CLI's environment ──────────────────────────

def test_bedrock_key_mode_sets_bearer_and_skips_sigv4(cfg):
    cfg.update({"auth_method": "bedrock_api_key", "aws_bearer_token_bedrock": KEY})
    env = executor._build_cli_env(dict(SIGV4))
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == KEY
    for k in SIGV4:
        assert k not in env
    assert "AWS_BEARER_TOKEN_BEDROCK" not in os.environ  # only the child gets it


def test_bedrock_key_mode_without_a_saved_key_keeps_the_old_path(cfg):
    cfg.update({"auth_method": "bedrock_api_key"})
    env = executor._build_cli_env(dict(SIGV4))
    assert "AWS_BEARER_TOKEN_BEDROCK" not in env
    assert env["AWS_ACCESS_KEY_ID"] == "ASIAMACHINE"


def test_aws_mode_injects_sigv4_without_bearer(cfg):
    cfg.update({"auth_method": "ada", "aws_bearer_token_bedrock": KEY})
    env = executor._build_cli_env(dict(SIGV4))
    assert "AWS_BEARER_TOKEN_BEDROCK" not in env
    assert {k: env[k] for k in SIGV4} == SIGV4


def test_key_mode_gets_no_credential_export_even_with_an_account(cfg, monkeypatch):
    """Chat leaves awsCredentialExport out on an API key; a job and the S7 review too."""
    from core import aws_account as aa
    from core.ingestion.sources import s7_review
    monkeypatch.setattr(aa, "cli_settings", lambda *a, **k: {"awsCredentialExport": "x"})
    cfg.update({"auth_method": "ada"})
    assert executor._aws_settings_args()[0] == "--settings"
    cfg.update({"auth_method": "bedrock_api_key", "aws_bearer_token_bedrock": KEY})
    assert executor._aws_settings_args() == []
    monkeypatch.setattr(executor, "_resolve_claude_cli", lambda: None)
    opts = s7_review.review_options(None, "/tmp", {"type": "object"}, fork=False, model=None)
    assert opts.settings is None
    assert opts.env["AWS_BEARER_TOKEN_BEDROCK"] == KEY and "AWS_ACCESS_KEY_ID" not in opts.env


def test_auth_precheck_carries_bedrock_key(cfg, monkeypatch):
    cfg.update({"auth_method": "bedrock_api_key", "aws_bearer_token_bedrock": KEY})
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return SimpleNamespace(returncode=0, stdout='{"loggedIn": true}', stderr="")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    assert executor._check_claude_auth("/x/claude") is None
    assert seen["env"]["AWS_BEARER_TOKEN_BEDROCK"] == KEY


# ── 2. the login-shell PATH probe ─────────────────────────────────────────────

def _shell_run(path_line: str, calls: list, returncode: int = 0):
    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = f"motd banner\n{executor._LOGIN_SHELL_MARK}{path_line}\n" if path_line else ""
        return SimpleNamespace(returncode=returncode, stdout=out, stderr="")
    return fake_run


@pytest.fixture
def posix(monkeypatch):
    monkeypatch.setattr(executor.sys, "platform", "linux")


def test_probe_uses_user_shell_not_zsh(posix, monkeypatch, tmp_path):
    bash = tmp_path / "bash"
    bash.write_text("#!/bin/sh\n")
    bash.chmod(0o755)
    monkeypatch.setenv("SHELL", str(bash))
    monkeypatch.setenv("PATH", "/inherited/bin:/usr/bin")
    calls: list = []
    monkeypatch.setattr(executor.subprocess, "run", _shell_run("/login/bin:/usr/bin", calls))
    executor._fix_path_from_login_shell()
    assert calls and calls[0][0] == str(bash)
    assert all("zsh" not in c[0] for c in calls)
    # the login PATH first, then what the daemon inherited and the login PATH lacks
    assert os.environ["PATH"] == "/login/bin:/usr/bin:/inherited/bin"


@pytest.mark.parametrize("failure", ["raises", "timeout", "nonzero", "empty", "unmarked"])
def test_failed_probe_keeps_path(posix, monkeypatch, failure):
    monkeypatch.setattr(executor, "_login_shells", lambda: ["/bin/sh"])
    monkeypatch.setenv("PATH", "/keep/bin")

    def fake_run(cmd, **kw):
        if failure == "raises":
            raise OSError("no such shell")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 10)
        if failure == "nonzero":
            return SimpleNamespace(returncode=1,
                                   stdout=f"{executor._LOGIN_SHELL_MARK}/other/bin\n", stderr="")
        if failure == "empty":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="/other/bin\n", stderr="")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    executor._fix_path_from_login_shell()
    assert os.environ["PATH"] == "/keep/bin"


def test_windows_skips_probe(monkeypatch):
    monkeypatch.setattr(executor.sys, "platform", "win32")
    monkeypatch.setenv("PATH", r"C:\keep")
    calls: list = []
    monkeypatch.setattr(executor.subprocess, "run", _shell_run("/login/bin", calls))
    executor._fix_path_from_login_shell()
    assert calls == [] and os.environ["PATH"] == r"C:\keep"
    assert executor._login_shells() == []


def test_shell_fallback_order(posix, monkeypatch, tmp_path):
    existing = {"/bin/bash", "/bin/sh"}
    monkeypatch.setattr(executor.os.path, "isfile", lambda p: p in existing)
    monkeypatch.setattr(executor.os, "access", lambda p, mode: p in existing)
    monkeypatch.setattr(executor, "_account_login_shell", lambda: None)
    monkeypatch.setenv("SHELL", "/no/such/zsh")
    assert executor._login_shells() == ["/bin/bash", "/bin/sh"]
    monkeypatch.delenv("SHELL")
    assert executor._login_shells() == ["/bin/bash", "/bin/sh"]
    # the account's login shell comes before the fixed fall-backs, each shell once
    existing.add("/usr/bin/fish")
    monkeypatch.setattr(executor, "_account_login_shell", lambda: "/usr/bin/fish")
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    assert executor._login_shells() == ["/usr/bin/fish", "/bin/bash", "/bin/sh"]
    monkeypatch.setattr(executor, "_account_login_shell", lambda: None)
    monkeypatch.delenv("SHELL")
    existing.discard("/bin/bash")
    assert executor._login_shells() == ["/bin/sh"]
    existing.clear()
    assert executor._login_shells() == []
    calls: list = []
    monkeypatch.setattr(executor.subprocess, "run", _shell_run("/login/bin", calls))
    monkeypatch.setenv("PATH", "/keep/bin")
    executor._fix_path_from_login_shell()
    assert calls == [] and os.environ["PATH"] == "/keep/bin"  # no shell: nothing runs


def test_a_shell_that_cannot_answer_hands_over_to_the_next(posix, monkeypatch):
    """A $SHELL that rejects -lic (csh) or prints no value: the next shell is asked."""
    monkeypatch.setattr(executor, "_login_shells", lambda: ["/bin/csh", "/bin/bash"])
    monkeypatch.setenv("PATH", "/keep/bin")
    calls: list = []

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "/bin/csh":
            return SimpleNamespace(returncode=1, stdout="", stderr="Unknown option: -lic")
        # a startup file printed without a trailing newline: the marker is mid-line
        return SimpleNamespace(returncode=0,
                               stdout=f"welcome{executor._LOGIN_SHELL_MARK}/login/bin\n",
                               stderr="")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    executor._fix_path_from_login_shell()
    assert calls == ["/bin/csh", "/bin/bash"]
    assert os.environ["PATH"] == "/login/bin:/keep/bin"


def test_resolve_claude_cli_uses_login_shell(posix, monkeypatch, tmp_path):
    cli = tmp_path / "claude"
    cli.write_text("")
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    monkeypatch.setattr(executor, "_login_shells", lambda: ["/bin/bash"])
    calls: list = []
    monkeypatch.setattr(executor.subprocess, "run", _shell_run(str(cli), calls))
    assert executor._resolve_claude_cli() == str(cli)
    assert calls[0][0] == "/bin/bash"
    calls.clear()
    monkeypatch.setattr(executor.subprocess, "run", _shell_run(str(tmp_path / "missing"), calls))
    assert executor._resolve_claude_cli() is None


# ── 3. the feature-map modules without pwd (Windows) ─────────────────────────────

def test_feature_map_imports_without_pwd():
    """In a fresh interpreter (re-importing in this one would leave other tests holding
    stale module copies): with ``pwd`` absent, as on Windows, the modules import."""
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "sys.modules['pwd'] = None\n"  # `import pwd` now raises ImportError
        "import core.feature_map.replay_user as ru\n"
        "import core.feature_map.sandbox, core.feature_map.replay\n"
        "assert ru._user_exists() is False\n"
        "print('ok')\n")
    env = {**os.environ, "PYTHONPATH": str(backend)}
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(backend), env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "ok", proc.stderr[-2000:]


# ── 4. the scheduler uninstall outside macOS ─────────────────────────────────────

def test_uninstall_noop_off_darwin(monkeypatch, tmp_path):
    from jobs import install_scheduler as ins
    calls: list = []
    monkeypatch.setattr(ins.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(ins, "LAUNCH_AGENTS", tmp_path)
    for platform in ("linux", "win32"):
        monkeypatch.setattr(ins.sys, "platform", platform)
        assert ins.uninstall() == "not_applicable"
    assert calls == []


def test_uninstall_darwin_still_boots_out(monkeypatch, tmp_path):
    from jobs import install_scheduler as ins
    calls: list = []
    monkeypatch.setattr(ins.subprocess, "run", lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(ins, "LAUNCH_AGENTS", tmp_path)
    monkeypatch.setattr(ins, "_uid", lambda: 501, raising=False)
    monkeypatch.setattr(ins.sys, "platform", "darwin")
    plist = tmp_path / f"{ins.NEW_LABEL}.plist"
    plist.write_text("<plist/>")
    assert ins.uninstall() == "removed"
    assert calls == [["launchctl", "bootout", f"gui/501/{ins.NEW_LABEL}"]]
    assert not plist.exists()
    assert ins.uninstall() == "not_installed"


def test_uninstall_cleanup_reports_what_the_scheduler_step_did(monkeypatch, tmp_path):
    """/api/system/uninstall-cleanup records the step's own status, never a fixed
    "removed" when nothing ran (Windows, Linux)."""
    import asyncio
    from unittest.mock import AsyncMock
    from jobs import install_scheduler as ins
    from core import service_manager as sm
    from routers import system
    monkeypatch.setattr(ins, "uninstall", lambda: "not_applicable")
    monkeypatch.setattr(sm.service_manager, "stop_all", AsyncMock())
    monkeypatch.setattr(system, "PORT_FILE", tmp_path / "backend.port")
    out = asyncio.run(system.uninstall_cleanup())
    assert out["details"]["scheduler_plist"] == "not_applicable"
    assert out["details"]["port_file"] == "already gone"   # there was none
    (tmp_path / "backend.port").write_text("18321")
    out = asyncio.run(system.uninstall_cleanup())
    assert out["details"]["port_file"] == "removed"
    assert not (tmp_path / "backend.port").exists()


def test_uninstall_cleanup_never_returns_internal_error_text(monkeypatch, tmp_path):
    """A failing step is reported as "error"; its exception text (paths, internals)
    stays in the log and never reaches the client (secure-coding baseline: generic
    client errors)."""
    import asyncio
    from unittest.mock import AsyncMock
    from jobs import install_scheduler as ins
    from core import service_manager as sm
    from routers import system

    def boom():
        raise RuntimeError("/Users/someone/Library/LaunchAgents/secret.plist denied")

    monkeypatch.setattr(ins, "uninstall", boom)
    monkeypatch.setattr(sm.service_manager, "stop_all",
                        AsyncMock(side_effect=RuntimeError("internal service detail")))
    monkeypatch.setattr(system, "PORT_FILE", tmp_path / "backend.port")
    out = asyncio.run(system.uninstall_cleanup())
    assert out["details"]["scheduler_plist"] == "error"
    assert out["details"]["services"] == "error"
    assert "secret.plist" not in str(out) and "internal service detail" not in str(out)


# ── 5. the sentinel and the new-Brain template ───────────────────────────────────

def test_sentinel_names_every_governed_file():
    from core.context_directory_loader import CONTEXT_FILES
    from core.swarm_workspace_manager import AI_INSTRUCTION_SENTINEL
    text = " ".join(AI_INSTRUCTION_SENTINEL.split())
    listed = re.search(r"governed context files \(([^)]*)\)", text)
    assert listed, text
    names = [n.strip() for n in listed.group(1).split("/")]
    assert names == [spec.filename.removesuffix(".md") for spec in CONTEXT_FILES]
    assert "TIMELINE" in names


def test_new_brain_agents_template_is_english():
    from core import swarm_workspace_manager as swm
    body = swm.SECTION_SCAFFOLD["AGENTS.md"]
    assert not re.search(r"[\u3000-\u9fff\uff00-\uffef]", body), \
        re.findall(r".{0,40}[\u3000-\u9fff].{0,40}", body)
    assert "never contains the asset itself and never runs its pipeline" in body
