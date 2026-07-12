#!/usr/bin/env python3
"""
Knockout test for the no_git_push preToolUse gate.

Three classes:
  BLOCK  — real `git push` invocations must exit 2
  ALLOW  — non-push commands + decoys (mentions of "git push" as data) exit 0
  (WIRED test omitted in the SwarmWS-native DDD: agent-specs are an
   AIM-export-form member, generated at export — not present in the native
   skeleton. The wiring test is re-added when this DDD is packaged for AIM.)

Run: python3 -m pytest gates/test_no_git_push.py -x   (or: python3 gates/test_no_git_push.py)
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_GATE = _HERE / "no_git_push.py"

# Real git-push invocations — MUST be blocked (exit 2).
BLOCK = [
    "git push",
    "git push origin mainline",
    "git push -f",
    "git push --force-with-lease",
    "  git   push  ",
    "cd /tmp/x && git push",
    "git -C /repo push",
    # evasion attempts caught by Gate-2 adversarial (2026-07-12) — must all block:
    "git push;",
    "git push||true",
    "git push &",
    "GIT_DIR=/repo git push",
    "FOO=bar git push",
    "git -c a=b -C /repo push",
    "git --no-verify push",
    "sh -c 'git push'",
    "(git push)",
    "{ git push; }",
]

# Non-push commands + decoys where "git push" is DATA, not an invocation —
# MUST be allowed (exit 0). These are the false-positive traps.
ALLOW = [
    "git status",
    "git commit -m 'add gate'",
    "git pushup",                     # different subcommand, not push
    "echo git push",                  # mentions it, doesn't run it
    "grep 'git push' changelog.txt",  # searches for the string
    "git log --oneline",
    "echo 'run git push later'",      # git push as quoted data
    "# git push in a comment",        # comment, not an invocation
    # AutoSDE f-<rev3> regression: a `push`-prefixed CONFIG KEY must NOT be mis-blocked.
    # The `push\b` anchor false-fired here because `\b` matches between `push` and `.`
    # and `-c`'s value-arg is optional (engine skips it, anchor grabs the `push` prefix).
    # Fixed by anchoring `push` as a bare command token (followed by ws/end/sep/quote).
    "git -c push.default=current fetch",   # real config command — NOT a push
    "git -c push.followTags=true commit",  # real config command — NOT a push
    "git -c push.default=simple pull",     # real config command — NOT a push
    "git config push.default simple",      # `config` subcommand, `push.default` is an arg
]


# The gate is runtime-agnostic. Kiro and Claude Code send DIFFERENT stdin shapes;
# the gate must fire on both. We parametrize every case over BOTH dialects.
_DIALECTS = {
    "kiro":   {"event_key": "hook_event_name", "event_val": "preToolUse",  "tool": "shell"},
    "claude": {"event_key": "hookEventName",   "event_val": "PreToolUse",  "tool": "Bash"},
}


def _run_gate(command: str, dialect: str = "kiro") -> int:
    d = _DIALECTS[dialect]
    payload = json.dumps({
        d["event_key"]: d["event_val"],
        "tool_name": d["tool"],
        "tool_input": {"command": command},
    })
    proc = subprocess.run(
        [sys.executable, str(_GATE)], input=payload,
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode


@pytest.mark.parametrize("dialect", list(_DIALECTS))
@pytest.mark.parametrize("command", BLOCK)
def test_block_class(command, dialect):
    assert _run_gate(command, dialect) == 2, \
        f"expected BLOCK (exit 2) for {command!r} on {dialect}"


@pytest.mark.parametrize("dialect", list(_DIALECTS))
@pytest.mark.parametrize("command", ALLOW)
def test_allow_class(command, dialect):
    assert _run_gate(command, dialect) == 0, \
        f"expected ALLOW (exit 0) for {command!r} on {dialect}"


@pytest.mark.parametrize("bad_event", [
    '{"hookEventName":123,"tool_name":"Bash","tool_input":{"command":"git push"}}',
    '{"hook_event_name":["preToolUse"],"tool_name":"shell","tool_input":{"command":"git push"}}',
    '{"hookEventName":{"x":1},"tool_name":"Bash","tool_input":{"command":"git push"}}',
    '{"tool_name":"Bash","tool_input":{"command":"git push"}}',  # no event-name key at all
])
def test_fail_open_on_malformed_event_name(bad_event):
    """A non-string / missing event-name must FAIL-OPEN (exit 0), never crash (exit 1).
    Gate-2 caught this: `.lower()` on a non-str value crashed, violating fail-open."""
    proc = subprocess.run([sys.executable, str(_GATE)], input=bad_event,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"must fail-OPEN, got exit {proc.returncode} for {bad_event!r}"


def test_fail_open_on_unparseable_stdin():
    proc = subprocess.run(
        [sys.executable, str(_GATE)], input="not json {{",
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, "gate must fail-OPEN on unparseable input"


def test_non_pretooluse_event_allows():
    payload = json.dumps({"hook_event_name": "postToolUse",
                          "tool_name": "shell",
                          "tool_input": {"command": "git push"}})
    proc = subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, "gate only fires on preToolUse"


# ReDoS attack vectors — each is a long option run whose tail is NOT `push`, so the
# match FAILS and a backtracking-vulnerable regex explores ~2^(N/2) paths. The gate
# had THREE overlap sources (dash-token / `=VAL` / trailing-push); the first fix
# closed only the dash-token one, so a `=VAL`-form input still hung (Gate-2 adversarial,
# 2026-07-12). We parametrize over ALL vectors so a partial regression is caught — a
# dash-only guard is structurally blind to the `=VAL` source.
_REDOS_VECTORS = {
    "dash_only":     lambda n: "git " + " ".join(f"-x{i}" for i in range(n)) + " notpush",
    "long_opt_eqval":lambda n: "git " + " ".join(f"--config{i}=val{i}" for i in range(n)) + " notpush",
    "short_eqval":   lambda n: "git " + " ".join("-a=b" for _ in range(n)) + " z",
    "mixed":         lambda n: "git " + " ".join((f"-x{i}" if i % 2 else f"--o{i}=v{i}") for i in range(n)) + " nope",
}


@pytest.mark.parametrize("vector", list(_REDOS_VECTORS))
@pytest.mark.parametrize("dialect", list(_DIALECTS))
def test_no_redos_on_pathological_option_runs(vector, dialect):
    """A long option run ending in a NON-push word must NOT hang the gate
    (catastrophic backtracking). Covers all three overlap sources — dash-token,
    `=VAL`, and mixed — not just the dash-only case the first fix addressed.

    Severity: an adversarial agent could weaponize the hang; on a runner that kills
    the timed-out hook the failure mode is fail-OPEN (the push slips through),
    defeating the gate. The pre-fix `--config=val` form exploded at N≈10 (~120 chars).
    This drives N=60 and asserts fast resolution (ALLOW — tail is not `push`).
    Mutation check: revert `_OPTS` to any overlapping form → the matching vector
    blows the 10s subprocess cap → RED.
    """
    import time
    pathological = _REDOS_VECTORS[vector](60)
    t = time.perf_counter()
    rc = _run_gate(pathological, dialect)
    elapsed = time.perf_counter() - t
    assert rc == 0, f"pathological non-push [{vector}] must ALLOW, got exit {rc}"
    assert elapsed < 3.0, f"gate took {elapsed:.2f}s on [{vector}] N=60 — ReDoS regression"


def test_redos_input_that_ends_in_push_still_blocks():
    """The anti-ReDoS fix must NOT weaken enforcement: the same long dashed command
    that DOES end in `push` (via a non-dash option arg path) still BLOCKS."""
    # non-dash option arguments are still consumed (e.g. `-C /path`), so a real
    # push behind many options blocks:
    cmd = "git -C /repo -c a=b --no-verify push"
    assert _run_gate(cmd, "kiro") == 2, "real push behind many options must still BLOCK"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
