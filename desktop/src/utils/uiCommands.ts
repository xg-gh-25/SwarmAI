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
 * Keep in sync with backend/core/ui_actions.py UI_COMMAND_ALLOWLIST. Run 2 scope =
 * non-destructive, window-target nav/display only. Deliberately EXCLUDED:
 * open-file (its resolver allows arbitrary host paths → infoleak), toast /
 * nav-activate (no live listener), show-library (not in ALL_SHOW_EVENTS).
 */

type EventTarget_ = 'window' | 'document';

interface UiCommandEntry {
  event: string; // the swarm:* CustomEvent name to dispatch
  target: EventTarget_;
}

/** The security SSOT on the frontend. Bare cmd id → {event, target}. */
export const UI_COMMAND_TABLE: Record<string, UiCommandEntry> = {
  'open-canvas': { event: 'swarm:open-canvas', target: 'window' },
  'back-to-chat': { event: 'swarm:back-to-chat', target: 'window' },
  'show-swarmws': { event: 'swarm:show-swarmws', target: 'window' },
  'show-brain-hub': { event: 'swarm:show-brain-hub', target: 'window' },
  'show-context': { event: 'swarm:show-context', target: 'window' },
  'show-pipeline': { event: 'swarm:show-pipeline', target: 'window' },
  'show-pollinate': { event: 'swarm:show-pollinate', target: 'window' },
  'show-history': { event: 'swarm:show-history', target: 'window' },
  'show-todo': { event: 'swarm:show-todo', target: 'window' },
  'show-jobs': { event: 'swarm:show-jobs', target: 'window' },
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
