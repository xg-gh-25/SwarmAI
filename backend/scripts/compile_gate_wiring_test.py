#!/usr/bin/env python3
"""
Tests for compile_gate_wiring — the Kiro -> Claude Code gate-wiring compiler.

Covers the dialect transform (matcher, entry nesting, aim-strip) AND asserts the
emitted block is STRUCTURALLY VALID against SwarmAI's OWN live .claude/settings.json
(we ARE Claude Code — the reference shape is not a guess).
"""
import json
from pathlib import Path

import pytest

from compile_gate_wiring import (
    compile_kiro_to_claude,
    compile_spec_pretooluse,
    strip_aim,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/scripts -> repo root
_LIVE_SETTINGS = _REPO_ROOT / ".claude" / "settings.json"

# A representative Kiro agent-spec preToolUse entry (the shape a bound internal DDD emits).
KIRO_ENTRY = {
    "matcher": "shell",
    "command": "python3 {{aim:filepath:gates/no_git_push.py}}",
    "description": "Block `git push` — commits are local-only; CRUX auto-merge owns the remote.",
}


def test_matcher_shell_becomes_bash():
    out = compile_kiro_to_claude(KIRO_ENTRY)
    assert out["matcher"] == "Bash"


def test_entry_is_nested_under_hooks_array():
    out = compile_kiro_to_claude(KIRO_ENTRY)
    assert isinstance(out["hooks"], list) and len(out["hooks"]) == 1
    assert out["hooks"][0]["type"] == "command"


def test_aim_template_stripped():
    assert strip_aim("python3 {{aim:filepath:gates/no_git_push.py}}") == "python3 gates/no_git_push.py"
    out = compile_kiro_to_claude(KIRO_ENTRY)
    assert "{{aim" not in out["hooks"][0]["command"]
    assert out["hooks"][0]["command"] == "python3 gates/no_git_push.py"


def test_non_shell_matcher_passthrough():
    e = {"matcher": "use_aws", "command": "python3 x.py"}
    assert compile_kiro_to_claude(e)["matcher"] == "use_aws"


def test_malformed_aim_template_raises():
    """Gate-2: an unknown/malformed aim template must NOT leak into the command as
    a literal (a broken hook) — strip_aim raises instead."""
    with pytest.raises(ValueError):
        strip_aim("python3 {{aim:other:value}}")
    with pytest.raises(ValueError):
        compile_kiro_to_claude({"matcher": "shell", "command": "python3 {{aim:bogus}}"})


def test_empty_command_or_matcher_raises():
    """Gate-2: an empty command/matcher = a functionally-dead hook (blocks nothing).
    The compiler must refuse rather than emit it."""
    with pytest.raises(ValueError):
        compile_kiro_to_claude({"matcher": "shell", "command": ""})
    with pytest.raises(ValueError):
        compile_kiro_to_claude({"matcher": "", "command": "python3 x.py"})


def test_compile_full_spec_block():
    spec = {"clientConfig": {"kiroCli": {"hooks": {"preToolUse": [KIRO_ENTRY]}}}}
    block = compile_spec_pretooluse(spec)
    assert "hooks" in block and "PreToolUse" in block["hooks"]
    assert block["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_emitted_block_matches_live_settings_shape():
    """The compiled entry must have the SAME structural keys as a real entry in
    SwarmAI's live .claude/settings.json (matcher + hooks[].type + hooks[].command)."""
    assert _LIVE_SETTINGS.exists(), f"missing reference {_LIVE_SETTINGS}"
    live = json.loads(_LIVE_SETTINGS.read_text())
    live_entry = live["hooks"]["PreToolUse"][0]
    live_keys = set(live_entry.keys())
    live_hook_keys = set(live_entry["hooks"][0].keys())

    out = compile_kiro_to_claude(KIRO_ENTRY)
    # emitted entry's keys must be a subset of the live shape's keys
    assert set(out.keys()) <= live_keys, f"{set(out.keys())} not subset of {live_keys}"
    assert {"type", "command"} <= set(out["hooks"][0].keys())
    # and every key we emit on the inner hook must be one the live shape uses
    assert set(out["hooks"][0].keys()) <= live_hook_keys, \
        f"emitted hook keys {set(out['hooks'][0].keys())} not subset of live {live_hook_keys}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
