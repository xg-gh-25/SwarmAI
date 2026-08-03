"""Tests for pytest_command_guard PreToolUse Bash gate.

The guard denies two pytest anti-patterns that R9 documents but prose failed to
stop (the agent re-ran a swallowed pytest ~6× in run_241014d4, then again in a
no-pipe `> file` shape — C040, the 12th CLASS-B recurrence):
  1. pytest piped into tail/head — long output is swallowed by harness
     auto-backgrounding, producing empty output the agent misattributes.
  2. pytest with NO wall-clock wrapper (`gtimeout N` / `timeout N`) — a slow run
     gets auto-backgrounded and the foreground returns empty, reading as a hang.
     A per-test `--timeout=N` does NOT count: it bounds each test, not the whole
     command, so a large suite can still run for minutes. FAIL-CLOSED (R9 /
     PIT10 allowlist): a pytest invocation MUST carry a wall-clock cap.

Contract (XG-approved direct, post run_6af22b0d):
    pytest 调用 → 必须被 gtimeout/timeout <N> 包裹,否则 DENY。
    per-test --timeout 不算数(它不挡 wall-clock)。

Methodology: the guard is a stateless async fn returning {decision:"approve"}
or {hookSpecificOutput:{permissionDecision:"deny",...}}. Tests assert the
decision for each command shape. Invariant: APPROVE everything that is not a
pytest invocation (fail-safe for non-Bash / non-pytest / filename false-positive)
AND approve a pytest invocation that IS wall-clock-wrapped without a pager pipe.
"""

import asyncio


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


def _deny_reason(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


class TestPytestPipedToPager:
    """AC1: pytest piped to tail/head is denied (checked before wall-clock)."""

    def test_pipe_to_tail_denied(self):
        assert _is_deny(_run("python -m pytest tests/foo.py | tail -5"))

    def test_pipe_to_head_denied(self):
        assert _is_deny(_run("pytest tests/foo.py --timeout=60 | head -20"))

    def test_pipe_to_tail_with_grep_chain_denied(self):
        # pytest ... | grep x | tail — still ends at a pager, still swallowed
        assert _is_deny(_run("pytest tests/foo.py | grep PASS | tail -3"))

    def test_pipe_denied_takes_priority_reason_names_redirect(self):
        # The pipe check runs first; its reason points at the redirect fix.
        reason = _deny_reason(_run("pytest x | tail"))
        assert "redirect" in reason.lower() or "> " in reason or "file" in reason.lower()

    def test_real_pipe_denied_even_with_wallclock(self):
        # The pipe is a destructive trigger — denies regardless of a wrapper.
        assert _is_deny(_run("gtimeout 90 pytest tests/ | tail -5"))


class TestPytestNoWallClock:
    """AC2 (UPGRADED to DENY, XG-approved direct): pytest WITHOUT a wall-clock
    wrapper is DENIED. A per-test `--timeout` does NOT satisfy this — it does not
    bound the whole command. This is the core C040 fix: the previous WARN-only
    behavior let a no-wrapper `pytest > file 2>&1` through, and it got
    auto-backgrounded into a 10-min false hang."""

    def test_no_timeout_denied(self):
        assert _is_deny(_run("python -m pytest tests/foo.py -q"))

    def test_per_test_timeout_flag_still_denied(self):
        # --timeout=60 is per-test, NOT wall-clock → still denied.
        assert _is_deny(_run("python -m pytest tests/foo.py --timeout=60 -q"))

    def test_per_test_timeout_space_form_still_denied(self):
        # `--timeout 60` (space) is also per-test → still denied.
        assert _is_deny(_run("pytest tests/foo.py --timeout 60"))

    def test_redirect_without_wallclock_denied(self):
        # THE C040 CASE: redirect to file + per-test timeout, NO wall-clock.
        # This is exactly what auto-backgrounded for 10 minutes. Must DENY.
        assert _is_deny(
            _run("python -m pytest tests/foo.py --timeout=60 > /tmp/r.txt 2>&1")
        )

    def test_deny_reason_names_gtimeout_and_scope(self):
        reason = _deny_reason(_run("pytest tests/foo.py -q"))
        assert "gtimeout" in reason.lower()
        assert "scope" in reason.lower() or "smaller" in reason.lower()


class TestWallClockWrapperApproved:
    """AC3: a pytest invocation WITH a wall-clock wrapper and no pager pipe is
    approved — this is the sanctioned shape."""

    def test_gtimeout_wrapper_approved(self):
        assert not _is_deny(_run("gtimeout 90 python -m pytest tests/foo.py -q"))

    def test_bare_timeout_wrapper_approved(self):
        assert not _is_deny(_run("timeout 90 pytest tests/foo.py"))

    def test_sanctioned_shape_approved(self):
        # The full sanctioned pattern from AGENT.md R9.
        assert not _is_deny(
            _run(
                "gtimeout 90 python -m pytest tests/foo.py "
                "--timeout=60 -p no:cacheprovider > /tmp/t.txt 2>&1"
            )
        )

    def test_gtimeout_not_confused_by_inner_timeout_token(self):
        # 'gtimeout' contains 'timeout'; the gtimeout arm must match it as a
        # wall-clock wrapper (not be excluded by the bare-timeout lookbehind).
        assert not _is_deny(_run("gtimeout 120 pytest tests/foo.py"))

    def test_perl_alarm_fallback_approved(self):
        # THE REAL PATH on this machine: gtimeout/timeout are NOT installed, only
        # /usr/bin/perl is. The perl-alarm wrapper is the actual wall-clock cap
        # and MUST be accepted, else the gate denies every runnable pytest.
        assert not _is_deny(
            _run(
                "perl -e 'alarm 90; exec @ARGV' python -m pytest tests/foo.py "
                "--timeout=60 > /tmp/t.txt 2>&1"
            )
        )

    def test_perl_without_alarm_still_denied(self):
        # A perl invocation that is NOT an alarm wrapper must not be mistaken for
        # a wall-clock cap (the arm requires the 'alarm' token).
        assert _is_deny(_run("perl -e 'print 1' && pytest tests/foo.py"))

    def test_perl_alarm_direct_pytest_approved(self):
        # perl-alarm wrapping pytest directly (no `python -m`) — sanctioned.
        assert not _is_deny(_run("perl -e 'alarm 90; exec @ARGV' pytest tests/foo.py"))

    def test_versioned_interpreter_with_cap_approved(self):
        # python3.12 -m pytest WITH a wall-clock wrapper — sanctioned.
        assert not _is_deny(_run("gtimeout 90 python3.12 -m pytest tests/"))


class TestGate2FailOpensClosed:
    """Regression lock for the 6 CRITICAL fail-opens the Gate-2 adversarial
    review (run after run_6af22b0d) found in the first draft. Each is a real
    uncapped pytest run that must now DENY. The first draft APPROVED all of
    these — the unbound 'wrapper anywhere' check is the root cause (PIT10)."""

    def test_decoy_wrapper_on_other_command_denied(self):
        # C1: gtimeout wraps `echo`, NOT pytest → pytest runs raw → DENY.
        assert _is_deny(_run("gtimeout 90 echo ok && pytest tests/"))

    def test_timeout_zero_denied(self):
        # C2: `timeout 0` means infinite (no cap) → DENY.
        assert _is_deny(_run("timeout 0 pytest tests/"))

    def test_gtimeout_zero_denied(self):
        assert _is_deny(_run("gtimeout 0 pytest tests/foo.py"))

    def test_decoy_alarm_mention_denied(self):
        # C3: perl prints the word 'alarm' but does not bound pytest → DENY.
        assert _is_deny(_run("perl -e 'print \"alarm\"' ; pytest tests/"))

    def test_alarm_zero_denied(self):
        # C3b: `alarm 0` cancels the alarm (no cap) → DENY.
        assert _is_deny(_run("perl -e 'alarm 0; exec @ARGV' pytest tests/"))

    def test_versioned_interpreter_no_cap_denied(self):
        # C5: python3.12 -m pytest with NO wall-clock wrapper → DENY (was
        # approved because `python3?` didn't match the versioned interpreter).
        assert _is_deny(_run("python3.12 -m pytest tests/"))


class TestFailSafeApprove:
    """AC4: approve everything that is not a pytest invocation (fail-safe)."""

    def test_non_bash_approved(self):
        assert not _is_deny(_run("pytest | tail", tool_name="Read"))

    def test_non_pytest_approved(self):
        assert not _is_deny(_run("ls -la | tail -5"))

    def test_empty_command_approved(self):
        assert not _is_deny(_run(""))

    def test_filename_mentioning_pytest_not_a_run(self):
        # FALSE-POSITIVE TRAP: 'pytest' in a filename, not an invocation.
        assert not _is_deny(_run("cat pytest_output.log | tail -20"))

    def test_grep_pytest_in_file_approved(self):
        assert not _is_deny(_run("grep FAILED pytest.log | head"))

    def test_env_var_value_mentioning_pytest_approved(self):
        # env VALUE is literal 'pytest', the actual command is echo.
        assert not _is_deny(_run("VAR=pytest echo hi"))


class TestEnvPrefixedInvocation:
    """An env-assignment PREFIX before pytest is still a pytest invocation and
    must be guarded — denied without a wall-clock wrapper."""

    def test_env_prefix_no_wallclock_denied(self):
        assert _is_deny(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py"))

    def test_multi_env_prefix_no_wallclock_denied(self):
        assert _is_deny(_run("FOO=1 BAR=2 pytest tests/foo.py"))

    def test_env_prefix_pipe_denied(self):
        assert _is_deny(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py | tail"))

    def test_env_prefix_per_test_timeout_still_denied(self):
        # --timeout=60 is per-test → still denied even with env prefix.
        assert _is_deny(_run("PYTEST_ADDOPTS=-x pytest tests/foo.py --timeout=60"))

    def test_env_prefix_with_wallclock_approved(self):
        assert not _is_deny(
            _run("PYTEST_ADDOPTS=-x gtimeout 90 pytest tests/foo.py")
        )


class TestHardening:
    """Quote-stripping, runner wrappers, and the wall-clock false-positive edges."""

    def test_quoted_pipe_in_k_expr_with_wallclock_approved(self):
        # '| tail' is inside a -k quoted string (NOT a real pipe) AND the run is
        # wall-clock-wrapped → must approve. Isolates the quote-strip concern.
        assert not _is_deny(
            _run("gtimeout 90 pytest --timeout=60 -k 'a | tail'")
        )

    def test_quoted_pipe_without_wallclock_still_denied(self):
        # Same quoted pipe but NO wrapper → denied for the wall-clock reason
        # (NOT the pipe reason — the quoted pipe is correctly not seen as a pipe).
        result = _run("pytest --timeout=60 -k 'a | tail'")
        assert _is_deny(result)
        assert "gtimeout" in _deny_reason(result).lower()

    def test_bare_pytest_no_wallclock_denied(self):
        # Config-driven timeout in pyproject.toml does NOT count — the contract
        # requires an explicit wall-clock wrapper. XG override of the old
        # "config false-positive" carve-out.
        assert _is_deny(_run("pytest -p no:cacheprovider tests/"))

    def test_poetry_run_pytest_pipe_denied(self):
        assert _is_deny(_run("poetry run pytest tests/ | tail -5"))

    def test_uv_run_pytest_pipe_denied(self):
        assert _is_deny(_run("uv run pytest tests/ | head"))

    def test_uv_run_pytest_with_wallclock_approved(self):
        assert not _is_deny(_run("gtimeout 90 uv run pytest tests/"))


def _is_approve(result):
    """Approve = the guard returned the bare {'decision': 'approve'} shape
    (no hookSpecificOutput deny block)."""
    return result.get("decision") == "approve" and "hookSpecificOutput" not in result


# The test token is assembled at runtime so this test FILE itself does not trip
# the very guard it tests (the guard scans the literal command of each Bash call;
# a source line containing "...|pytest|..." in a string would false-positive a
# Bash `grep` over this file). PYT == "pytest".
PYT = "py" + "test"


class TestInQuoteFalsePositive:
    """Regression for the in-quote false-positive (run_5511508d).

    The invocation gate must NOT treat a pytest-runner token that appears ONLY
    inside a quoted span (a grep -E pattern, a -k expression, a commit message)
    as a real invocation. Fixed by requiring BOTH: the anchored
    _PYTEST_INVOCATION_RE matches the raw command AND a pytest TOKEN appears as a
    real command WORD (shlex-tokenized — _pytest_token_is_command_word). See the
    invocation gate in security_hooks.pytest_command_guard.

    Mutation contract (proves the test is not vacuous):
      - drop the token-is-command-word half (scan only the raw anchored regex)
        -> the in-quote APPROVE cases below go RED (the original bug: harmless
        greps/commits get DENIED).
      - swap shlex for a regex quote-strip (`_PYTEST_TOKEN_RE.search(_strip_quoted)`)
        -> test_apostrophe_word_with_real_run_still_denied goes RED (the Gate-2
        HIGH: an apostrophe-in-a-word pairs with a later quoted arg, the strip
        deletes the real pytest token, and a genuine uncapped run fail-opens).
    """

    def test_session_grep_with_separator_token_in_quotes_approved(self):
        # AC1 — the EXACT grep that was DENIED twice this session. The '|' before
        # the token lives INSIDE the quoted -E pattern, so it is not a real pipe
        # into a pytest invocation.
        cmd = (
            "grep -iE 'screencapture|screenshot|peekaboo|swarmai|artifact_cli|"
            + PYT
            + "|alarm' /tmp/x.txt"
        )
        assert _is_approve(_run(cmd))

    def test_commit_message_with_separator_token_in_quotes_approved(self):
        # AC1 — a ';' separator before the token, inside a quoted commit message.
        assert _is_approve(_run("git commit -m 'refactor cfg; " + PYT + " now capped'"))

    def test_bare_pytest_still_denied(self):
        # AC2 — a REAL bare invocation with no wall-clock wrapper must still DENY.
        # The fix must not fail-open on genuine invocations.
        assert _is_deny(_run("python -m " + PYT + " backend/tests/foo.py --timeout=60"))

    def test_env_prefix_quoted_value_still_denied(self):
        # AC3 — the skeptic-found fail-open guard. An env-assignment prefix whose
        # VALUE is single-quoted is a REAL uncapped invocation: the anchor matches
        # the raw command AND the pytest token is outside quotes (survives
        # _strip_quoted), so the AND-rule still DENIES. (The earlier delete-based
        # "scan the stripped command for the anchor" approach fail-opened here.)
        assert _is_deny(_run("VAR='x' " + PYT + " a.py"))

    def test_perl_alarm_sanctioned_form_still_approved(self):
        # AC4 — the sanctioned wall-clock form. The alarm/exec tokens live INSIDE
        # the single-quoted -e arg; the wall-clock check runs on the RAW command,
        # so the cap stays detectable and the command APPROVES.
        cmd = (
            "perl -e 'alarm 90; exec @ARGV' python -m "
            + PYT
            + " backend/tests/foo.py --timeout=60 > /tmp/t.txt 2>&1"
        )
        assert _is_approve(_run(cmd))

    def test_apostrophe_word_with_real_run_still_denied(self):
        # Gate-2 HIGH guard: an apostrophe in an UNQUOTED word (it's) pairs with a
        # LATER legitimate single-quoted arg ('x'). A regex quote-strip would
        # delete everything between them — including the REAL pytest token — and
        # fail-open this genuine uncapped run to APPROVE. The shlex tokenizer is
        # immune (it lexes by true shell rules), so this still DENIES.
        assert _is_deny(_run("echo it's; " + PYT + " tests/ -k 'x'"))

    def test_unbalanced_quote_with_real_run_fails_closed(self):
        # An unterminated quote can't be proven in-quote → fail CLOSED (DENY a
        # real invocation rather than risk a hang slipping past the cap).
        assert _is_deny(_run("echo 'oops; " + PYT + " tests/"))
