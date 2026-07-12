#!/usr/bin/env python3
"""
PreToolUse gate: block `git push` in this package.

WHY: at Amazon, `git push` is forbidden — commits land locally and CRUX
auto-merge owns the remote (see amazon-builder-git.md: "Run `git commit` often,
but DO NOT ever run `git push`"). This gate COMPILES that governance rule into
executable enforcement: a shell `git push` invocation is blocked before it runs.

CONTRACT (Kiro preToolUse hook): read the event JSON on stdin; exit 2 (with a
reason on stderr) BLOCKS the tool; exit 0 ALLOWS it. Fail-OPEN: any parse/logic
error exits 0 — a gate bug must never brick the agent (fail-closed only on a
confirmed match). Mirrors the internal idiom (BraketAIContext block_prod_admin.py).
"""
import json
import re
import sys

# Match `git [opts...] push` as an INVOKED command sequence. `git` must sit at a
# COMMAND position — string start, or after a separator / operator / opening
# paren-brace-backtick / `sh -c '` / `bash -c '` — optionally preceded by env
# assignments (FOO=bar, GIT_DIR=/x). Any run of option tokens (-f, --force-with-lease,
# -c a=b, -C /path, --no-verify) may sit between `git` and `push`. The trailing `push`
# is anchored as a bare command token (see `_PUSH_TOKEN_END` below), so `git pushup`
# and the config key `push.default` are both safe.
#
# This deliberately does NOT match `git push` appearing as DATA (echo/grep arg, a
# commit message, a comment) — matching those would be a trust-eroding false-positive.
#
# HONEST BOUNDARY (this gate raises the bar, it is not airtight): regex cannot fully
# parse shell. A determined agent can still evade via indirection the parser can't see
# — e.g. `g=git; $g push`, an alias, base64-decoded command, or writing to `.git/config`
# directly. This gate stops the ACCIDENTAL / naive `git push` (the real threat: an agent
# that doesn't know pushing is forbidden), the same class SwarmAI's own dangerous_command_gate
# targets. Airtight enforcement belongs at the transport (no push credentials / CRUX-only
# remote), not a command-string gate.
_CMD_START = r"(?:^|[\n;&|(){}`]|\|\||&&|\bsh\s+-c\s+['\"]?|\bbash\s+-c\s+['\"]?)"
_ENVS = r"(?:\s*[A-Za-z_][A-Za-z0-9_]*=\S+)*"
# ReDoS-hardened option matcher. The naive form
# `(?:\s+-{1,2}\S+(?:=\S+)?(?:\s+\S+)?)*` had THREE overlapping ambiguities that
# each let the engine explore ~2^(N/2) backtrack paths on a failing match (a long
# option run whose tail is NOT `push`) — a hang an adversarial agent could weaponize;
# on a runner that kills the timed-out hook that is fail-OPEN (the push slips through),
# defeating the gate. The overlaps: (1) a dash token claimable by the prev iteration's
# optional arg OR the next iteration's option; (2) the leading `\S+` swallowing `=VAL`
# that `(?:=\S+)?` could also take (`--config=val` exploded at N≈10); (3) the optional
# arg swallowing a trailing `push` the anchor needs.
# Fix (all three closed, verified flat <0.4ms at N=200 across dash / --cfg=val / -a=b /
# mixed vectors; 31/31 enforcement cases correct):
#   • `(?>-{1,2}[^\s=]+(?:=\S+)?)` — atomic option token; `[^\s=]+` can't overlap `=VAL`,
#     and the atomic group blocks intra-token backtracking.
#   • `(?:\s+(?!-)\S+)?` — an option's value arg is one non-dash token.
# (Gate-2 adversarial, 2026-07-12: caught that the first fix — only the `(?!-)` on the
# arg — left overlaps (2) and (3) live; `--config=val` still hung at N≈10.)
_OPTS = r"(?:\s+(?>-{1,2}[^\s=]+(?:=\S+)?)(?:\s+(?!-)\S+)?)*"

# The trailing `push` must be a BARE command token — followed by whitespace, end-of-
# string, a shell separator, or a closing quote/backtick. NOT `push` followed by `.` /
# `-` / `=` (a config key or pseudo-subcommand).
# AutoSDE f-<rev3>, 2026-07-12: the earlier `push\b` anchor false-blocked
# `git -c push.default=current fetch` — `\b` matches between `push` and `.`, and because
# `-c`'s value-arg is OPTIONAL the engine skips consuming `push.default`, so `\s+push\b`
# grabs the `push` prefix of the config key. Fixing the value-arg lookahead could NOT
# solve this (the arg is optional — the engine just declines it); the real discriminator
# is at the anchor: only a bare `push` token is the subcommand. `[;&|)'"``]` covers
# `git push;`, `(git push)`, and `sh -c 'git push'` (push before a closing quote).
_PUSH_TOKEN_END = r"""(?=\s|$|[;&|)'"`])"""
_GIT_PUSH = re.compile(_CMD_START + _ENVS + r"\s*git\b" + _OPTS + r"\s+push" + _PUSH_TOKEN_END)

# Shell tool name varies by runtime: Kiro uses "shell"/"execute_bash", Claude Code
# uses "Bash". This gate is runtime-agnostic — it fires on all of them.
_SHELL_TOOLS = {"shell", "execute_bash", "Bash"}


def check_tool_input(tool_name: str, tool_input: dict) -> str | None:
    """Return a block-reason string if this call must be blocked, else None."""
    if tool_name in _SHELL_TOOLS:
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if _GIT_PUSH.search(command):
            return (
                "BLOCKED: `git push` is not permitted in this package. Commits are "
                "local-only — CRUX auto-merge owns the remote (amazon-builder-git.md). "
                "Use `git commit` then create a CR with `cr`."
            )
    return None


def _evaluate(event: dict) -> str | None:
    # Event-name field differs by runtime: Kiro `hook_event_name`, Claude Code
    # `hookEventName`; value case differs too (preToolUse vs PreToolUse). Coerce to
    # str defensively — a malformed non-str value must not crash the gate (fail-open).
    raw = event.get("hook_event_name") or event.get("hookEventName") or ""
    event_name = raw if isinstance(raw, str) else ""
    if event_name.lower() != "pretooluse":
        return None
    return check_tool_input(event.get("tool_name", ""), event.get("tool_input", {}))


def main() -> None:
    # Blanket fail-OPEN: a gate BUG (bad input shape, unexpected type) must NEVER
    # brick the agent — only a CONFIRMED git-push match blocks. Any exception in
    # parsing/evaluation exits 0 (allow). Fail-CLOSED only on the deliberate block below.
    try:
        event = json.load(sys.stdin)
        reason = _evaluate(event)
    except Exception:
        sys.exit(0)

    if reason:
        # BLOCK. exit 2 is honored by BOTH Kiro and Claude Code. The decision JSON
        # on stderr matches Claude Code's block convention (see SwarmAI's own
        # .claude/hooks/reject-pytest-tail.sh: `{"decision":"block",...}` >&2 + exit 2);
        # Kiro reads exit 2 + surfaces stderr as the reason. One output form, both runtimes.
        print(json.dumps({"decision": "block", "reason": reason}), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)  # ALLOW


if __name__ == "__main__":
    main()
