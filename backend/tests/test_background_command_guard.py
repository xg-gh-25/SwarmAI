"""Forcing tests for the background-command PreToolUse guard.

Tests that the guard ENTERS its decision path (not just that it exists):
- backgrounding (run_in_background flag OR shell &/nohup/setsid) is DENIED
- foreground commands and false-positive ampersands (&&, 2>&1) are APPROVED
- the long-lived-service allowlist (dev servers, --watch, tail -f) may background
- non-Bash tools are ignored

Closes the runaway hole the foreground 120s timeout cannot bound
(anthropics/claude-code#61568): a backgrounded find/pytest escapes the timeout
and can hang indefinitely.
"""
from __future__ import annotations

import pytest

from core.security_hooks import background_command_guard, _is_backgrounded


def _bash(command: str, *, run_in_background: bool | None = None) -> dict:
    tool_input: dict = {"command": command}
    if run_in_background is not None:
        tool_input["run_in_background"] = run_in_background
    return {"tool_name": "Bash", "tool_input": tool_input}


def _denied(result: dict) -> bool:
    return (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


# ── _is_backgrounded detection ────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "find desktop -maxdepth 2 -name 'vitest.config*' &",
    "python -m pytest tests/ &",
    "nohup ./long.sh",
    "setsid ./worker.sh",
    "sleep 100 & echo started",
    "./build.sh & disown",
])
def test_detects_shell_backgrounding(command):
    assert _is_backgrounded(command, {"command": command}) is True


def test_detects_run_in_background_flag():
    assert _is_backgrounded("find . -name x", {"run_in_background": True}) is True


@pytest.mark.parametrize("command", [
    "find desktop -name 'vite.config*'",        # plain foreground
    "make build && npm test",                    # && is logical-AND, not bg
    "python script.py 2>&1 | tee log",          # fd dup, not bg
    "grep -r foo src &> /dev/null",             # &> redirect, not bg
    "ls -la",
])
def test_foreground_and_false_positive_amps_not_flagged(command):
    assert _is_backgrounded(command, {"command": command}) is False


# ── run_3bde4b8b Bug2: heredoc-body & is DATA, not a control operator ──────────
# A heredoc body fed to a program's stdin can legitimately contain `&` (Python
# bitwise-and, an unquoted URL query string). The old strip pipeline missed
# heredoc bodies → false "backgrounded" → the command was wrongly DENIED. This
# guard's own false-positive blocked the pipeline that fixes it (3× this session).
@pytest.mark.parametrize("command", [
    "python3 - <<HDOC\nx = a & b\nHDOC",                    # bitwise-and in body
    "python3 - <<'EOF'\nx = a & b\nEOF",                    # quoted delimiter (Gate-1: strip before quote-strip)
    'python3 - <<"EOF"\ny = c & d\nEOF',                    # double-quoted delimiter
    "cat <<-EOF\n\tport=1&reset=2\n\tEOF",                  # <<- indented terminator (Gate-1 finding)
    "curl -d @- <<REQ\nhttp://x?a=1&b=2\nREQ",              # unquoted URL in body
    "python3 - <<A\nfoo & bar\nA\npython3 - <<B\nbaz & qux\nB",  # two heredocs, both bodies
])
def test_heredoc_body_amp_not_flagged(command):
    assert _is_backgrounded(command, {"command": command}) is False, (
        f"heredoc-body & is data, not backgrounding: {command!r}")


# A REAL backgrounding & that lives OUTSIDE the heredoc body (intro line or after
# the terminator) MUST still be flagged — the body strip must not swallow it.
@pytest.mark.parametrize("command", [
    "cat <<EOF & echo started\nbody & data\nEOF",          # & on the intro line
    "cat <<EOF\nbody & data\nEOF\n& echo done",            # & after the terminator
    # Gate-2 correctness (run_3bde4b8b): the delimiter WORD recurs as a bare body
    # line. bash ends the heredoc at the FIRST standalone delimiter line, so the
    # trailing `sleep 999 &` is REAL backgrounding. A prefix-match terminator would
    # let the body span swallow it (fail-open); the whole-line ^WORD$ anchor stops
    # at the first `E` line, leaving the real & detectable.
    "cat <<E\nE\nsleep 999 &\nE",
    # delimiter appears as a PREFIX of a body line — must not early-terminate
    "cat <<END & echo bg\nENDPOINT=1\ndata\nEND",
])
def test_heredoc_with_real_backgrounding_still_flagged(command):
    assert _is_backgrounded(command, {"command": command}) is True, (
        f"a real & outside the heredoc body must stay flagged: {command!r}")


def test_heredoc_redos_guard_bounds_runtime():
    """Gate-2 security HIGH: N unterminated `<<A` openers make the lazy body regex
    O(N^2). The opener-count cap (_HEREDOC_MAX_OPENERS) skips the strip on a
    pathological command so the <5s PreToolUse budget holds. Fail-SAFE: skipping
    the strip can only over-DENY (a body-& re-counts as bg), never fail-open."""
    import time
    pathological = "<<A\n" * 5000  # 5000 unterminated openers, ~20KB
    start = time.monotonic()
    result = _is_backgrounded(pathological, {"command": pathological})
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"heredoc strip must be bounded, took {elapsed:.2f}s"
    # no real `&` present → not backgrounded (the cap-skip path returns the raw
    # string to the &-scan, which finds none)
    assert result is False


# ── the guard decision (the path that was never exercised) ──────────────────

@pytest.mark.asyncio
async def test_backgrounded_find_is_denied():
    """The exact bug: a backgrounded find that hung ~10min — now blocked."""
    result = await background_command_guard(
        _bash("find desktop -maxdepth 2 -name 'vitest.config*' &"), None, None,
    )
    assert _denied(result)
    assert "foreground" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


@pytest.mark.asyncio
async def test_backgrounded_pytest_is_denied():
    result = await background_command_guard(
        _bash("python -m pytest tests/", run_in_background=True), None, None,
    )
    assert _denied(result)


@pytest.mark.asyncio
async def test_foreground_command_approved():
    result = await background_command_guard(
        _bash("find desktop -name 'vite.config*'"), None, None,
    )
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
async def test_logical_and_not_treated_as_background():
    result = await background_command_guard(
        _bash("brazil-build && npm test"), None, None,
    )
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    "npm run dev &",
    "vite &",
    "tail -f ~/.swarm-ai/logs/backend-daemon.log &",
    "tsc --watch &",
    "./dev.sh &",
])
async def test_longlived_services_may_background(command):
    """Genuine long-lived services are allowlisted to background."""
    result = await background_command_guard(_bash(command), None, None)
    assert result == {"decision": "approve"}


@pytest.mark.asyncio
async def test_non_bash_tool_ignored():
    result = await background_command_guard(
        {"tool_name": "Read", "tool_input": {"file_path": "x"}}, None, None,
    )
    assert result == {"decision": "approve"}


# ── a detach keyword is text only when every segment is a text program (_bg_keyword_is_text)
# A grep for the keywords, or a requirement text naming them, was denied as
# backgrounding three times in one session.
@pytest.mark.parametrize("command", [
    'grep -n "def _is_background\\|setsid\\|nohup" core/security_hooks.py | head -20',
    "grep -n 'nohup' tests/x.py",
    "grep -rn setsid backend/core",
    'python-backend pipeline-v1 create --requirement "names nohup / setsid / disown"',
    "echo nohup",
    "git log --grep=setsid",
    "cat > /tmp/x.py <<'EOF'\nimport os  # nohup setsid\nEOF",
])
def test_a_named_detach_keyword_is_data(command):
    assert _is_backgrounded(command, {"command": command}) is False


@pytest.mark.parametrize("command", [
    "/usr/bin/nohup ./long.sh",
    "sudo -u bob nohup ./x",
    "env A=1 setsid ./w",
    "timeout 60 nohup ./x",
    "cd /tmp && nohup ./x",
    "echo hi | nohup cat",
    "xargs -n 1 nohup ./x < list",
    "find . -name '*.sh' -exec setsid {} \\;",
    "bash -c 'nohup ./x'",
    'sh -lc "setsid ./w"',
    'eval "nohup ./x"',
    "bash <<'EOF'\nnohup ./x\nEOF",
    "echo 'nohup ./x' | sh",
    'nohup ./x "unterminated',
    # Gate-2 probes: each was approved by the first version of this change.
    'echo "$(setsid -f sleep 999)"',
    'ls "$(setsid -f sleep 999)"',
    'X="$(nohup ./a)"',
    "printf %s \"$(nohup ./x)\"",
    "nohup grep -r x /",
    "sudo nohup echo hi",
    'env -i bash -c "setsid -f sleep 999"',
    'sudo -u bob bash -c "nohup sleep 999 &"',
    'find . -maxdepth 0 -exec sh -c "setsid -f sleep 999" \\;',
    'su -c "setsid -f sleep 999"',
    "script -q -c 'setsid -f sleep 9' /dev/null",
    "ssh h 'nohup sleep 999 &'",
    "docker exec c sh -c 'nohup sleep 999 &'",
    "echo 'nohup ./x &' | env -i bash",
    'echo "setsid -f sleep 9" | xargs -I{} sh -c {}',
    'fish -c "setsid -f sleep 9"',
    "python3 -c \"import os; os.system('nohup sleep 999')\"",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.Popen(['setsid', '-f', 'x'])\nEOF",
    "node -e \"require('child_process').exec('nohup sleep 9')\"",
    "echo 'nohup sleep 999 &' > /tmp/x.sh; bash /tmp/x.sh",
    "grep -n setsid f && python3 x.py",
    "rg --pre ./run.sh setsid",
    "git -c core.pager=x log --grep=setsid",
    "echo `setsid -f sleep 9`",
    'python artifact_cli.py run-create --requirement "$(setsid -f sleep 9)"',
])
def test_a_detach_keyword_that_runs_is_still_caught(command):
    assert _is_backgrounded(command, {"command": command}) is True


# ── F7 (2026-10-21 stability design): a keyword only counts in the list it is in ──
def test_keyword_in_a_separate_text_list_is_not_backgrounding():
    """The 10-20 and 10-21 false denies: a read-only `grep setsid` after `sed -n`."""
    from core.security_hooks import _is_backgrounded
    for cmd in (
        'sed -n 1320,1365p backend/scripts/pipeline_test.py; grep -n "setsid\\|nohup" x.py | head',
        "cd core; sed -n 513,530p a.py; grep -n 'nohup' b.py | grep -n setsid | head -20",
        "awk '{print}' f && grep -c disown f",
    ):
        assert not _is_backgrounded(cmd, {}), cmd


def test_keyword_still_counts_where_it_can_run():
    from core.security_hooks import _is_backgrounded
    for cmd in (
        "nohup sleep 999",
        "sed -n 1p f; nohup sleep 999",
        "echo 'nohup sleep 9' > f.sh; bash f.sh",
        "echo setsid -f sleep 9 | xargs sh -c",
        "grep -l x . | sed 's/^/nohup /' | sh",
        "env -i nohup sleep 9",
        "python3 -c 'import os; os.system(\"nohup sleep 9\")'",
        "ls; find . -exec setsid sleep 9 \\;",
        "cat f; echo `nohup sleep 9`",
    ):
        assert _is_backgrounded(cmd, {}), cmd
