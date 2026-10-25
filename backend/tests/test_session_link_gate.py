"""Tests for create_session_link_gate (security_hooks.py) and its registration.

What is tested: the PreToolUse Bash gate that keeps the run↔session link intact.
The run gates only apply to runs whose run.json session_id is this session's id,
which pipeline-v1 create reads from SWARM_APP_SESSION_ID. A command that removes
or overrides that variable would create an unowned run and switch those gates off,
so the gate denies it ALWAYS.

Methodology: the gate is driven through its real async callable with no pipeline
run seeded (it must not depend on one), plus one end-to-end call through the real
build_hooks() Bash chain. Parametrized deny / approve matrices cover every form.

Invariants:
  • removal / override is denied: unset, env -u / -uVAR / --unset[=], env -S,
    any assignment (VAR=x cmd, VAR=x, VAR+=, env VAR=, sudo VAR=), export VAR=,
    export -n, declare / typeset / local / readonly forms, read / printf -v /
    for / getopts targets — also inside bash -c, sh -c, eval, pnpm dlx -c, nested
    shell strings, a shell reading a heredoc, and behind unknown wrappers;
  • a wholesale env reset (env -i / env - / --ignore-environment, exec -c, sudo
    without -E) is denied only when the wrapped command mentions python,
    or pipeline_v1 (directly or in its shell string);
  • reads pass (echo, env | grep, printenv, declare -p, grep VAR=, quoted text),
    and so do `env -u OTHER`, `export VAR` and clean-env runs of other tools;
  • the deny text explains the unlink and names the operator-only force env;
    it cannot be forced (gate_force.UNFORCEABLE); an internal error fails open;
  • build_hooks registers session_link_gate once, Bash-scoped, right before
    the commit gates, with every existing gate still registered.
"""
import asyncio

import pytest

from core import security_hooks as sh

V = "SWARM_APP_SESSION_ID"
SID = "app-session-1"


@pytest.fixture(autouse=True)
def _no_force(monkeypatch):
    monkeypatch.delenv("SWARM_GATE_FORCE", raising=False)


def _call(command, tool_name="Bash", ctx=None):
    gate = sh.create_session_link_gate(ctx if ctx is not None else {"sdk_session_id": SID})
    return asyncio.run(gate(
        {"tool_name": tool_name, "tool_input": {"command": command}, "cwd": "/tmp"},
        None, None))


def _is_deny(res):
    return res.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


REMOVALS = [
    f"unset {V}",
    f"unset -v {V}",
    f"unset OTHER {V}",
    f"unset \"SWARM_APP_\"SESSION_ID",
    f"env -u {V} python-backend pipeline-v1 create",
    f"env -u {V} ls",
    f"env -u{V} ls",
    f"env -iu {V} ls",
    f"env --unset={V} ls",
    f"env --unset {V} ls",
    f"env -S '-u {V} python a.py'",
    f"/usr/bin/env -u {V} make",
    f"export -n {V}",
    f"typeset +x {V}",
    f"declare +x {V}",
    f"local {V}",
    f"declare {V}",
]
OVERRIDES = [
    f"{V}=forged python-backend pipeline-v1 create --project SwarmAI",
    f"{V}= python a.py",
    f"{V}=x",
    f"{V}+=x",
    f"FOO=1 {V}=x make",
    f"cd /tmp && {V}=x python a.py",
    f"export {V}=x",
    f"export FOO=1 {V}=x",
    f"readonly {V}=x",
    f"declare -x {V}=x",
    f"typeset {V}=x",
    f"local {V}=x",
    f"env {V}=x python a.py",
    f"env -S '{V}=x python a.py'",
    f"sudo {V}=x python a.py",
    f"read {V} < /tmp/f",
    f"read -r {V} <<< x",
    f"mapfile {V} < /tmp/f",
    f"printf -v {V} %s x",
    f"for {V} in a b; do python a.py; done",
    f"getopts ab {V}",
    f"command export {V}=x",
    f"if true; then {V}=x python a.py; fi",
]
NESTED = [
    f"bash -c 'unset {V}; python a.py'",
    f"sh -c \"env -u {V} python a.py\"",
    f"zsh -c 'export {V}=x'",
    f"bash -eo pipefail -c '{V}=x python a.py'",
    f"eval 'unset {V}'",
    f"eval export {V}=x",
    f"pnpm dlx -c 'unset {V}'",
    f"yarn dlx -c '{V}=x node a.js'",
    f"bash -c \"sh -c 'unset {V}'\"",
    f"bash -c \"bash -c 'bash -c \\\"unset {V}\\\"'\"",
    f"bash <<EOF\nunset {V}\npython a.py\nEOF",
    f"echo 'unset {V}' | sh",
    f"caffeinate env -u {V} python a.py",
    f"nice -n 5 env -u {V} python a.py",
    f"perl -e 'alarm 90; exec @ARGV' env -u {V} python a.py",
    f"timeout 60 bash -c 'unset {V}'",
    f"echo hi; unset {V}",
    f"( unset {V}; python a.py )",
    f"x=$(unset {V}; python a.py)",
    f"exec env -u {V} python a.py",
]
RESETS_DENIED = [
    "env -i python-backend pipeline-v1 create --project SwarmAI",
    "env -i PATH=/usr/bin python-backend pipeline-v1 status",
    "env - python3 a.py",
    "env --ignore-environment python a.py",
    "env -i -- python a.py",
    "env -0i python a.py",
    "env -i bash -c 'python-backend pipeline-v1 create'",
    "env -i sh <<EOF\npython-backend pipeline-v1 create\nEOF",
    "env -i uv run pipeline-v1 create",
    "bash -c 'env -i python a.py'",
    "exec -c python a.py",
    "sudo python-backend pipeline-v1 create",
    "sudo -u root python3 a.py",
    "sudo --preserve-env=PATH python a.py",
]
RESETS_ALLOWED = [
    "env -i PATH=/usr/bin make build",
    "env -i HOME=$HOME git status",
    "env -i bash -c 'ls -la'",
    "env -i node a.js",
    "sudo -E python a.py",
    f"sudo --preserve-env={V},PATH python a.py",
    "sudo --preserve-env python a.py",
    "sudo ls /var/log",
    "exec -c ls",
]
READS_ALLOWED = [
    f"echo ${V}",
    f"echo \"${{{V}}}\"",
    "env | grep SWARM",
    "env",
    f"printenv {V}",
    f"declare -p {V}",
    f"typeset -p {V}",
    f"export {V}",
    f"export -p",
    f"grep -rn '{V}=' backend/",
    f"grep -rn {V} backend/core/prompt_builder.py",
    f"echo {V}=x",
    f"test -n \"${V}\" && echo linked",
    f"git commit -m 'unset {V} is denied now'",
    f"python -c \"import os; print(os.environ.get('{V}'))\"",
    f"bash -c 'echo ${V}'",
    "env -u OTHER python a.py",
    "env -u PYTHONPATH --unset=FOO python a.py",
    "FOO=1 python a.py",
    "unset FOO BAR",
    "export FOO=1",
    "cat .env",
    "conda env list",
    "ls -la",
]


class TestDenyMatrix:
    @pytest.mark.parametrize("command", REMOVALS + OVERRIDES + NESTED + RESETS_DENIED)
    def test_denied(self, command):
        res = _call(command)
        assert _is_deny(res), command

    @pytest.mark.parametrize("command", RESETS_ALLOWED + READS_ALLOWED)
    def test_allowed(self, command):
        assert _call(command) == {"decision": "approve"}, command

    def test_applies_without_any_run_or_session(self):
        # Always on: no run.json is consulted, and an empty context still denies.
        assert _is_deny(_call(f"unset {V}", ctx={}))

    def test_non_bash_and_empty_commands_pass(self):
        assert _call(f"unset {V}", tool_name="Write") == {"decision": "approve"}
        assert _call("") == {"decision": "approve"}

    def test_deny_text_explains_the_unlink_and_operator_force(self):
        reason = _call(f"env -u {V} python a.py")["hookSpecificOutput"][
            "permissionDecisionReason"]
        assert reason.startswith("DENY (env -u " + V + ")")
        assert "pipeline test gate" in reason and "direct-commit gate" in reason
        assert "backend sets" in reason
        assert "no bypass" in reason and "FORCE" not in reason

    def test_depth_limit_fails_closed_when_the_variable_is_named(self):
        deep = f"unset {V}"
        for _ in range(6):
            deep = "bash -c " + sh.shlex.quote(deep)
        assert _is_deny(_call(deep))
        harmless = "echo hi"
        for _ in range(6):
            harmless = "bash -c " + sh.shlex.quote(harmless)
        assert _call(harmless) == {"decision": "approve"}


class TestForceAndFailOpen:
    @pytest.mark.parametrize("value", ["session_link_gate", "session_link_gate,commit_gate"])
    def test_cannot_be_forced(self, monkeypatch, value):
        monkeypatch.setenv("SWARM_GATE_FORCE", value)
        assert _is_deny(_call(f"unset {V}"))

    def test_internal_error_fails_open(self, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("classifier broke")

        monkeypatch.setattr(sh, "_command_session_link", boom)
        assert _call(f"unset {V}") == {"decision": "approve"}

    def test_unsplittable_command_is_left_to_bash_syntax_guard(self):
        command = f"unset {V} 'unterminated"
        assert _call(command) == {"decision": "approve"}
        res = asyncio.run(sh.bash_syntax_guard(
            {"tool_name": "Bash", "tool_input": {"command": command}}, None, None))
        assert _is_deny(res)  # the sibling guard in the same Bash chain denies it


def _build():
    from core import hook_builder
    from core.permission_manager import permission_manager

    return asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context={"sdk_session_id": "test-sess"},
        permission_manager=permission_manager,
    ))


def test_build_hooks_registers_the_gate_once_right_before_the_commit_gates(monkeypatch):
    from core import hook_builder

    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        for n in getattr(hook, "members", (name,)):  # a compose_gates group
            recorded.append((event, n, matcher))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    _build()
    names = [n for _e, n, _m in recorded]
    for existing in ("pre_tool_logger", "background_command_guard", "run_amend_gate",
                     "bash_syntax_guard",
                     "image_read_dedup_guard",
                     "dangerous_command_gate",
                     "active_run_commit_gate", "ask_question_gate",
                     "external_approval_gate", "governance_file_gate"):
        assert names.count(existing) == 1, existing
    assert names.count("session_link_gate") == 1
    assert names.index("session_link_gate") == names.index("run_amend_gate") - 1
    assert names.index("session_link_gate") < names.index("dangerous_command_gate")
    assert {n: (e, m) for e, n, m in recorded}["session_link_gate"] == ("PreToolUse", "Bash")
    without = [n for n in names if n != "session_link_gate"]
    assert without.index("background_command_guard") < without.index("run_amend_gate")


def test_real_bash_chain_denies_before_any_approval_prompt():
    """End to end: the Bash matcher built by build_hooks() denies the unlink. The
    chain short-circuits at session_link_gate, before dangerous_command_gate could
    open an approval dialog."""
    hooks, _skills, _allow_all = _build()
    bash = [hm for hm in hooks["PreToolUse"] if hm.matcher == "Bash"]
    assert len(bash) == 1
    res = asyncio.run(bash[0].hooks[0](
        {"tool_name": "Bash", "tool_input": {"command": f"env -u {V} python a.py"},
         "cwd": "/tmp"}, None, None))
    assert _is_deny(res)
    assert "no bypass" in res["hookSpecificOutput"]["permissionDecisionReason"]
