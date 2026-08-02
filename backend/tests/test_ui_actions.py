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
    _expected_post_state,
    build_ui_ack,
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


# ── expected-post-state ack (proprioception feedback arc) ────────────────────
# The ui_action SUCCESS ack must tell the agent WHAT to verify on its next turn,
# referencing the SENSE-RENDERED signal (the "## Current UI State" prose lines
# from prompt_builder._render_ui_context_section), NOT the raw schema fields the
# agent never sees. It must NOT claim synchronous confirmation.

def test_expected_post_state_canvas():
    # open-canvas → point at the rendered "Canvas (output panel): ... open" line.
    exp = _expected_post_state("open-canvas")
    low = exp.lower()
    assert "canvas" in low and "open" in low
    # Must reference the SENSE surface the agent actually reads, not raw fields.
    assert "canvas.open" not in exp, "must not name the raw schema field"


def test_expected_post_state_show_overlay():
    # show-* → point at the rendered "Open overlay:" line; echo the cmd, do NOT
    # hardcode the _OVERLAY_LABELS value (drift + circular import).
    exp = _expected_post_state("show-todo")
    assert "overlay" in exp.lower()
    assert "show-todo" in exp, "should echo the dispatched cmd so it's verifiable"
    # Drift guard: must NOT duplicate the human label from _OVERLAY_LABELS.
    assert "ToDo" not in exp, "must not hardcode the overlay label (drift/dup)"


def test_expected_post_state_back_to_chat():
    # back-to-chat → the "Open overlay:" line should be ABSENT (overlays closed).
    exp = _expected_post_state("back-to-chat").lower()
    assert "overlay" in exp and ("absent" in exp or "no " in exp or "closed" in exp)


def test_success_ack_says_verify_not_confirmed():
    # Honest: the ack is a pointer to the PASSIVE next-turn readback, never proof.
    for cmd in ("open-canvas", "show-jobs", "back-to-chat"):
        ack = build_ui_ack(cmd)
        low = ack.lower()
        assert "next turn" in low, f"{cmd}: ack must point at next-turn SENSE readback"
        assert "verify" in low, f"{cmd}: ack must ask the agent to verify"
        # Must NOT over-promise a synchronous confirmation.
        assert "confirmed" not in low, f"{cmd}: ack must not claim confirmation"
        assert cmd in ack, f"{cmd}: ack should name the dispatched cmd"


def test_build_ui_ack_none_for_bad_cmd():
    # Fail-closed: a non-allowlisted cmd has no success ack (the tool returns the
    # rejection instead — verified via the tool body path unchanged).
    assert build_ui_ack("open-terminal-here") is None
    assert build_ui_ack("open-file") is None


def test_ack_signals_match_real_sense_render():
    """CRUX / anti-theater: the phrases the ack tells the agent to look for MUST
    be exactly what prompt_builder._render_ui_context_section actually injects.
    If the render renames those lines, the ack silently becomes unverifiable
    theater — this test binds the two so that drift FAILS the build.

    (This is the one-way direction the modules allow: prompt_builder imports
    ui_actions, so ui_actions cannot import it — but the TEST can import both and
    assert the contract holds.)
    """
    from core.prompt_builder import _render_ui_context_section

    canvas_render = _render_ui_context_section(
        {"canvas": {"open": True, "output_count": 2}}
    )
    overlay_render = _render_ui_context_section({"active_overlay": "swarm:show-todo"})

    # open-canvas ack points at these — they must exist in the real render.
    assert "Current UI State" in canvas_render
    assert "Canvas (output panel):" in canvas_render
    # show-* ack points at the "Open overlay:" line.
    assert "Open overlay:" in overlay_render

    # And prove the ack text itself only references phrases present in the render.
    for phrase in ("Current UI State", "Canvas (output panel):"):
        assert phrase in build_ui_ack("open-canvas")
    assert "Open overlay:" in build_ui_ack("show-todo")
    assert "Open overlay:" in build_ui_ack("back-to-chat")


# ── binding to the LeftNav SSOT (ALL_SHOW_EVENTS) ────────────────────────────
# The REAL source of truth for the swarm:show-* overlay events is
# desktop/src/components/layout/useExclusiveOverlay.ts::ALL_SHOW_EVENTS. The
# frontend UI_COMMAND_TABLE now DERIVES its show-* entries from that SSOT (see
# uiCommands.ts), so frontend drift is structurally impossible. Python cannot
# import a TS constant, so the backend UI_COMMAND_ALLOWLIST stays a hand-written
# literal — and THIS test binds it to the SSOT: it parses ALL_SHOW_EVENTS and
# asserts the backend allowlist == {every show-* event} + {open-canvas,
# back-to-chat}. So a LeftNav card add/rename/remove that the backend didn't
# follow FAILS THE BUILD instead of silently making an agent command a no-op.


def _parse_all_show_events() -> list[str]:
    """Parse the ALL_SHOW_EVENTS string-literal array from useExclusiveOverlay.ts.

    This is the LeftNav SSOT. Kept as a source-parse (not a duplicated Python
    list) on purpose: duplicating it here would just move the drift, not kill it.
    """
    import re
    from pathlib import Path

    ts_path = (
        Path(__file__).resolve().parents[2]
        / "desktop" / "src" / "components" / "layout" / "useExclusiveOverlay.ts"
    )
    text = ts_path.read_text()
    # Anchor on the DECLARATION, not a bare name match — the name also appears in
    # the file's docstring (line 13), and splitting on the first mention would grab
    # the wrong bracket span if a '[' ever entered that prose. Anchor on the
    # `export const ALL_SHOW_EVENTS = [` declaration so only the array body is read.
    m = re.search(r"export\s+const\s+ALL_SHOW_EVENTS\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert m, "could not locate the `export const ALL_SHOW_EVENTS = [...]` declaration"
    events = re.findall(r"'([^']+)'", m.group(1))
    return events


def test_backend_allowlist_is_bound_to_leftnav_ssot():
    """The backend allowlist must EXACTLY equal the LeftNav SSOT's show-* events
    plus the two non-overlay commands (open-canvas, back-to-chat). This is the
    drift guard the frontend can't be (Python can't import TS): a card added to
    ALL_SHOW_EVENTS but not to UI_COMMAND_ALLOWLIST — or one removed/renamed there
    but stale here — FAILS this test.
    """
    show_events = _parse_all_show_events()
    assert len(show_events) >= 8, (
        f"parsed too few ALL_SHOW_EVENTS ({len(show_events)}) — parser drift or "
        "the SSOT shrank unexpectedly"
    )
    for ev in show_events:
        assert ev.startswith("swarm:show-"), (
            f"ALL_SHOW_EVENTS entry {ev!r} is not a swarm:show-* event — the "
            "derivation assumption (strip 'swarm:' → cmd id) no longer holds"
        )

    # Expected backend allowlist = every SSOT show-* event, keyed by its bare cmd
    # id (strip the 'swarm:' prefix), all window-target, PLUS the two commands that
    # are deliberately NOT in ALL_SHOW_EVENTS.
    expected = {
        ev[len("swarm:"):]: {"event": ev, "target": "window"} for ev in show_events
    }
    expected["open-canvas"] = {"event": "swarm:open-canvas", "target": "window"}
    expected["back-to-chat"] = {"event": "swarm:back-to-chat", "target": "window"}

    assert UI_COMMAND_ALLOWLIST == expected, (
        "backend UI_COMMAND_ALLOWLIST drifted from the LeftNav SSOT "
        "(ALL_SHOW_EVENTS) — a nav card was added/renamed/removed without updating "
        "the agent's allowlist.\n"
        f"  allowlist-only (stale/no-op): {set(UI_COMMAND_ALLOWLIST) - set(expected)}\n"
        f"  SSOT-only (unreachable by agent): {set(expected) - set(UI_COMMAND_ALLOWLIST)}"
    )


def test_frontend_table_derives_show_star_from_ssot():
    """The frontend UI_COMMAND_TABLE must DERIVE its show-* entries from
    ALL_SHOW_EVENTS (not hand-list them) — that is the structural drift-kill.

    Verified by source shape: uiCommands.ts must (a) import ALL_SHOW_EVENTS from
    useExclusiveOverlay, and (b) build the show-* rows by mapping over it — NOT
    carry per-overlay `'show-xxx': { event: 'swarm:show-xxx', ... }` literals. If
    someone re-hand-lists the overlays as literals, this test fails (the drift
    door the whole change closes would be re-opened). open-canvas + back-to-chat
    (the two non-overlay commands) MAY remain literals — they are excluded.
    """
    import re
    from pathlib import Path

    ts_path = (
        Path(__file__).resolve().parents[2]
        / "desktop" / "src" / "utils" / "uiCommands.ts"
    )
    text = ts_path.read_text()

    assert "ALL_SHOW_EVENTS" in text and "from '../components/layout/useExclusiveOverlay'" in text, (
        "uiCommands.ts must import ALL_SHOW_EVENTS from useExclusiveOverlay so the "
        "show-* table is derived from the LeftNav SSOT (not hand-copied)"
    )
    assert "ALL_SHOW_EVENTS.map(" in text, (
        "uiCommands.ts must DERIVE the show-* table by mapping over ALL_SHOW_EVENTS "
        "(Object.fromEntries(ALL_SHOW_EVENTS.map(...))) — not hand-list the overlays"
    )
    # Guard against re-introducing hand-listed overlay literals (the drift source).
    hand_listed = re.findall(
        r"'(show-[A-Za-z0-9-]+)'\s*:\s*\{\s*event:\s*'swarm:show-", text
    )
    assert not hand_listed, (
        "uiCommands.ts re-introduced hand-listed show-* literals "
        f"{hand_listed} — these must be DERIVED from ALL_SHOW_EVENTS, or they "
        "silently drift from the LeftNav SSOT (the exact bug this change removed)"
    )
