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
import os
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
#   - show-* (9 overlays)  → useExclusiveOverlay ALL_SHOW_EVENTS (all window)
# DROPPED on purpose: open-file (host-path infoleak), toast/nav-activate (no
# live listener), show-library (not in ALL_SHOW_EVENTS). Anything side-effecting
# is NEVER added without an explicit human decision (STEERING / EVALUATE gate).
UI_COMMAND_ALLOWLIST: dict[str, UiCommandEntry] = {
    "open-canvas": {"event": "swarm:open-canvas", "target": "window"},
    # open-canvas-file: open a CURRENT-workspace file in Canvas (run_c0550cc2). This
    # is DISTINCT from the dropped raw `open-file`: it carries a `path` that rides the
    # EXISTING generic swarm:open-file → /workspace/file/resolve filter, which is
    # workspace-scoped and rejects abs/host paths + `..` traversal (returns 400 →
    # frontend drops). So ui_action adds NO path validation of its own — the generic
    # canvas file filter is the single authority. document-target (all open-file
    # dispatchers listen on document, per useCanvasHost EVENT-TARGET CONTRACT).
    "open-canvas-file": {"event": "swarm:open-file", "target": "document"},
    # Canvas layout commands (canvas-proprioception.md §4): fold to the side rail and
    # pick the Artifacts / Changes tab. Payload-less (the tab is part of the command
    # name, not a parameter); the frontend records each outcome for the next turn's
    # snapshot. Named canvas-* (NOT show-*): show-* is the overlay family.
    "fold-canvas": {"event": "swarm:canvas-fold", "target": "window"},
    "canvas-tab-artifacts": {"event": "swarm:canvas-tab-artifacts", "target": "window"},
    "canvas-tab-changes": {"event": "swarm:canvas-tab-changes", "target": "window"},
    "back-to-chat": {"event": "swarm:back-to-chat", "target": "window"},
    "show-swarmws": {"event": "swarm:show-swarmws", "target": "window"},
    "show-brain-hub": {"event": "swarm:show-brain-hub", "target": "window"},
    "show-context": {"event": "swarm:show-context", "target": "window"},
    "show-pipeline": {"event": "swarm:show-pipeline", "target": "window"},
    "show-pollinate": {"event": "swarm:show-pollinate", "target": "window"},
    "show-history": {"event": "swarm:show-history", "target": "window"},
    # Artifacts — History's twin: opens the recent-artifacts selector overlay
    # (read-only; a row click opens a file in the current-tab Canvas). Payload-less
    # show-only, same safety class as show-history.
    "show-artifacts": {"event": "swarm:show-artifacts", "target": "window"},
    "show-todo": {"event": "swarm:show-todo", "target": "window"},
    "show-jobs": {"event": "swarm:show-jobs", "target": "window"},
    # Capabilities domain (run_b5d98151) — opens the CapabilitiesOverlay
    # ("what your AI can do" — skills + connections). Payload-less show-only:
    # frontend derives event/target from its own table, never trusts wire data;
    # internal-skill filtering is enforced backend-side regardless of opener.
    "show-capabilities": {"event": "swarm:show-capabilities", "target": "window"},
    # New Brain launcher — non-destructive: opens the collect-modal only; "Create"
    # still routes through chat (autoSend:false, human reviews before send). Safe
    # for the agent to open per the Run-2 nav/display charter.
    "show-new-brain": {"event": "swarm:show-new-brain", "target": "window"},
    # Unified Need You channel (2026-08-08) — opens the needs-you overlay (the
    # AlertsPill's fullscreen view). Payload-less show-only nav; the agent uses it
    # to ACT on its own attention queue when the user says "show me Need You".
    "show-needs-you": {"event": "swarm:show-needs-you", "target": "window"},
    # Community overlay (run_5165013e) — opens the CommunityOverlay (SwarmAI's
    # two-way membrane: inbound signals + outbound engagement). Payload-less
    # show-only nav; the agent opens it when the user asks about signals /
    # community / subscriptions. Read-only in Phase-1.
    "show-community": {"event": "swarm:show-community", "target": "window"},
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
    if cmd in _CANVAS_CMDS:
        return (
            "the 'Current UI State' section lists it under 'Your Canvas commands since "
            "the last message' as applied, ignored (with the reason, e.g. file not "
            "found or unsaved edits) or pending (opening / folding during your answer "
            "applies when the answer finishes), and the 'Canvas (output panel):' and "
            "'File:' lines show the result"
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


def build_ui_ack(cmd: object, path: object = None) -> Optional[str]:
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
    target = f"'{cmd}'"
    if cmd in _PATH_CARRYING_CMDS and isinstance(path, str) and path:
        target = f"'{cmd}' (path={path})"
    return (
        f"Dispatched {target} to the UI. This is NOT synchronous confirmation — "
        f"the frontend applies it after this turn. On your NEXT turn, verify: "
        f"{expected}. If it doesn't, the command did not reach the UI."
    )


# Canvas commands whose outcome the frontend reports back in the next snapshot.
_CANVAS_CMDS = frozenset({"open-canvas", "open-canvas-file", "fold-canvas",
                          "canvas-tab-artifacts", "canvas-tab-changes"})

# Commands that carry a `path` payload (per-cmd opt-in, mirroring the frontend's
# "re-introduce a payload ONLY per-cmd" rule). ONLY open-canvas-file: its path is
# NOT validated here — it rides the generic workspace-scoped swarm:open-file filter
# (/workspace/file/resolve), which is the single authority that rejects abs/host
# paths + `..`. A pure-nav command never gains a path key even if one is supplied.
_PATH_CARRYING_CMDS = frozenset({"open-canvas-file"})


def build_ui_command_event(
    cmd: object, path: object = None, allow_abs: bool = False
) -> Optional[dict]:
    """Build the ``ui_command`` SSE payload for *cmd*, or None if not allowlisted.

    Fail-closed at the source: a non-allowlisted cmd yields no event. The payload
    carries the derived event/target for frontend cross-check + logging parity —
    but the frontend re-derives its own dispatch from its own table (never trusts
    these fields blindly).

    ``path`` is attached ONLY for the path-carrying commands (open-canvas-file)
    and IGNORED for every other (pure-nav) command. This function IS a
    session-type security gate for the path (run_c0550cc2 / run_cbaecb86) — it is
    the *sole authority* on whether the agent may reach the resolver with an
    absolute host path:

      * ``..`` traversal is ALWAYS rejected (every session type).
      * an ABSOLUTE path is rejected UNLESS ``allow_abs`` is True. The caller sets
        ``allow_abs = not self._parent._has_channel_context`` — i.e. True only on a
        genuine LOCAL DESKTOP session (owner already has host file-picker access
        there), False on ANY channel incl. owner-over-channel (C041 leak defense).

    When ``allow_abs`` is True and an absolute path is admitted, the event carries
    ``allowAbs: True`` so the frontend's independent leading-``/`` reject (defense
    in depth) knows this backend-authored path was session-type-authorized. The
    frontend STILL rejects ``~`` and ``..`` unconditionally.
    """
    entry = validate_ui_command(cmd)
    if entry is None:
        logger.warning("ui_action: rejected non-allowlisted cmd %r (fail-closed)", cmd)
        return None
    ev = {
        "type": "ui_command",
        "cmd": cmd,
        "event": entry["event"],
        "target": entry["target"],
    }
    # Attach path ONLY for a path-carrying cmd AND only when a non-empty str given.
    if cmd in _PATH_CARRYING_CMDS and isinstance(path, str) and path:
        # SECURITY (Gate-2 CRITICAL, run_c0550cc2 + run_cbaecb86): the AGENT efferent
        # channel is workspace-RELATIVE only, EXCEPT a genuine local-desktop owner
        # session (allow_abs=True) where absolute paths are legitimate (the owner can
        # already open any host file via the picker). The downstream
        # /workspace/file/resolve HAPPILY resolves an ABSOLUTE host path
        # (/etc/passwd, ~/.aws/credentials) — that branch exists for USER-CLICK opens
        # of source-repo files, and the agent must NOT reach it on ANY channel
        # (owner-over-channel included: the owner on Slack can't browse the host FS,
        # so a prompt-injected turn could exfiltrate a host file into the channel —
        # the exact C041/Gate-1-BLOCK-3 infoleak). `..` traversal is rejected in ALL
        # cases. Fail-closed: reject → drop the whole event (no open).
        _norm = os.path.normpath(path)
        _is_abs = os.path.isabs(_norm)
        if _norm.startswith("..") or (_is_abs and not allow_abs):
            logger.warning(
                "ui_action: rejected open-canvas-file with non-workspace-relative "
                "path %r (allow_abs=%s — agent channel is workspace-relative unless "
                "local-desktop owner)", path, allow_abs
            )
            return None
        ev["path"] = path
        if _is_abs:
            # Audit trail: a sensitive capability (absolute host-file open) was
            # authorized for a local-desktop session. Also tells the frontend this
            # abs path was session-type-authorized (relaxes its leading-/ reject).
            logger.info(
                "ui_action: ALLOWED open-canvas-file absolute path %r "
                "(local-desktop owner session, allow_abs=True)", path
            )
            ev["allowAbs"] = True
    return ev


UI_MCP_SERVER_NAME = "swarm_ui"
UI_ACTION_TOOL_NAME = "ui_action"
UI_ACTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{UI_ACTION_TOOL_NAME}"
SENSE_ATTENTION_TOOL_NAME = "sense_attention"
SENSE_ATTENTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{SENSE_ATTENTION_TOOL_NAME}"


def format_attention_for_agent(brain: Optional[str] = None) -> str:
    """Render the unified Need You queue as agent-readable text (the SENSE half of
    the Need You channel — the afferent complement to the `show-needs-you` ACT).

    Reads the SAME backend authority the frontend overlay + brain-card use
    (core.attention_authority.collect), so the agent sees EXACTLY what the user
    sees — one source of truth. Read-only; never mutates. Grouped by tier
    (BLOCKING first) then brain, mirroring the overlay's double-axis layout so the
    agent can relay it faithfully and then dispatch each item (resume run / review
    proposal / triage job) via a normal chat action.
    """
    try:
        from .attention_authority import collect
        from jobs.paths import SWARMWS
    except Exception as e:  # pragma: no cover - import guard
        return f"(Could not load the attention authority: {e})"

    result = collect(SWARMWS, brain=brain)
    if not result.items:
        scope = f" for brain '{brain}'" if brain else ""
        return f"Need You{scope}: nothing needs you right now (0 items)."

    lines: list[str] = [
        f"Need You — {result.counts['blocking']} blocking, "
        f"{result.counts['review']} review"
        + (f" (brain '{brain}' only)" if brain else "") + ":",
    ]
    for tier, label in (("blocking", "🔴 BLOCKING (stopped — needs you now)"),
                        ("review", "🟡 REVIEW (self-advancing — confirm/override)")):
        tier_items = [it for it in result.items if it.tier == tier]
        if not tier_items:
            continue
        lines.append(f"\n{label}:")
        # group by brain (None → OS-level), preserving order
        seen_brains: list[Optional[str]] = []
        for it in tier_items:
            if it.brain not in seen_brains:
                seen_brains.append(it.brain)
        for b in seen_brains:
            lines.append(f"  [{b or 'OS-level'}]")
            for it in (x for x in tier_items if x.brain == b):
                lines.append(f"    • ({it.source}) {it.title}")
                msg = (it.dispatch or {}).get("message")
                if msg:
                    lines.append(f"        → to handle: {msg}")
    return "\n".join(lines)


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
            "Act on your OWN UI in the SwarmAI desktop app: open a panel, switch a "
            "navigation view, or open one of THIS session's workspace files in Canvas. "
            "Use ONLY when the user asks you to show/open/fold something in the app. "
            f"cmd MUST be one of: {_cmd_list}. Canvas: 'open-canvas' opens and unfolds "
            "it, 'fold-canvas' folds it to the side rail, 'canvas-tab-artifacts' / "
            "'canvas-tab-changes' pick its tab (they do not open a closed or folded "
            "Canvas: for \"show me the changes\" call open-canvas, then "
            "canvas-tab-changes), 'open-canvas-file' shows a file — pass "
            "`path` = the workspace-relative file path (e.g. 'Knowledge/Designs/foo.md'); "
            "only files inside the workspace open. A file with unsaved edits is never "
            "replaced (the open is ignored). Opening or folding while you are still "
            "answering applies when the answer finishes. Each outcome shows in the next "
            "'Current UI State'. Non-destructive only — this cannot write, delete, "
            "send, or run anything."
        ),
        {"cmd": str, "path": str},
    )
    async def ui_action(args: dict) -> dict:
        cmd = args.get("cmd")
        path = args.get("path")
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
        return {"content": [{"type": "text", "text": build_ui_ack(cmd, path)}]}

    @tool(
        SENSE_ATTENTION_TOOL_NAME,
        (
            "SENSE your unified 'Need You' queue — everything across the whole OS "
            "that structurally needs the user: paused pipeline decisions, blocked "
            "escalations, pending knowledge/governance proposals, circuit-broken "
            "jobs. Use when the user asks to see, review, or handle 'Need You' / "
            "attention items, or when you want to check what is waiting before "
            "acting. Returns the SAME queue the user's Need You overlay shows "
            "(grouped by tier: BLOCKING first, then REVIEW; then by brain). "
            "Optionally pass `brain` = a project name to scope to one brain "
            "(OS-level governance is excluded from a per-brain query). Read-only — "
            "this reads state, it changes nothing; to act on an item, follow its "
            "'to handle' message as a normal chat step."
        ),
        {"brain": str},
    )
    async def sense_attention(args: dict) -> dict:
        # Read-only SENSE: return the live queue as text. Unlike ui_action
        # (which acks and defers to a next-turn SSE effect), this
        # tool's RESULT IS the data — the agent reads it THIS turn and acts.
        brain = args.get("brain") or None
        try:
            text = format_attention_for_agent(brain if isinstance(brain, str) else None)
        except Exception as e:  # noqa: BLE001 — never break the turn on a read
            text = f"(Could not read the Need You queue: {e})"
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name=UI_MCP_SERVER_NAME,
        tools=[ui_action, sense_attention],
    )
