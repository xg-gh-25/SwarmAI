#!/usr/bin/env python3
"""
compile_gate_wiring — transform a gate's Kiro preToolUse wiring into the equivalent
Claude Code hooks.PreToolUse wiring.

WHY: the gate SCRIPT is runtime-agnostic (one file, fires on both runtimes). Only the
WIRING dialect differs between runtimes. This compiler mechanizes that transform so a
gate authored once in SwarmWS/AIDLC (as a Kiro agent-spec entry) can be emitted for
Claude Code without hand-editing — the "compile once, enforce everywhere" moat.

Dialect map (Kiro -> Claude Code), verified against real files:
  container : clientConfig.kiroCli.hooks.preToolUse   ->  hooks.PreToolUse
  entry     : {matcher, command}                      ->  {matcher, hooks:[{type:"command", command}]}
  matcher   : "shell"                                 ->  "Bash"   (shell tool name differs)
  path      : "python3 {{aim:filepath:gates/x.py}}"   ->  "python3 gates/x.py"  (AIM template stripped)

Scope: Kiro -> Claude Code only (the two runtimes verifiable against live files here).
Adding a target = one more dialect entry; the transform stays data-driven.
"""
from __future__ import annotations

import json
import re
import sys

# matcher token per target runtime (Kiro "shell" == Claude Code "Bash").
_MATCHER_MAP = {"shell": "Bash"}

_AIM = re.compile(r"\{\{aim:filepath:([^}]+)\}\}")


def strip_aim(command: str) -> str:
    """`python3 {{aim:filepath:gates/x.py}}` -> `python3 gates/x.py`.

    Claude Code resolves a hook command relative to the repo root (see
    .claude/settings.json: `bash .claude/hooks/x.sh`), so the AIM template
    collapses to the plain repo-root-relative path.

    Fail-LOUD (Gate-2): if the command still contains a `{{aim` sequence AFTER
    stripping filepath templates, an unknown/malformed template (e.g.
    `{{aim:other:x}}`) would leak into the emitted command as a literal — a
    structurally-valid but broken hook. Raise rather than emit a broken gate.
    """
    out = _AIM.sub(lambda m: m.group(1), command)
    if "{{aim" in out:
        raise ValueError(
            f"unresolved/malformed aim template in command: {command!r} "
            "(only {{aim:filepath:...}} is supported)"
        )
    return out


def compile_kiro_to_claude(kiro_entry: dict) -> dict:
    """One Kiro preToolUse entry -> one Claude Code PreToolUse entry.

    Kiro:   {"matcher": "shell", "command": "...", "description": "..."}
    Claude: {"matcher": "Bash",  "hooks": [{"type": "command", "command": "..."}]}
    """
    matcher = kiro_entry.get("matcher", "")
    matcher = _MATCHER_MAP.get(matcher, matcher)
    command = strip_aim(kiro_entry.get("command", ""))
    # Fail-LOUD (Gate-2): an empty command/matcher yields a structurally-valid but
    # FUNCTIONALLY-DEAD hook (blocks nothing). Refuse to emit one.
    if not command.strip():
        raise ValueError(f"kiro entry has no command to compile: {kiro_entry!r}")
    if not matcher.strip():
        raise ValueError(f"kiro entry has no matcher to compile: {kiro_entry!r}")
    hook = {"type": "command", "command": command}
    if kiro_entry.get("description"):
        hook["statusMessage"] = kiro_entry["description"]
    return {"matcher": matcher, "hooks": [hook]}


def compile_spec_pretooluse(agent_spec: dict) -> dict:
    """Extract a Kiro agent-spec's preToolUse entries and emit a Claude Code
    `{"hooks": {"PreToolUse": [...]}}` block ready to merge into .claude/settings.json."""
    kiro_entries = (
        agent_spec.get("clientConfig", {})
        .get("kiroCli", {})
        .get("hooks", {})
        .get("preToolUse", [])
    )
    return {"hooks": {"PreToolUse": [compile_kiro_to_claude(e) for e in kiro_entries]}}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: compile_gate_wiring.py <kiro-agent-spec.json>", file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        spec = json.load(f)
    print(json.dumps(compile_spec_pretooluse(spec), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
