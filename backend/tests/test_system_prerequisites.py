"""Prerequisite checks (git, and Git Bash on Windows) and their place in
/api/system/status.

The checks never touch the real machine here: platform, PATH lookup, file
existence and the xcode-select call are all mocked.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def prereq():
    from core import prerequisites
    return prerequisites


def _by_name(items):
    return {item["name"]: item for item in items}


def _no_subprocess(*_a, **_k):
    raise AssertionError("no subprocess expected here")


# ── git ─────────────────────────────────────────────────────────────


def test_git_found_on_linux_is_ok(prereq, monkeypatch):
    monkeypatch.setattr(prereq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run", _no_subprocess)
    items = prereq.check_prerequisites()
    assert [i["name"] for i in items] == ["git"]
    assert items[0]["ok"] is True


@pytest.mark.parametrize("system, needle", [
    ("Darwin", "xcode-select --install"),
    ("Windows", "https://git-scm.com/download/win"),
    ("Linux", "package manager"),
])
def test_missing_git_hint_per_os(prereq, monkeypatch, system, needle):
    monkeypatch.setattr(prereq.platform, "system", lambda: system)
    monkeypatch.setattr(prereq.shutil, "which", lambda name: None)
    monkeypatch.setattr(prereq, "_is_file", lambda p: True)
    git = _by_name(prereq.check_prerequisites())["git"]
    assert git["ok"] is False
    assert needle in git["hint"]


def test_macos_clt_stub_counts_as_missing(prereq, monkeypatch):
    """F2: /usr/bin/git exists but the Command Line Tools are not installed."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=2, stdout="", stderr="unable to get active developer directory")

    monkeypatch.setattr(prereq.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run", fake_run)
    git = _by_name(prereq.check_prerequisites())["git"]
    assert git["ok"] is False
    assert git["hint"] == "Run: xcode-select --install"
    # constant argv, no shell, bounded
    argv, kwargs = calls[0]
    assert isinstance(argv, list) and argv[-1] == "-p" and argv[0].endswith("xcode-select")
    assert not kwargs.get("shell")
    assert kwargs.get("timeout") == 5


def test_macos_clt_installed_is_ok(prereq, monkeypatch):
    monkeypatch.setattr(prereq.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run",
                        lambda argv, **k: SimpleNamespace(returncode=0, stdout="/Library/Developer/CommandLineTools\n", stderr=""))
    assert _by_name(prereq.check_prerequisites())["git"]["ok"] is True


def test_macos_non_system_git_skips_xcode_select(prereq, monkeypatch):
    monkeypatch.setattr(prereq.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/opt/homebrew/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run", _no_subprocess)
    assert _by_name(prereq.check_prerequisites())["git"]["ok"] is True


def test_xcode_select_timeout_is_unknown_not_raised(prereq, monkeypatch):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(prereq.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run", slow)
    git = _by_name(prereq.check_prerequisites())["git"]
    assert git["ok"] is None
    assert "timed out" in git["hint"]


def test_a_check_that_errors_is_unknown_not_raised(prereq, monkeypatch):
    def boom(name):
        raise RuntimeError("PATH lookup broke")

    monkeypatch.setattr(prereq.platform, "system", lambda: "Linux")
    monkeypatch.setattr(prereq.shutil, "which", boom)
    git = _by_name(prereq.check_prerequisites())["git"]
    assert git["ok"] is None
    assert git["hint"] == "Could not check git"  # the detail is logged, not sent to the client


# ── Git Bash (Windows only) ────────────────────────────────────────


def _windows(prereq, monkeypatch, existing, env=None):
    monkeypatch.setattr(prereq.platform, "system", lambda: "Windows")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: None)
    monkeypatch.setattr(prereq, "_is_file", lambda p: p in existing)
    for key in ("CLAUDE_CODE_GIT_BASH_PATH", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)


def test_git_bash_item_only_on_windows(prereq, monkeypatch):
    for system in ("Darwin", "Linux"):
        monkeypatch.setattr(prereq.platform, "system", lambda s=system: s)
        monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/local/bin/git")
        assert "git_bash" not in _by_name(prereq.check_prerequisites())


def test_git_bash_present_in_program_files(prereq, monkeypatch):
    _windows(prereq, monkeypatch, {r"C:\Program Files\Git\bin\bash.exe"})
    bash = _by_name(prereq.check_prerequisites())["git_bash"]
    assert bash["ok"] is True


def test_git_bash_present_in_local_appdata(prereq, monkeypatch):
    local = r"C:\Users\u\AppData\Local"
    _windows(prereq, monkeypatch, {local + r"\Programs\Git\bin\bash.exe"}, env={"LOCALAPPDATA": local})
    assert _by_name(prereq.check_prerequisites())["git_bash"]["ok"] is True


def test_git_bash_from_env_var(prereq, monkeypatch):
    custom = r"D:\tools\Git\bin\bash.exe"
    _windows(prereq, monkeypatch, {custom}, env={"CLAUDE_CODE_GIT_BASH_PATH": custom})
    assert _by_name(prereq.check_prerequisites())["git_bash"]["ok"] is True


def test_git_bash_missing_names_the_env_var(prereq, monkeypatch):
    _windows(prereq, monkeypatch, set())
    bash = _by_name(prereq.check_prerequisites())["git_bash"]
    assert bash["ok"] is False
    assert "CLAUDE_CODE_GIT_BASH_PATH" in bash["hint"]


def test_git_bash_env_var_pointing_nowhere_is_missing(prereq, monkeypatch):
    _windows(prereq, monkeypatch, {r"C:\Program Files\Git\bin\bash.exe"},
             env={"CLAUDE_CODE_GIT_BASH_PATH": r"D:\gone\bash.exe"})
    bash = _by_name(prereq.check_prerequisites())["git_bash"]
    assert bash["ok"] is False
    assert "CLAUDE_CODE_GIT_BASH_PATH" in bash["hint"]


# ── /api/system/status ─────────────────────────────────────────────


def test_status_carries_prerequisites_and_data_dir(client: TestClient, monkeypatch):
    import routers.system as system_router
    from config import get_app_data_dir

    fake = [{"name": "git", "ok": False, "hint": "Run: xcode-select --install"}]
    monkeypatch.setattr(system_router, "check_prerequisites", lambda: fake)
    response = client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert data["prerequisites"] == fake
    assert data["data_dir"] == str(get_app_data_dir())
    # existing fields unchanged
    for key in ("database", "agent", "channel_gateway", "swarm_workspace", "initialized", "timestamp"):
        assert key in data


def test_status_survives_a_failing_prerequisite_check(client: TestClient, monkeypatch):
    import routers.system as system_router

    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(system_router, "check_prerequisites", boom)
    response = client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json()["prerequisites"] == []


def test_an_os_error_hint_carries_no_internal_detail(prereq, monkeypatch):
    def broken(*_a, **_k):
        raise OSError(13, "Permission denied", "/private/secret/path")
    monkeypatch.setattr(prereq.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(prereq.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(prereq.subprocess, "run", broken)
    git = _by_name(prereq.check_prerequisites())["git"]
    assert git["ok"] is None
    assert "/private/secret" not in git["hint"] and "Permission denied" not in git["hint"]
