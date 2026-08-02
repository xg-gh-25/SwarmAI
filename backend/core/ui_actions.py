"""Agent UI-action (ACT) channel — proprioception Run 2 (efferent/传出).

The afferent half (Run 1, SENSE) lets the agent PERCEIVE its own UI state. This
module is the efferent half: it lets the agent ACT on the UI — from a chat turn,
call the `ui_action` tool with an enum command, and the frontend executes it by
re-dispatching to the EXISTING `swarm:*` window handlers.

Security model (the whole point of this module — Gate-1 hardened):

1. **Structured enum, no raw injection.** The agent picks a `cmd` from a fixed
   set; it can NEVER supply a raw `swarm:*` event string. The event name is
   derived server-side from UI_COMMAND_ALLOWLIST, never from agent input.
2. **Fail-closed allowlist.** A cmd not in UI_COMMAND_ALLOWLIST is rejected —
   `validate_ui_command` returns None, `build_ui_command_event` returns None, and
   the orchestrator emits nothing. The allowlist is the ONLY authority.
3. **Non-destructive nav/display only.** Run 2's allowlist is limited to opening
   Canvas, switching a fullscreen nav overlay, and returning to chat — all
   `window`-target, none carrying a data payload. Explicitly EXCLUDED (would be a
   real hazard if reachable): `open-terminal-here` (spawns a PTY),
   `inject-chat-input` (could self-drive the chat), `open-file` (its resolver
   allows arbitrary host paths → information disclosure — Gate-1 BLOCK 3).
4. **Frontend owns the mapping (defense in depth).** The SSE event carries `cmd`
   (+ event/target for logging parity), but the frontend derives its dispatch
   event+target from ITS OWN cmd-keyed table — it does not trust the wire's event
   name. So even a buggy/compromised backend can only pick from the enum.

Mechanism: the agent calls the `ui_action` tool (a `create_sdk_mcp_server`
in-process tool). The tool body validates `cmd` and returns an ACK that points
the agent at the passive next-turn SENSE readback (what to verify in the
"## Current UI State" snapshot) — it is NOT synchronous confirmation, since the
tool body cannot observe the frontend. The streaming orchestrator observes the
`ToolUseBlock` by name, validates `cmd` again, and yields an additive
`{type: "ui_command", cmd, event, target}` SSE event (the SDK still delivers the
tool's normal result to the agent). This mirrors the `file_changed` emit pattern.
This ACK closes the proprioception feedback arc: ACT (this emit) → next-turn
SENSE snapshot → the agent verifies the effect took hold.
"""
from __future__ import annotations

import logging
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class UiCommandEntry(TypedDict):
    event: str  # the swarm:* CustomEvent name the frontend will dispatch
    target: str  # "window" | "document" — which EventTarget the listener is on


# ── The allowlist — the security SSOT ───────────────────────────────────────
# Every entry is a VERIFIED-LIVE, non-destructive, non-data-carrying nav/display
# command. Keep in sync with desktop/src/utils/uiCommands.ts UI_COMMAND_TABLE.
#
# Run 2 scope = window-target nav/display only:
#   - open-canvas          → ThreeColumnLayout window listener
#   - back-to-chat         → useExclusiveOverlay window listener (closes overlays)
#   - show-* (8 overlays)  → useExclusiveOverlay ALL_SHOW_EVENTS (all window)
# DROPPED on purpose: open-file (host-path infoleak), toast/nav-activate (no
# live listener), show-library (not in ALL_SHOW_EVENTS). Anything side-effecting
# is NEVER added without an explicit human decision (STEERING / EVALUATE gate).
UI_COMMAND_ALLOWLIST: dict[str, UiCommandEntry] = {
    "open-canvas": {"event": "swarm:open-canvas", "target": "window"},
    "back-to-chat": {"event": "swarm:back-to-chat", "target": "window"},
    "show-swarmws": {"event": "swarm:show-swarmws", "target": "window"},
    "show-brain-hub": {"event": "swarm:show-brain-hub", "target": "window"},
    "show-context": {"event": "swarm:show-context", "target": "window"},
    "show-pipeline": {"event": "swarm:show-pipeline", "target": "window"},
    "show-pollinate": {"event": "swarm:show-pollinate", "target": "window"},
    "show-history": {"event": "swarm:show-history", "target": "window"},
    "show-todo": {"event": "swarm:show-todo", "target": "window"},
    "show-jobs": {"event": "swarm:show-jobs", "target": "window"},
}

# The enum the agent chooses from — sorted for a stable tool schema.
UI_COMMAND_IDS: list[str] = sorted(UI_COMMAND_ALLOWLIST.keys())


def validate_ui_command(cmd: object) -> Optional[UiCommandEntry]:
    """Return the allowlist entry for *cmd*, or None (fail-closed).

    Rejects: unknown ids, destructive/excluded ids, raw ``swarm:*`` strings, and
    any non-str / empty input. This is the single authority — a None result means
    "never emit, never dispatch".
    """
    if not isinstance(cmd, str) or not cmd:
        return None
    return UI_COMMAND_ALLOWLIST.get(cmd)


def _expected_post_state(cmd: str) -> str:
    """The observable post-state to verify AFTER dispatching *cmd*, phrased as the
    SENSE-rendered signal the agent will actually see next turn.

    The agent perceives its UI via the "## Current UI State" section that
    prompt_builder._render_ui_context_section injects — which is PROSE, not the
    raw schema fields. So this points at the RENDERED line, not `canvas.open` /
    `active_overlay`:
      - Canvas  → the "Canvas (output panel): … open …" line
      - overlay → the "Open overlay: …" line (we echo the cmd id rather than the
                  human label, so this never drifts from _OVERLAY_LABELS and needs
                  no import of prompt_builder — which imports THIS module).
      - back-to-chat → that "Open overlay:" line should be ABSENT (overlays closed).

    Assumes *cmd* is already allowlist-validated (callers gate on validate_ui_command).
    """
    if cmd == "open-canvas":
        return (
            "the 'Current UI State' section should show a "
            "'Canvas (output panel): … open …' line"
        )
    if cmd == "back-to-chat":
        return (
            "the 'Current UI State' section should have NO 'Open overlay:' line "
            "(overlays closed, back to chat/Canvas)"
        )
    if cmd.startswith("show-"):
        return (
            "the 'Current UI State' section should show an 'Open overlay:' line "
            f"for the '{cmd}' view"
        )
    # Any other allowlisted cmd (future-proofing) — generic pointer, still honest.
    return (
        f"the 'Current UI State' section should reflect the '{cmd}' action"
    )


def build_ui_ack(cmd: object) -> Optional[str]:
    """Build the SUCCESS ack text for an allowlisted *cmd*, or None if not
    allowlisted (fail-closed — the caller returns the rejection instead).

    The ack is NOT a synchronous confirmation (the tool body cannot see the
    frontend). It is a pointer to the PASSIVE next-turn SENSE readback: it names
    what the agent should verify in the 'Current UI State' snapshot on its next
    turn, and says the effect is unconfirmed until then.
    """
    if validate_ui_command(cmd) is None:
        return None
    expected = _expected_post_state(cmd)  # type: ignore[arg-type]  # validated str
    return (
        f"Dispatched '{cmd}' to the UI. This is NOT synchronous confirmation — "
        f"the frontend applies it after this turn. On your NEXT turn, verify: "
        f"{expected}. If it doesn't, the command did not reach the UI."
    )


def build_ui_command_event(cmd: object) -> Optional[dict]:
    """Build the ``ui_command`` SSE payload for *cmd*, or None if not allowlisted.

    Fail-closed at the source: a non-allowlisted cmd yields no event. The payload
    carries the derived event/target for frontend cross-check + logging parity —
    but the frontend re-derives its own dispatch from its own table (never trusts
    these fields blindly).
    """
    entry = validate_ui_command(cmd)
    if entry is None:
        logger.warning("ui_action: rejected non-allowlisted cmd %r (fail-closed)", cmd)
        return None
    return {
        "type": "ui_command",
        "cmd": cmd,
        "event": entry["event"],
        "target": entry["target"],
    }


# ── The SDK-MCP tool the agent calls ─────────────────────────────────────────

# Tool name as the agent sees it (SDK-MCP convention: mcp__<server>__<tool>).
UI_MCP_SERVER_NAME = "swarm_ui"
UI_ACTION_TOOL_NAME = "ui_action"
UI_ACTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{UI_ACTION_TOOL_NAME}"


def get_ui_mcp_server():
    """Build the in-process SDK-MCP server exposing the `ui_action` tool.

    The tool body is intentionally trivial: it validates and returns an ack
    string. The actual UI effect is produced by the streaming orchestrator, which
    observes this tool call and emits the `ui_command` SSE event. Returns the
    server config to merge into ClaudeAgentOptions.mcp_servers, or None if the SDK
    primitives are unavailable (fail-open on the SDK, not on the security gate).
    """
    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except Exception as e:  # pragma: no cover - SDK always present in prod
        logger.warning("ui_action tool unavailable (SDK import failed): %s", e)
        return None

    _cmd_list = ", ".join(UI_COMMAND_IDS)

    @tool(
        UI_ACTION_TOOL_NAME,
        (
            "Act on your OWN UI in the SwarmAI desktop app: open a panel or switch "
            "a navigation view for the user. Use when the user asks you to show/open "
            f"something in the app. cmd MUST be one of: {_cmd_list}. Non-destructive "
            "navigation only — this cannot write, delete, send, or run anything."
        ),
        {"cmd": str},
    )
    async def ui_action(args: dict) -> dict:
        cmd = args.get("cmd")
        entry = validate_ui_command(cmd)
        if entry is None:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Rejected: '{cmd}' is not an allowed UI command. "
                        f"Allowed: {_cmd_list}."
                    ),
                }],
                "is_error": True,
            }
        # The orchestrator emits the ui_command SSE event on observing this call.
        # The ack points the agent at the passive next-turn SENSE readback (what to
        # verify in "## Current UI State") — it is NOT synchronous confirmation.
        return {"content": [{"type": "text", "text": build_ui_ack(cmd)}]}

    return create_sdk_mcp_server(name=UI_MCP_SERVER_NAME, tools=[ui_action])
