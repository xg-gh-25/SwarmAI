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
  // open-canvas-file (run_c0550cc2): open a CURRENT-workspace file in Canvas. The
  // ONLY path-carrying command — it rides the EXISTING document-target
  // swarm:open-file, whose /workspace/file/resolve is the generic workspace-scoped
  // filter (rejects abs/host paths + `..`). document-target per useCanvasHost's
  // EVENT-TARGET CONTRACT (all open-file dispatchers listen on document).
  'open-canvas-file': { event: 'swarm:open-file', target: 'document' },
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
 * PAYLOAD POLICY (Gate-2 LOW): pure nav/display commands carry NO detail — the
 * `detail` would originate only from the SSE wire (a rogue/compromised backend),
 * and forwarding it to a handler that reads `event.detail` is an untrusted-input
 * sink for zero benefit. A payload is re-introduced ONLY per-cmd (PATH_CARRYING_CMDS
 * below): open-canvas-file forwards `{ path }` to swarm:open-file — a DATA path, not
 * an event/target (the routing is STILL derived from UI_COMMAND_TABLE, never the
 * wire), and the path is filtered downstream by the workspace-scoped
 * /workspace/file/resolve. The security crux (backend can't pick the event/target)
 * is unchanged.
 */
const PATH_CARRYING_CMDS = new Set(['open-canvas-file']);

// TAB_STAMPED_CMDS (run_10c51cac): commands that carry NO path but MUST land on the
// stream's ORIGIN tab, not whatever tab is active when the mid-stream event fires.
// These forward ONLY { tabId: originTabId } (caller-supplied, never wire-derived) so
// useCanvasHost patches the initiating tab. Distinct from PATH_CARRYING_CMDS because
// open-canvas has no path → the path-required gate would wrongly reject it. Without
// this, an agent open-canvas from a background tab opened the ACTIVE tab's Canvas
// (the sibling of the open-canvas-file bleed). Bare user-click dispatch (no
// originTabId) falls back to the active tab in useCanvasHost — unchanged.
const TAB_STAMPED_CMDS = new Set(['open-canvas']);

// originTabId (run_48a29fc2): the FRONTEND-captured origin tab of the stream that
// emitted this ui_command (the caller passes _stampTab / capturedTabId — NEVER a
// value from the SSE wire). It rides swarm:open-file's detail.tabId so
// useCanvasHost lands the file on the tab that INITIATED the open, not whatever
// tab is active when the mid-stream event fires (the cross-tab bleed). Because it
// is caller-supplied (not wire-derived), it does NOT widen the untrusted-input
// surface the PAYLOAD POLICY above guards — the routing is still table-derived.
export function dispatchUiCommand(cmd: unknown, path?: unknown, originTabId?: string): boolean {
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
  if (PATH_CARRYING_CMDS.has(cmd)) {
    // A path-carrying command NEEDS a non-empty string path to be meaningful.
    if (typeof path !== 'string' || !path) {
      console.warn(`[ui_command] '${cmd}' needs a path — dispatching nothing`);
      return false;
    }
    // SECURITY (Gate-2 CRITICAL, run_c0550cc2) — defense in depth: the agent channel
    // is workspace-RELATIVE only. The `path` arrives from the SSE wire (untrusted per
    // this module's crux), and the downstream /workspace/file/resolve WILL resolve an
    // absolute host path (/etc/passwd, ~/.aws/credentials). The backend
    // build_ui_command_event already drops abs/`..` paths; we re-reject here so a
    // crafted wire event can't reach open-file with a host path either.
    if (path.startsWith('/') || path.startsWith('~') || path.includes('..')) {
      console.warn(`[ui_command] '${cmd}' rejected non-workspace-relative path`);
      return false;
    }
    // originTabId (a valid non-empty string) rides as detail.tabId so the file lands
    // on the initiating tab. Omitted when absent → handleOpenFile falls back to the
    // active tab (correct for the synchronous user-click path, which never stamps).
    const detail = (typeof originTabId === 'string' && originTabId)
      ? { path, tabId: originTabId }
      : { path };
    targetObj.dispatchEvent(new CustomEvent(entry.event, { detail }));
    return true;
  }
  if (TAB_STAMPED_CMDS.has(cmd)) {
    // Carries ONLY the caller-supplied origin tab (never a wire value) so the
    // command lands on the INITIATING tab. Omitted when absent → useCanvasHost falls
    // back to the active tab (correct for the synchronous user-click path). No path,
    // so no workspace-relative filtering applies.
    const detail = (typeof originTabId === 'string' && originTabId) ? { tabId: originTabId } : undefined;
    targetObj.dispatchEvent(new CustomEvent(entry.event, detail ? { detail } : undefined));
    return true;
  }
  // Pure-nav command: payload-less (a supplied path is ignored by design).
  targetObj.dispatchEvent(new CustomEvent(entry.event));
  return true;
}
