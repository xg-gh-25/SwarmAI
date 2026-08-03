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


import os

from core.security_hooks import (
    _SYNTAX_CHECK_SHELL,
    _resolve_syntax_check_shell,
    bash_syntax_guard,
)

# The two zsh-short-form cases below (`foo() { echo hi }`, `for i (1 2 3) { … }`)
# are VALID zsh but INVALID bash. The guard parse-checks with the EXEC shell
# (`_SYNTAX_CHECK_SHELL`, resolved from $SHELL → /bin/zsh → …). The guard's
# behavior on these is therefore CORRECT-FOR-THE-RESOLVED-SHELL, not a fixed
# approve: under zsh it must APPROVE (valid syntax), under bash it must DENY
# (bash -n rejects zsh-only syntax → would-hang set for bash). Rather than SKIP
# off-zsh (which would silence the false-kill regression lock on exactly the CI
# Linux/bash env where the original hardcoded-bash bug manifests), we assert the
# shell-correct decision in BOTH environments. Match on basename (not substring)
# so a path like `/usr/local/zsh-tools/bash` is not misread as zsh.
_RESOLVED_SHELL_IS_ZSH = os.path.basename(_SYNTAX_CHECK_SHELL or "") == "zsh"


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

    def test_zsh_brace_function_shell_correct(self):
        # GATE-2 HIGH (run_07fd1d8f): the Bash tool runs zsh on macOS. A one-line
        # zsh brace function `foo() { echo hi }` (no semicolon before `}`) is
        # VALID zsh but `/bin/bash -n` REJECTS it (exit 2). The guard MUST check
        # with the EXEC shell, so its decision is shell-dependent: APPROVE under
        # zsh (no false-kill — the regression lock), DENY under bash (the syntax
        # genuinely would-hang bash). Assert the shell-correct decision in BOTH
        # environments so CI (bash) still exercises the guard rather than skipping.
        result = _run("foo() { echo hi }")
        if _RESOLVED_SHELL_IS_ZSH:
            assert not _is_deny(result)  # zsh: valid → must not false-kill
        else:
            assert _is_deny(result)  # bash: invalid syntax → correctly denied

    def test_zsh_brace_loop_shell_correct(self):
        # zsh short-form loop `for x (list) { ... }` is valid zsh (exit 0) but
        # `/bin/bash -n` rejects it — same root cause (shell mismatch). NOTE:
        # `for i in 1 2 3 { ... }` is NOT valid zsh either (verified: zsh -n
        # exit 1). Shell-correct: approve under zsh, deny under bash.
        result = _run("for i (1 2 3) { echo $i }")
        if _RESOLVED_SHELL_IS_ZSH:
            assert not _is_deny(result)
        else:
            assert _is_deny(result)

    def test_syntax_check_shell_prefers_zsh_when_available(self):
        # Finding-5 regression lock (platform-independent): the WHOLE point of the
        # guard is to check with the exec shell. If a future change hardcodes bash
        # or drops the $SHELL/zsh preference, this fails on EVERY platform that has
        # zsh — closing the CI coverage hole that skipping would leave. We assert
        # the resolution PREFERENCE, not the host's actual shell: when /bin/zsh
        # exists and $SHELL is unset, the resolver must pick zsh over bash.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHELL", None)
            if os.path.exists("/bin/zsh"):
                assert os.path.basename(_resolve_syntax_check_shell()) == "zsh"
            else:
                # No zsh on this host (e.g. CI Linux): resolver must still return a
                # usable shell and never crash — falls back to bash.
                assert _resolve_syntax_check_shell() in ("/bin/bash", "bash")


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

    def test_oversized_command_fails_open_without_shell(self):
        # GATE-2 LOW: a pathological huge/deeply-nested command could pin a core
        # in the parse check. Oversized input must fail-open WITHOUT invoking the
        # shell at all. Patch create_subprocess_exec to assert it is NOT called.
        big = "echo " + ("$(" * 200_000)  # > 256KB, also unbalanced
        with patch(
            "core.security_hooks.asyncio.create_subprocess_exec",
            side_effect=AssertionError("shell must NOT be invoked for oversized cmd"),
        ):
            assert not _is_deny(_run(big))
