/**
 * uiCommands — the agent UI-action (ACT) dispatch bridge (proprioception Run 2).
 *
 * The agent calls a backend `ui_action` tool; the backend emits a `ui_command`
 * SSE event; useChatStreamingLifecycle consumes it and calls dispatchUiCommand,
 * which re-dispatches to the EXISTING swarm:* window handlers.
 *
 * SECURITY CRUX (Gate-1 BLOCK 1) — the frontend OWNS the mapping:
 * dispatchUiCommand derives the event name + target from THIS table, keyed by the
 * bare `cmd` id. It NEVER trusts a backend-supplied event/target field on the
 * wire. So even a buggy or compromised backend can only pick a cmd from this fixed
 * enum — it can never name an arbitrary `swarm:*` event (e.g. open-terminal-here,
 * inject-chat-input) or flip the target. Fail-closed: an unknown cmd dispatches
 * nothing.
 *
 * Backend counterpart: backend/core/ui_actions.py UI_COMMAND_ALLOWLIST. Run 2
 * scope = non-destructive, window-target nav/display only. Deliberately EXCLUDED:
 * open-file (its resolver allows arbitrary host paths → infoleak), toast /
 * nav-activate (no live listener), show-library (not in ALL_SHOW_EVENTS).
 *
 * DRIFT IS STRUCTURALLY IMPOSSIBLE ON THIS SIDE (the "shared list" fix): the
 * show-* portion of the table is DERIVED from ALL_SHOW_EVENTS — the LeftNav's
 * single source of truth for its overlay events. Add/rename/remove a nav card
 * (edit ALL_SHOW_EVENTS) and this table auto-follows; there is no hand-copied
 * list to fall out of sync. The backend (Python, can't import TS) is bound to
 * the same SSOT by a test (test_ui_actions.py::test_backend_allowlist_is_bound_to_leftnav_ssot).
 * open-canvas + back-to-chat are the ONLY hand-listed entries because they are
 * NOT overlay show-events (open-canvas has no overlay; back-to-chat is the close
 * event) — everything else rides the SSOT.
 */
import { ALL_SHOW_EVENTS, BACK_TO_CHAT_EVENT } from '../components/layout/useExclusiveOverlay';

type EventTarget_ = 'window' | 'document';

interface UiCommandEntry {
  event: string; // the swarm:* CustomEvent name to dispatch
  target: EventTarget_;
}

const SWARM_PREFIX = 'swarm:';

/** The security SSOT on the frontend. Bare cmd id → {event, target}.
 *  show-* entries are DERIVED from ALL_SHOW_EVENTS (strip the 'swarm:' prefix →
 *  bare cmd id, always window-target); the two non-overlay commands are explicit. */
export const UI_COMMAND_TABLE: Record<string, UiCommandEntry> = {
  // Explicit non-overlay commands (NOT in ALL_SHOW_EVENTS):
  'open-canvas': { event: 'swarm:open-canvas', target: 'window' },
  'back-to-chat': { event: BACK_TO_CHAT_EVENT, target: 'window' },
  // Derived from the LeftNav SSOT — one entry per overlay, auto-synced:
  ...Object.fromEntries(
    ALL_SHOW_EVENTS.map((event) => [
      event.slice(SWARM_PREFIX.length),
      { event, target: 'window' as const },
    ]),
  ),
};

/**
 * Dispatch an agent-requested UI command by re-emitting the mapped swarm:* event.
 *
 * @param cmd the bare command id (from the ui_command SSE event's `cmd` field)
 * @returns true if dispatched, false if the cmd was not allowlisted (fail-closed).
 *
 * PAYLOAD-LESS BY DESIGN (Gate-2 LOW): Run 2's allowlisted commands are pure
 * navigation/display — none take a data argument. We deliberately do NOT forward
 * any wire `detail` to the CustomEvent: `detail` would originate only from the
 * SSE wire (i.e. a rogue/compromised backend), and forwarding it to a handler
 * that might one day read `event.detail` is an untrusted-input sink for zero
 * current benefit. Re-introduce a payload ONLY per-cmd, gated in the table, when
 * a specific command genuinely needs one.
 */
export function dispatchUiCommand(cmd: unknown): boolean {
  if (typeof cmd !== 'string' || !cmd) {
    console.warn('[ui_command] rejected non-string cmd:', cmd);
    return false;
  }
  const entry = UI_COMMAND_TABLE[cmd];
  if (!entry) {
    // Fail-closed: unknown / destructive / raw-injection cmd → dispatch nothing.
    console.warn(`[ui_command] rejected non-allowlisted cmd: ${cmd}`);
    return false;
  }
  const targetObj = entry.target === 'document' ? document : window;
  targetObj.dispatchEvent(new CustomEvent(entry.event));
  return true;
}
