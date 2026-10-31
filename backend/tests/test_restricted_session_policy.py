"""A non-owner channel session is confined by mechanisms the CLI enforces, not by text
matching (core/prompt_builder.restricted_session_policy / restricted_tool_gate; design
2026-10-31-swarmai-aws-account §5.3).

Questions answered:
1. does the session get no Bash, and are the owner's credential stores read-denied in the
   CLI's permission-rule path syntax (a bare absolute path denies nothing: measured)?
2. does the PreToolUse gate (which the CLI runs in bypassPermissions and for subagents,
   where can_use_tool is never called) let through only allowed paths and tools, resolve
   relative paths against the session's folder, and deny on its own error?
3. does build_options put the gate first, drop add_dirs, Bash, setting sources and skill
   shell, only for a non-owner? (``~`` paths and the CLI's own config files in the folder
   are denied: the CLI expands ``~`` itself, and project settings could add hooks.)
4. are the account's work-doing POSTs refused without X-Swarm-User-Action?

Measured 2026-10-31 with the real CLI in bypassPermissions: before, a Read outside the
session's folder went through and a sandboxed Bash wrote ~/.swarm-ai, printed a planted
*_API_KEY and queried the keychain; with this gate, Reads outside the folder (also from a
subagent) and an absolute Glob pattern are denied, a Read inside works, Bash is absent.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.prompt_builder import _rule_path, restricted_session_policy, restricted_tool_gate
from core.security_hooks import create_file_access_permission_handler


def test_no_bash_and_the_owners_credential_stores_are_read_denied():
    pol = restricted_session_policy()
    assert pol["disallow"] == ["Bash"]
    deny = pol["deny_read"]
    from config import get_app_data_dir
    assert _rule_path(Path(get_app_data_dir()) / "aws") + "/**" in deny  # the token file
    for want in ("~/.aws/**", "~/.ada/**", "~/.midway/**", "~/.ssh/**", "~/.config/**"):
        assert want in deny, want
    assert any(d.endswith("secrets.json") for d in deny)
    assert all(d.startswith(("~/", "//")) for d in deny), deny


def test_rule_paths_use_the_cli_syntax():
    assert _rule_path(Path.home() / ".swarm-ai" / "aws") == "~/.swarm-ai/aws"
    assert _rule_path(Path("/opt/swarm/secrets.json")) == "//opt/swarm/secrets.json"


def _gate(tmp_path):
    sender = tmp_path / "sender"
    sender.mkdir()
    (sender / "mine.txt").write_text("x")
    return restricted_tool_gate(create_file_access_permission_handler([str(sender)]),
                                str(sender)), sender


def _decide(gate, tool, tool_input, **extra):
    res = asyncio.run(gate({"tool_name": tool, "tool_input": tool_input, **extra}, None, None))
    return (res.get("hookSpecificOutput") or {}).get("permissionDecision") or "allow"


@pytest.mark.parametrize("tool,inp,want", [
    ("Read", {"file_path": "{s}/mine.txt"}, "allow"),
    ("Read", {"file_path": "mine.txt"}, "allow"),                     # relative to the folder
    ("Read", {"file_path": "{o}/secret.txt"}, "deny"),
    ("Read", {"file_path": "../outside/secret.txt"}, "deny"),
    ("Write", {"file_path": "{o}/x.txt", "content": "x"}, "deny"),
    ("Edit", {"file_path": "{o}/x.txt", "old_string": "a", "new_string": "b"}, "deny"),
    ("NotebookEdit", {"notebook_path": "{o}/n.ipynb", "new_source": ""}, "deny"),
    ("Glob", {"pattern": "*.txt"}, "allow"),                          # no path: the folder
    ("Glob", {"pattern": "/tmp/**"}, "deny"),
    ("Glob", {"pattern": "../**"}, "deny"),
    ("Glob", {"pattern": "*", "path": "{o}"}, "deny"),
    ("Grep", {"pattern": r"\.\./x"}, "allow"),                        # a regex, not a path
    ("Grep", {"pattern": "x", "glob": "/etc/*"}, "deny"),
    ("Grep", {"pattern": "x", "path": "{o}"}, "deny"),
    ("Read", {"file_path": "~/.zshrc"}, "deny"),                      # the CLI expands ~
    ("Write", {"file_path": "~/.zshrc", "content": "x"}, "deny"),
    ("Grep", {"pattern": "x", "path": "~"}, "deny"),
    ("Glob", {"pattern": "*", "path": "~/.swarm-ai"}, "deny"),
    ("Glob", {"pattern": "{..,a}/x"}, "deny"),
    ("Write", {"file_path": ".claude/settings.json", "content": "{}"}, "deny"),
    ("Write", {"file_path": "{s}/.Claude/skills/x/SKILL.md", "content": ""}, "deny"),
    ("Edit", {"file_path": "CLAUDE.md", "old_string": "a", "new_string": "b"}, "deny"),
    ("Write", {"file_path": "AGENTS.md", "content": ""}, "deny"),
    ("Write", {"file_path": ".mcp.json", "content": "{}"}, "deny"),
    ("Write", {"file_path": "notes/report.md", "content": "x"}, "allow"),
    ("Read", {"file_path": ".claude/settings.json"}, "allow"),          # reading is harmless
    ("Bash", {"command": "echo hi"}, "deny"),
    ("BashOutput", {"bash_id": "1"}, "deny"),
    ("SomeFutureTool", {}, "deny"),
    ("WebFetch", {"url": "https://example.com", "prompt": "x"}, "allow"),
    ("Skill", {"skill": "x"}, "deny"),
    ("mcp__slack__post", {}, "allow"),                                # filtered by tier earlier
    ("Agent", {"prompt": "x"}, "allow"),
])
def test_gate_decides(tmp_path, tool, inp, want):
    gate, sender = _gate(tmp_path)
    (tmp_path / "outside").mkdir()
    o = str(tmp_path / "outside")
    inp = {k: v.replace("{s}", str(sender)).replace("{o}", o) if isinstance(v, str) else v
           for k, v in inp.items()}
    assert _decide(gate, tool, inp) == want


def test_a_subagents_call_is_checked_the_same(tmp_path):
    gate, _sender = _gate(tmp_path)
    assert _decide(gate, "Read", {"file_path": "/etc/hosts"}, agent_id="a1") == "deny"


def test_the_gate_denies_on_its_own_error(tmp_path):
    async def broken(*_a):
        raise RuntimeError("boom")
    gate = restricted_tool_gate(broken, str(tmp_path))
    assert _decide(gate, "Read", {"file_path": str(tmp_path / "x")}) == "deny"


def test_work_doing_posts_need_the_app_header(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    from core.app_config_manager import AppConfigManager
    monkeypatch.setattr(AppConfigManager, "_instance",
                        AppConfigManager(config_path=tmp_path / "config.json"))
    client = TestClient(app)
    for path in ("/api/aws-account/verify", "/api/aws-account/check"):
        assert client.post(path).status_code == 403, path
    ok = client.post("/api/aws-account/verify", headers={"X-Swarm-User-Action": "aws-account"})
    assert ok.status_code == 200 and ok.json()["state"] == "unconnected"


@pytest.mark.asyncio
@pytest.mark.parametrize("tier,restricted", [("public", True), ("trusted", True), ("owner", False)])
async def test_build_options_confines_only_a_non_owner_channel(tier, restricted, tmp_path, monkeypatch):
    import json
    from tests.test_knowledge_restricted_channel import _options
    ctx = {"channel_id": "c1", "channel_type": "slack",
           "sender_identity": {"permission_tier": tier, "external_id": "u1"}}
    opts = await _options(ctx, tmp_path, monkeypatch)
    settings = json.loads(opts.settings)
    first = (opts.hooks or {}).get("PreToolUse", [None])[0]
    if restricted:
        assert first.matcher is None
        gate = first.hooks[0]
        assert (await gate({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
                           None, None))["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Bash" in opts.disallowed_tools
        assert not opts.add_dirs
        assert "Read(~/.aws/**)" in settings["permissions"]["deny"]
        assert settings["disableSkillShellExecution"] is True
        assert opts.setting_sources == []  # files in its folder never configure the CLI
    else:
        assert "permissions" not in settings
        assert "Bash" not in opts.disallowed_tools
        assert opts.setting_sources == ["project"]
        assert all(getattr(m.hooks[0], "__name__", "") != "gate"
                   for m in (opts.hooks or {}).get("PreToolUse", []))
