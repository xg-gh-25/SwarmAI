"""Tests for eval_command_guard PreToolUse Bash gate (run_a16d61ad).

Eval is a SYSTEM-LEVEL decoupled subsystem (DEC05/PIT179) that scores the
DEPLOYED system via CI (post-push) / deploy / scheduled — NEVER by the agent
inside a coding pipeline. Running eval on un-deployed changes tests the OLD
binary (the daemon still runs it mid-pipeline), proves nothing about the change
in flight, wastes tokens, and on 2026-06-28 hung the LLM-judge's Bedrock call
(a network HANG that froze the session spinner).

Prose (R6/R9/STEERING #5) said this and was violated anyway (CLASS A/B). This
guard is the structural backstop (P7: defense outside the agent), the twin of
pytest_command_guard / background_command_guard.

Methodology: stateless async fn returning {decision:"approve"} or
{hookSpecificOutput:{permissionDecision:"deny",...}}. Invariant: DENY any eval
invocation in the agent Bash path; APPROVE everything else (fail-safe for
non-Bash / non-eval / filename-in-a-quoted-string false-positive).
"""

import asyncio

import pytest

from core.security_hooks import eval_command_guard


def _run(command, tool_name="Bash"):
    return asyncio.run(
        eval_command_guard(
            {"tool_name": tool_name, "tool_input": {"command": command}}, None, None
        )
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestEvalInvocationsDenied:
    """The whole point: every shape of running eval in the agent Bash path is denied."""

    @pytest.mark.parametrize("cmd", [
        "python scripts/eval_runner.py run --trigger code_change",
        "cd backend && python scripts/eval_runner.py run --trigger code_change",
        "python backend/scripts/ci_eval_gate.py",
        "cd backend && python scripts/ci_eval_gate.py",
        "perl -e 'alarm 300; exec @ARGV' python scripts/eval_runner.py run --trigger x",
        "eval_runner run --trigger code_change",
        "python -m core.eval_service run",
    ])
    def test_eval_command_denied(self, cmd):
        assert _is_deny(_run(cmd)), f"eval invocation must be DENIED: {cmd!r}"

    def test_deny_reason_explains_system_level(self):
        r = _run("python scripts/eval_runner.py run --trigger code_change")
        reason = r.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "系统层" in reason or "system" in reason.lower()
        assert "CI" in reason or "deploy" in reason.lower()


class TestNonEvalApproved:
    """Fail-safe: the guard must not block legitimate non-eval commands."""

    @pytest.mark.parametrize("cmd", [
        "python -m pytest tests/test_foo.py",
        "git status",
        "python scripts/artifact_cli.py run-status",
        "ls backend/scripts/",
        "cat backend/scripts/eval_runner.py",          # READING the file is fine
        "grep -n run backend/scripts/eval_runner.py",   # grepping is fine
    ])
    def test_non_eval_approved(self, cmd):
        assert not _is_deny(_run(cmd)), f"non-eval command must be APPROVED: {cmd!r}"

    def test_git_commit_mentioning_eval_not_a_false_positive(self):
        # The eval script name inside a quoted commit message must NOT trip the gate.
        cmd = 'git commit -m "fix: ci_eval_gate.py stale-report handling"'
        assert not _is_deny(_run(cmd)), "quoted eval filename in a commit msg is not an eval run"

    def test_non_bash_tool_approved(self):
        assert not _is_deny(_run("python scripts/eval_runner.py run", tool_name="Read"))

    def test_empty_command_approved(self):
        assert not _is_deny(_run(""))


class TestRegisteredInHookChain:
    """The guard is wired into the PreToolUse chain — not just defined (GUI: dead code)."""

    def test_guard_registered(self):
        import inspect
        from core import hook_builder

        src = inspect.getsource(hook_builder)
        assert "eval_command_guard" in src, (
            "eval_command_guard must be registered in hook_builder.build_hooks — "
            "an unregistered guard is dead code (defense outside the agent requires wiring)."
        )
