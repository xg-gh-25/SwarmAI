"""Tests for bash_syntax_guard PreToolUse Bash gate.

The guard runs `bash -n` (parse-only, no execution) on every Bash command BEFORE
it runs. A syntactically incomplete command — an unterminated quote, backtick, or
unclosed block — makes bash enter PS2 continuation mode waiting on stdin that
never arrives in headless mode, so it BLOCKS FOREVER (run-real: an unterminated
`echo "=== jobs/bedro` ran 12 minutes, escaping the 120s foreground timeout via
harness auto-backgrounding). `bash -n` exit!=0 == the PS2-continuation set == the
hang set, so denying exactly those prevents the hang at zero false-kill cost.

Contract (XG-approved A+C, B untouched):
    Bash command → `bash -n` parse-check FIRST. Syntax error (exit!=0) → DENY,
    echo the bash stderr so the agent rewrites it immediately instead of hanging.
    EVERYTHING ELSE fail-OPEN (approve): non-Bash, empty, valid syntax, AND any
    guard-infra failure (bash missing, the check itself timing out, OSError). The
    guard must never block a legitimate command because of its OWN failure.

Methodology: the guard is an async fn returning {decision:"approve"} or
{hookSpecificOutput:{permissionDecision:"deny",...}}. Tests assert the decision
per command shape. The DENY set is verified to match real `bash -n` exit!=0
(T1/T2/T6/T7/T8 from the EVALUATE live probe); the APPROVE set covers valid
multiline/heredoc/$()/quoted/long commands (T3/T4/T9/T11) plus the fail-open
infra cases.
"""

import asyncio
from unittest.mock import patch

import pytest

from core.security_hooks import bash_syntax_guard


def _run(command, tool_name="Bash", run_in_background=False):
    tool_input = {"command": command}
    if run_in_background:
        tool_input["run_in_background"] = True
    return asyncio.run(
        bash_syntax_guard(
            {"tool_name": tool_name, "tool_input": tool_input}, None, None
        )
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _deny_reason(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


class TestTrueKill:
    """AC3: syntactically incomplete commands (the hang set) are DENIED.

    Each of these makes `bash -n` exit!=0 because bash would enter PS2
    continuation waiting on stdin — exactly the 12-minute-hang class.
    """

    def test_unterminated_double_quote_denied(self):
        # THE incident command: `echo "=== jobs/bedro` (unterminated ").
        assert _is_deny(_run('echo "=== jobs/bedro'))

    def test_unterminated_single_quote_denied(self):
        assert _is_deny(_run("grep 'foo backend/x.py"))

    def test_unterminated_backtick_denied(self):
        assert _is_deny(_run("echo `ls"))

    def test_unclosed_if_block_denied(self):
        assert _is_deny(_run("if [ -f x ]; then echo hi"))

    def test_unclosed_brace_function_denied(self):
        assert _is_deny(_run("foo() { echo hi"))

    def test_concatenated_garbage_command_denied(self):
        # The real-world shape: multiple commands smashed onto one line with a
        # trailing unterminated quote (newlines eaten) — the incident pattern.
        assert _is_deny(
            _run('cd /tmp grep -n foo x.py | head -20 echo "=== jobs/bedro')
        )

    def test_deny_reason_includes_bash_stderr(self):
        # The reason must echo bash's own diagnostic so the agent can rewrite.
        reason = _deny_reason(_run('echo "unterminated'))
        assert "syntax" in reason.lower() or "unexpected" in reason.lower() or "eof" in reason.lower()


class TestNoFalseKill:
    """AC2 (BLOCKING): valid commands — including complex ones — are APPROVED.

    These all `bash -n` exit 0. A false kill here is the cardinal sin: it would
    block legitimate work and erode trust in the guard.
    """

    def test_valid_heredoc_approved(self):
        assert not _is_deny(_run("cat <<EOF\nhello\nEOF\necho done"))

    def test_valid_subshell_and_quotes_approved(self):
        assert not _is_deny(_run('X=$(ls -1 | head -5); echo "got: $X"'))

    def test_valid_long_pipeline_approved(self):
        assert not _is_deny(
            _run("for i in 1 2 3; do echo $i; done; grep -rn foo . | head")
        )

    def test_valid_mixed_quotes_approved(self):
        assert not _is_deny(_run("echo \"a'b\" && echo \"c\\\"d\""))

    def test_valid_simple_grep_approved(self):
        assert not _is_deny(
            _run('grep -nE "converse|_get_bedrock|def test" backend/tests/x.py | head -20')
        )

    def test_unterminated_heredoc_approved_does_not_hang(self):
        # SUBTLE (EVALUATE T5): an unterminated heredoc `bash -n` EXITS 0, AND it
        # does not wait on stdin (reads to end-of-string) → it does not hang, so
        # approving it is correct. The guard only targets the real hang set.
        assert not _is_deny(_run("cat <<EOF\nhello\n"))

    def test_valid_multiline_with_backslash_continuation_approved(self):
        assert not _is_deny(
            _run("python backend/scripts/x.py \\\n  --project SwarmAI \\\n  --flag")
        )


class TestFailOpen:
    """AC4: the guard NEVER blocks because of its OWN failure (fail-open)."""

    def test_non_bash_approved(self):
        assert not _is_deny(_run('echo "unterminated', tool_name="Read"))

    def test_empty_command_approved(self):
        assert not _is_deny(_run(""))

    def test_missing_command_key_approved(self):
        assert not _is_deny(
            asyncio.run(
                bash_syntax_guard(
                    {"tool_name": "Bash", "tool_input": {}}, None, None
                )
            )
        )

    def test_bash_binary_missing_fails_open(self):
        # If the bash -n subprocess can't even start (FileNotFoundError), the
        # guard must APPROVE — never block a real command on infra failure.
        with patch(
            "core.security_hooks.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("no bash"),
        ):
            assert not _is_deny(_run('echo "unterminated'))

    def test_check_timeout_fails_open(self):
        # Drive the REAL timeout path (GUI32 — exercise the real cancellable
        # subprocess, don't mock wait_for which leaves communicate() un-awaited):
        # spawn a genuinely slow process in place of `bash -n` and set a tiny cap
        # so wait_for actually fires, the kill branch actually runs on a real
        # proc, and the guard fails OPEN.
        async def _slow_proc(*_a, **kw):
            # A real subprocess that outlives the 0.05s cap. communicate() will be
            # cancelled by wait_for → guard kills it → fail-open.
            return await asyncio.create_subprocess_exec(
                "/bin/sh", "-c", "sleep 5",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        with patch("core.security_hooks._BASH_SYNTAX_CHECK_TIMEOUT_S", 0.05), patch(
            "core.security_hooks.asyncio.create_subprocess_exec", side_effect=_slow_proc
        ):
            assert not _is_deny(_run('echo "unterminated'))

    def test_unexpected_exception_fails_open(self):
        with patch(
            "core.security_hooks.asyncio.create_subprocess_exec",
            side_effect=RuntimeError("boom"),
        ):
            assert not _is_deny(_run('echo "unterminated'))
