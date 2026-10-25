"""``python-backend hook <event> --host <host>``: a coding agent host's hook calls this
with its event JSON on stdin (design 2026-10-25-pipeline-standalone §4.1).

The command only maps the host's fields onto the host-neutral functions a SwarmAI
session's in-process hooks call too (``run_protocol.hook_bridge``); it decides nothing
itself. It never blocks the host: any failure is written to stderr and the command
still exits 0 with an empty answer, so a broken pipeline never stops someone's agent.

Events:
- ``pre-tool-use`` — an agent spawn whose prompt is a handout's becomes a ``spawn``.
- ``subagent-stop`` — the expert's answer, read from its own transcript (spawn prompt
  at the head) and the host's last-message field.

Hosts: ``claude`` (Claude Code). Codex and Kiro are added in standalone T4 / T5.

Public symbols: ``EVENTS``, ``HOSTS``, ``main``.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EVENTS = ("pre-tool-use", "subagent-stop")
HOSTS = ("claude",)
# the host's tool names for "spawn a sub-agent"
_SPAWN_TOOLS = {"claude": ("Agent", "Task")}
_MAX_INPUT = 4 * 1024 * 1024


def _field(payload: Any, name: str, default: Any = "") -> Any:
    return payload.get(name, default) if isinstance(payload, dict) else default


def _handle(event: str, host: str, payload: dict) -> None:
    from core.run_protocol import hook_bridge
    if event == "pre-tool-use":
        if _field(payload, "tool_name") not in _SPAWN_TOOLS[host]:
            return
        tool_input = _field(payload, "tool_input", {})
        prompt = _field(tool_input, "prompt")
        hook_bridge.on_spawn(prompt if isinstance(prompt, str) else "",
                             str(_field(payload, "tool_use_id") or ""))
        return
    # subagent-stop
    from core import subagent_transcript
    path = _field(payload, "agent_transcript_path")
    path = path if isinstance(path, str) and path else ""
    head = subagent_transcript.read_prompt_head(path) if path else ""
    final = _field(payload, "last_assistant_message")
    if not (isinstance(final, str) and final.strip()):
        final = subagent_transcript.read_final_text(path) if path else ""
    hook_bridge.on_subagent_stop(head, final, transcript_path=path or None)


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="hook")
    parser.add_argument("event", choices=EVENTS)
    parser.add_argument("--host", required=True, choices=HOSTS)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read(_MAX_INPUT + 1)
        if len(raw) > _MAX_INPUT:
            raise ValueError("hook input is larger than 4 MiB")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        _handle(args.event, args.host, payload)
    except Exception as exc:  # noqa: BLE001 — never block the host's agent
        print(f"swarmai pipeline hook {args.event}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
    print("{}")
    return 0
