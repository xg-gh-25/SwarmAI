"""backend/scripts/pytest-full-suite-guard.sh — the Claude Code PreToolUse(Bash) hook.

What is tested: it still blocks a bare full-suite pytest run and passes targeted runs,
the SWARMAI_SUITE=1 bypass and non-pytest commands; and a command that mentions
neither pytest nor make is answered with bash builtins only (no cat / jq / grep
process): it runs on every Bash call, so that path must not spawn anything. Proven by
running it with an empty PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pytest-full-suite-guard.sh"
BASH = shutil.which("bash") or "/bin/bash"


def _run(command: str, env: dict | None = None) -> tuple[int, str]:
    payload = json.dumps({"tool_input": {"command": command}})
    r = subprocess.run([BASH, str(SCRIPT)], input=payload, text=True, capture_output=True,
                       env=env, timeout=10)
    return r.returncode, r.stderr


@pytest.mark.skipif(shutil.which("jq") is None, reason="the slow path needs jq")
@pytest.mark.parametrize("command,rc", [
    ("cd backend && python -m pytest -q", 2),
    ("pytest", 0),  # no trailing space: not matched as a pytest run (unchanged)
    ("cd backend && python -m pytest tests/test_x.py -q", 0),
    ("python -m pytest -k thing", 0),
    ("SWARMAI_SUITE=1 python -m pytest", 0),
    ("make test", 2),
    ("make test-backend", 0),
])
def test_verdicts_unchanged(command, rc):
    assert _run(command)[0] == rc


@pytest.mark.parametrize("command", ["ls -la", "git status && echo done", "cat README.md | head -5"])
def test_an_unrelated_command_spawns_no_program(tmp_path, command):
    # An empty PATH: cat, jq and grep cannot be found. Exit 0 with no "not found"
    # on stderr proves none was even tried.
    rc, err = _run(command, env={"PATH": str(tmp_path)})
    assert rc == 0
    assert "not found" not in err
