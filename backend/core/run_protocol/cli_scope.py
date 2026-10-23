"""What one hook token authorizes: the scope of a pipeline command (B-class B-b).

The PreToolUse hook (run_owner_gate) issues a one-time token for every pipeline
command, bound to this scope; the daemon executes the command only when the token it
redeems carries the same scope. One function, used by both, so they cannot drift.

- ``scope(command, request_id)`` — ``land``; ``<cmd>:<request id>`` for the actions
  a person approves first (run-land / accept-finding / discard-snapshot executed with
  ``--request``); ``cli:<cmd>`` for every other pipeline_v1 command.
- ``AFTER_APPROVAL`` — the commands whose ``--request`` form is approved first.
"""
from __future__ import annotations

from typing import Optional

AFTER_APPROVAL = frozenset({"run-land", "accept-finding", "discard-snapshot"})


def scope(command: str, request_id: Optional[str]) -> str:
    if command == "land":
        return "land"
    if command in AFTER_APPROVAL and request_id:
        return f"{command}:{request_id}"
    return f"cli:{command}"


__all__ = ["AFTER_APPROVAL", "scope"]
