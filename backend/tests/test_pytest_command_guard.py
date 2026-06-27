"""Tests for pytest_command_guard PreToolUse Bash gate (run_6af22b0d).

The guard denies two pytest anti-patterns that R9 documents but prose failed to
stop (the agent re-ran a swallowed pytest ~6× in run_241014d4):
  1. pytest piped into tail/head — long output is swallowed by harness
     auto-backgrounding, producing empty output the agent misattributes.
  2. pytest with no per-test timeout (--timeout=N) AND no gtimeout/timeout
     wrapper — a slow run hangs to the foreground ceiling.

Methodology: the guard is a stateless async fn returning {decision:"approve"}
or {hookSpecificOutput:{permissionDecision:"deny",...}}. Tests assert the
decision for each command shape. Invariant: APPROVE everything that is not the
narrow anti-pattern (fail-safe) — non-Bash, non-pytest, compliant pytest, and
the filename false-positive (`cat pytest.log | tail`).
"""

import asyncio

import pytest

from core.security_hooks import pytest_command_guard


def _run(command, tool_name="Bash", run_in_background=False):
    tool_input = {"command": command}
    if run_in_background:
        tool_input["run_in_background"] = True
    return asyncio.run(
        pytest_command_guard(
            {"tool_name": tool_name, "tool_input": tool_input}, None, None
        )
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _has_warn(result):
    """allow + a non-empty additionalContext nudge (the no-timeout WARN path)."""
    hso = result.get("hookSpecificOutput", {})
    return hso.get("permissionDecision") == "allow" and bool(
        hso.get("additionalContext")
    )


class TestPytestPipedToPager:
    """AC1: pytest piped to tail/head is denied."""

    def test_pipe_to_tail_denied(self):
        assert _is_deny(_run("python -m pytest tests/foo.py | tail -5"))

    def test_pipe_to_head_denied(self):
        assert _is_deny(_run("pytest tests/foo.py --timeout=60 | head -20"))

    def test_pipe_to_tail_with_grep_chain_denied(self):
        # pytest ... | grep x | tail — still ends at a pager, still swallowed
        assert _is_deny(_run("pytest tests/foo.py | grep PASS | tail -3"))

    def test_deny_reason_names_redirect_fix(self):
        reason = _run("pytest x | tail").get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert "redirect" in reason.lower() or "> " in reason or "file" in reason.lower()


class TestPytestNoTimeout:
    """AC2 (revised by Gate-2): pytest without a timeout is WARNED, not denied.
    An un-timed run is bounded by the harness ceiling and timeout may live in
    config — denying it is a false positive. The destructive case (pipe) denies;
    this one only nudges."""

    def test_no_timeout_warns_not_denied(self):
        result = _run("python -m pytest tests/foo.py -q")
        assert not _is_deny(result)
        assert _has_warn(result)

    def test_timeout_flag_no_warn(self):
        result = _run("python -m pytest tests/foo.py --timeout=60 -q")
        assert not _is_deny(result)
        assert not _has_warn(result)

    def test_timeout_space_form_no_warn(self):
        # Gate-2: `--timeout 60` (space) must satisfy the timeout check too.
        result = _run("pytest tests/foo.py --timeout 60")
        assert not _is_deny(result)
        assert not _has_warn(result)

    def test_gtimeout_wrapper_no_warn(self):
        assert not _has_warn(_run("gtimeout 90 python -m pytest tests/foo.py -q"))

    def test_timeout_wrapper_no_warn(self):
        assert not _has_warn(_run("timeout 90 pytest tests/foo.py"))


class TestFailSafeApprove:
    """AC3: approve everything outside the narrow anti-pattern."""

    def test_non_bash_approved(self):
        assert not _is_deny(_run("pytest | tail", tool_name="Read"))

    def test_non_pytest_approved(self):
        assert not _is_deny(_run("ls -la | tail -5"))

    def test_empty_command_approved(self):
        assert not _is_deny(_run(""))

    def test_compliant_pytest_redirect_approved(self):
        # The CORRECT pattern: redirect to file (not a pipe), with timeout.
        assert not _is_deny(
            _run("python -m pytest tests/foo.py --timeout=60 > /tmp/r.txt 2>&1")
        )

    def test_filename_mentioning_pytest_not_a_run(self):
        # FALSE-POSITIVE TRAP: 'pytest' appears in a filename, not as an
        # invocation. Must NOT be denied.
        assert not _is_deny(_run("cat pytest_output.log | tail -20"))

    def test_grep_pytest_in_file_approved(self):
        assert not _is_deny(_run("grep FAILED pytest.log | head"))

    def test_env_var_value_mentioning_pytest_approved(self):
        # REVIEW finding: env VALUE is the literal 'pytest', the actual command
        # is echo — not a pytest run. Must NOT be denied.
        assert not _is_deny(_run("VAR=pytest echo hi"))


class TestEnvPrefixedInvocation:
    """REVIEW finding (run_6af22b0d): an env-assignment PREFIX before pytest is
    still a pytest invocation and must be guarded (was a fail-open miss)."""

    def test_env_prefix_recognized_as_invocation(self):
        # Recognized as a pytest invocation → no-timeout WARN fires (proves the
        # env-prefix is matched; pre-Gate-2 this asserted deny).
        assert _has_warn(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py"))

    def test_multi_env_prefix_recognized_as_invocation(self):
        assert _has_warn(_run("FOO=1 BAR=2 pytest tests/foo.py"))

    def test_env_prefix_pipe_denied(self):
        # Env-prefixed invocation piped to a pager → the destructive case → deny.
        assert _is_deny(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py | tail"))

    def test_env_prefix_with_timeout_no_warn(self):
        assert not _has_warn(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py --timeout=60"))


class TestGate2Hardening:
    """Findings from the Gate-2 adversarial review (run_6af22b0d)."""

    def test_quoted_pipe_in_k_expr_not_denied(self):
        # '| tail' is inside a -k quoted string, NOT a real pipe → must approve.
        assert not _is_deny(_run("pytest --timeout=60 -k 'a | tail'"))

    def test_config_timeout_bare_pytest_not_denied(self):
        # Timeout in pyproject.toml → bare `pytest` has no flag; must NOT deny
        # (only warn). Denying a correct config-driven setup is the false positive.
        result = _run("pytest -p no:cacheprovider tests/")
        assert not _is_deny(result)

    def test_poetry_run_pytest_pipe_denied(self):
        assert _is_deny(_run("poetry run pytest tests/ | tail -5"))

    def test_uv_run_pytest_pipe_denied(self):
        assert _is_deny(_run("uv run pytest tests/ | head"))

    def test_real_pipe_still_denied_even_with_timeout(self):
        # The pipe is the destructive trigger — denies regardless of timeout.
        assert _is_deny(_run("pytest tests/ --timeout=60 | tail -5"))
