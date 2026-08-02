"""Tests for the agent UI-action (ACT) channel — proprioception Run 2.

The agent calls a `ui_action` tool with an enum `cmd`; the orchestrator validates
it against UI_COMMAND_ALLOWLIST and emits a `ui_command` SSE event. The allowlist
is the security boundary — fail-closed:

- An allowlisted cmd → validate() returns its entry {event, target}.
- A non-allowlisted / destructive / raw-injection cmd → validate() returns None
  (never emitted, never dispatched).
- The agent supplies ONLY an enum cmd (no raw swarm:* string, no data path) — the
  event name is derived server-side from the allowlist, never agent-supplied.
- Every allowlisted event targets `window` and is a known non-destructive nav/
  display command (open-canvas, the ALL_SHOW_EVENTS overlays, back-to-chat).
"""
from core.ui_actions import (
    UI_COMMAND_ALLOWLIST,
    validate_ui_command,
    build_ui_command_event,
)


# ── allowlist shape ─────────────────────────────────────────────────────────

def test_allowlist_is_nonempty_and_window_only():
    assert len(UI_COMMAND_ALLOWLIST) >= 10
    # Run 2 scope: every command is a non-destructive nav/display action on window.
    for cmd, entry in UI_COMMAND_ALLOWLIST.items():
        assert entry["target"] == "window", f"{cmd} must target window in Run 2"
        assert entry["event"].startswith("swarm:"), cmd


def test_allowlist_excludes_destructive_and_dropped_commands():
    # open-file dropped (arbitrary host-path infoleak, Gate-1 BLOCK 3).
    assert "open-file" not in UI_COMMAND_ALLOWLIST
    # never any side-effecting / injection-path command
    for banned in (
        "open-terminal-here", "inject-chat-input", "attach-file",
        "file-changed", "toast", "nav-activate", "show-library",
    ):
        assert banned not in UI_COMMAND_ALLOWLIST, f"{banned} must NOT be allowlisted"


def test_allowlist_covers_expected_nav_commands():
    for cmd in (
        "open-canvas", "back-to-chat",
        "show-swarmws", "show-brain-hub", "show-context", "show-pipeline",
        "show-pollinate", "show-history", "show-todo", "show-jobs",
    ):
        assert cmd in UI_COMMAND_ALLOWLIST, f"{cmd} should be allowlisted"


# ── validate() fail-closed ──────────────────────────────────────────────────

def test_validate_allowlisted_returns_entry():
    entry = validate_ui_command("open-canvas")
    assert entry is not None
    assert entry["event"] == "swarm:open-canvas"
    assert entry["target"] == "window"


def test_validate_rejects_unknown():
    assert validate_ui_command("definitely-not-a-command") is None


def test_validate_rejects_destructive():
    assert validate_ui_command("open-terminal-here") is None
    assert validate_ui_command("inject-chat-input") is None
    assert validate_ui_command("open-file") is None


def test_validate_rejects_raw_event_injection():
    # The agent must NOT be able to pass a raw swarm:* string as the cmd.
    assert validate_ui_command("swarm:open-terminal-here") is None
    assert validate_ui_command("open-canvas; rm -rf") is None
    assert validate_ui_command("") is None
    assert validate_ui_command(None) is None  # type: ignore[arg-type]


# ── build_ui_command_event: the SSE payload carries cmd + event + target ─────
# (backend emits event/target for the frontend to CROSS-CHECK against its own
#  table; the frontend derives its dispatch from ITS table keyed by cmd, never
#  trusting these fields blindly — but they're carried for logging/debug parity.)

def test_build_event_for_allowlisted_cmd():
    ev = build_ui_command_event("show-todo")
    assert ev is not None
    assert ev["type"] == "ui_command"
    assert ev["cmd"] == "show-todo"
    assert ev["event"] == "swarm:show-todo"
    assert ev["target"] == "window"


def test_build_event_fail_closed_for_bad_cmd():
    # A non-allowlisted cmd MUST NOT produce an event (fail-closed at the source).
    assert build_ui_command_event("open-terminal-here") is None
    assert build_ui_command_event("open-file") is None
