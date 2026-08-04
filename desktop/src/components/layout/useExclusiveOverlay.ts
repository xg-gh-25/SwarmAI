/**
 * Overlay show-event vocabulary + the cross-surface close broadcast.
 *
 * HISTORY: this module used to host `useExclusiveOverlay` — a per-overlay open/close
 * hook + an `activeOverlayEvent` module singleton — that drove the legacy window-event
 * overlay bus. That hook + singleton were DELETED (2026-08-04, M5): every fullscreen
 * surface now renders through the OverlayHost subsystem (contexts/OverlayContext +
 * components/layout/OverlayHost), whose single `activeOverlay` slot is the sole state.
 *
 * What SURVIVES here, and why:
 *   • ALL_SHOW_EVENTS  — the agent's ui_action ACT vocabulary. `ui_action` (backend
 *     UI_COMMAND_ALLOWLIST, derived from this list) dispatches `swarm:show-<id>` to
 *     open a surface; OverlayContext's afferent bridge maps that → openOverlay(<id>).
 *     The NAMES are the command contract (SELF.md proprioception) — they outlive the
 *     old hook. uiCommands.ts derives the allowlist from this SSOT.
 *   • BACK_TO_CHAT_EVENT / closeOpenOverlays — the "close any open fullscreen surface"
 *     broadcast. OverlayContext listens for it (closes the host overlay); the Chat
 *     hero + deep-link + file-panel paths dispatch it to return to chat.
 */

export const BACK_TO_CHAT_EVENT = 'swarm:back-to-chat';

/** Every window event that opens a fullscreen surface — the SSOT the agent ui_action
 *  allowlist (uiCommands.ts) is derived from, and the set OverlayContext maps to
 *  openOverlay(id) (strip the `swarm:show-` prefix → registry id). A new agent-openable
 *  surface adds its event here + registers the matching id in overlaySurfaces. */
export const ALL_SHOW_EVENTS = [
  'swarm:show-swarmws',
  'swarm:show-brain-hub',
  'swarm:show-context',
  'swarm:show-pipeline',
  'swarm:show-pollinate',
  'swarm:show-history',
  'swarm:show-todo',
  'swarm:show-jobs',
  'swarm:show-new-brain',
] as const;

/**
 * Close every open fullscreen surface — dispatch the back-to-chat broadcast.
 * OverlayHost (via OverlayContext) closes the active `activeOverlay` on this event.
 * Call before opening a NON-overlay surface (a small modal, a file panel) or the
 * settings deep-link, so at most one fullscreen surface is ever open.
 *
 * dispatchEvent is synchronous: every listener runs to completion here before the
 * caller's next line.
 */
export function closeOpenOverlays(): void {
  window.dispatchEvent(new CustomEvent(BACK_TO_CHAT_EVENT));
}
