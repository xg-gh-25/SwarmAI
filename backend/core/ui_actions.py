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
    "back-to-chat": {"event": "swarm:back-to-chat", "target": "window"},
    "show-swarmws": {"event": "swarm:show-swarmws", "target": "window"},
    "show-brain-hub": {"event": "swarm:show-brain-hub", "target": "window"},
    "show-context": {"event": "swarm:show-context", "target": "window"},
    "show-pipeline": {"event": "swarm:show-pipeline", "target": "window"},
    "show-pollinate": {"event": "swarm:show-pollinate", "target": "window"},
    "show-history": {"event": "swarm:show-history", "target": "window"},
    "show-todo": {"event": "swarm:show-todo", "target": "window"},
    "show-jobs": {"event": "swarm:show-jobs", "target": "window"},
    # New Brain launcher — non-destructive: opens the collect-modal only; "Create"
    # still routes through chat (autoSend:false, human reviews before send). Safe
    # for the agent to open per the Run-2 nav/display charter.
    "show-new-brain": {"event": "swarm:show-new-brain", "target": "window"},
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
    if cmd == "open-canvas-file":
        return (
            "the 'Current UI State' section should show a 'Canvas (output panel): "
            "… open …' line AND the 'Open file:' line should name the file you "
            "opened (if it's absent, the path was outside the workspace or not found "
            "→ the generic file filter dropped it)"
        )
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


# Commands that carry a `path` payload (per-cmd opt-in, mirroring the frontend's
# "re-introduce a payload ONLY per-cmd" rule). ONLY open-canvas-file: its path is
# NOT validated here — it rides the generic workspace-scoped swarm:open-file filter
# (/workspace/file/resolve), which is the single authority that rejects abs/host
# paths + `..`. A pure-nav command never gains a path key even if one is supplied.
_PATH_CARRYING_CMDS = frozenset({"open-canvas-file"})


def build_ui_command_event(cmd: object, path: object = None) -> Optional[dict]:
    """Build the ``ui_command`` SSE payload for *cmd*, or None if not allowlisted.

    Fail-closed at the source: a non-allowlisted cmd yields no event. The payload
    carries the derived event/target for frontend cross-check + logging parity —
    but the frontend re-derives its own dispatch from its own table (never trusts
    these fields blindly).

    ``path`` is PASSED THROUGH verbatim for the path-carrying commands
    (open-canvas-file) and IGNORED for every other (pure-nav) command. No path
    validation happens here by design — the generic swarm:open-file resolver is the
    workspace-scoped filter (run_c0550cc2).
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
        # SECURITY (Gate-2 CRITICAL, run_c0550cc2): the AGENT efferent channel is
        # workspace-RELATIVE only. The downstream /workspace/file/resolve HAPPILY
        # resolves an ABSOLUTE host path (/etc/passwd, ~/.aws/credentials) — that
        # branch exists for USER-CLICK opens of source-repo files, but the agent must
        # NOT reach it (that is exactly the Gate-1 BLOCK 3 open-file infoleak, by
        # another name). So we reject an absolute or `..`-traversal path HERE, at the
        # agent's entry point, and fail-closed (drop the whole event → no open). This
        # does NOT touch the generic resolver (user clicks keep their abs-path opens).
        _norm = os.path.normpath(path)
        if os.path.isabs(_norm) or _norm.startswith(".."):
            logger.warning(
                "ui_action: rejected open-canvas-file with non-workspace-relative "
                "path %r (agent channel is workspace-relative only)", path
            )
            return None
        ev["path"] = path
    return ev


# ── Finish-time PR-review batch: surface a run's coding files (run_b8ea6d5c) ──
#
# CHANNEL A. Coding/source files are suppressed mid-run (needs_human_review →
# kind=source → dropped at useReferencedFiles.ts). At pipeline COMPLETE the agent
# calls the `surface_run_outputs` tool with the run_id; the streaming orchestrator
# observes that ToolUseBlock BY NAME (exactly like ui_action) and yields N
# `file_changed` SSE events built HERE — one per file this run committed — carrying
# kind="source-final", the ONE kind the rail accepts for a finish batch (mid-run
# `source` stays dropped). This reuses the rail-is-a-projection-of-the-tool-stream
# invariant, so persistence + tab-scope are automatically correct; run-commit (a
# CLI subprocess) structurally cannot push rows, which is why this rides the live
# COMPLETE turn instead. The tool reads run.json.commits (populated by run-commit),
# NOT agent-supplied paths — so it cannot fabricate rows for arbitrary files.

# Files that are surfaced immediately per-change (knowledge/report) must NOT be
# re-emitted in the finish batch (they already have a row). The batch is source only.
def build_surface_events(run_id: object, workspace_root: object = None) -> list[dict]:
    """Build the finish-time batch of file_changed SSE events for a run's committed
    source files. Returns [] on any problem (fail-safe — never crashes the turn).

    Reads ``run.json.commits[].files`` (repo-relative paths the run actually
    committed) for ``run_id``, resolves each to a workspace-relative display path,
    and emits one ``file_changed`` event per file with ``kind="source-final"`` +
    ``operation="written"``. The frontend rail accepts source-final and persists it.
    """
    if not isinstance(run_id, str) or not run_id:
        return []
    try:
        import json
        from pathlib import Path
        if workspace_root is not None:
            ws = Path(str(workspace_root))
        else:
            from core.project_registry import get_swarmws
            ws = Path(get_swarmws())
        ws = ws.resolve()
        # Locate the run dir across projects (run ids are unique).
        run_json = None
        for proj in (ws / "Projects").glob("*/.artifacts/runs/" + run_id + "/run.json"):
            run_json = proj
            break
        if run_json is None or not run_json.exists():
            return []
        data = json.loads(run_json.read_text())
        events: list[dict] = []
        seen: set[str] = set()
        for commit in data.get("commits", []) or []:
            repo = commit.get("repo", "")
            for f in commit.get("files", []) or []:
                if not f or f in seen:
                    continue
                seen.add(f)
                # Absolute physical path (repo root + repo-relative file). Display
                # path stays repo-relative (what the user recognizes in a PR).
                abs_path = str(Path(repo) / f) if repo else f
                events.append({
                    "type": "file_changed",
                    "path": f,
                    "absolutePath": abs_path,
                    "operation": "written",
                    "relevance": "deliverable",
                    "kind": "source-final",
                })
        return events
    except Exception as e:  # noqa: BLE001 — hot-path fail-safe
        logger.warning("build_surface_events failed for run %r: %s", run_id, e)
        return []


# ── The SDK-MCP tool the agent calls ─────────────────────────────────────────

# Tool name as the agent sees it (SDK-MCP convention: mcp__<server>__<tool>).
UI_MCP_SERVER_NAME = "swarm_ui"
UI_ACTION_TOOL_NAME = "ui_action"
UI_ACTION_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{UI_ACTION_TOOL_NAME}"
SURFACE_OUTPUTS_TOOL_NAME = "surface_run_outputs"
SURFACE_OUTPUTS_FULL_TOOL_NAME = f"mcp__{UI_MCP_SERVER_NAME}__{SURFACE_OUTPUTS_TOOL_NAME}"


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
            "Use when the user asks you to show/open something in the app. cmd MUST be "
            f"one of: {_cmd_list}. For 'open-canvas-file', also pass `path` = the "
            "workspace-relative file path to show in Canvas (e.g. "
            "'Knowledge/Designs/foo.md'); it is resolved by the workspace file "
            "filter, so only files inside the workspace open. Non-destructive only — "
            "this cannot write, delete, send, or run anything."
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
        SURFACE_OUTPUTS_TOOL_NAME,
        (
            "At pipeline COMPLETE, surface THIS run's committed coding files as a "
            "batch of review rows in the Canvas OUTPUTS list — a local-PR review "
            "experience. Call this ONCE at the end of a pipeline run that committed "
            "source, after run-commit has recorded the commits. Pass `run_id` = the "
            "pipeline run id (e.g. 'run_b8ea6d5c'). The rows are read from the run's "
            "recorded commits (not from you) — non-destructive, display only."
        ),
        {"run_id": str},
    )
    async def surface_run_outputs(args: dict) -> dict:
        run_id = args.get("run_id")
        # The orchestrator observes this call and emits the batch of file_changed
        # events (build_surface_events). The tool body just acks + points at the
        # SENSE readback, mirroring ui_action (it cannot see the frontend).
        n = len(build_surface_events(run_id))
        if n == 0:
            return {"content": [{"type": "text", "text": (
                f"No committed source files found for run {run_id!r} — nothing to "
                "surface. (Did run-commit record commits for this run yet?)"
            )}]}
        return {"content": [{"type": "text", "text": (
            f"Surfacing {n} committed file(s) from run {run_id!r} to the Canvas "
            "OUTPUTS list. This is NOT synchronous — the frontend adds the rows "
            "after this turn. On your NEXT turn, verify the 'Current UI State' Canvas "
            "outputs count reflects them; click a row to review that file's changes."
        )}]}

    return create_sdk_mcp_server(
        name=UI_MCP_SERVER_NAME, tools=[ui_action, surface_run_outputs]
    )
