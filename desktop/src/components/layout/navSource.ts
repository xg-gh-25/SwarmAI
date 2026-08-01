/**
 * navSource — the shared "where did this panel spit out from" origin.
 *
 * A11 / run_2e6d6029: when a nav card is clicked it publishes its on-screen
 * position here (via A10Card, the single injection point for BOTH the
 * window-event overlays AND the activeModal modals), and the fullscreen Modal
 * reads it on open to (1) anchor its spit-out transform-origin and (2) draw a
 * spout triangle pointing back at the source card.
 *
 * Why a module-level ref (NOT LayoutContext / React state):
 *  - the value is write-once-on-click, read-once-on-open — it must NEVER trigger
 *    a render by itself (Gate-1 #1: adding it to useLayout() would re-render the
 *    whole shell on every nav click).
 *
 * Staleness contract (Gate-2 #3): the fullscreen Modal CONSUMES the source on
 * open (reads then `clearNavSource()`), so a source set by one click is used by
 * exactly one open and then gone. The invariant this depends on: a card that
 * does NOT open a fullscreen Modal (Memory / Community open a file panel / toast)
 * MUST `clearNavSource()` in its handler — otherwise its stale rect would be
 * picked up by the next unrelated fullscreen open (e.g. credential-banner →
 * Settings) and mis-point the spout. Non-card open paths never set it, so after
 * a consume they correctly read null → no spout.
 *
 * Forward-compatible: a future central `swarm:nav-activate` dispatcher can call
 * the same `setNavSource(rect)` — this is the shared geometry primitive, not a
 * band-aid that entrenches the two-mechanism split (Gate-1 #7).
 */

export interface NavSource {
  /** viewport-space vertical center of the clicked card */
  centerY: number;
  /** the source card's region tint (hex) — the panel border / ring / spout /
   *  header underline align to this so the panel reads as "spat out from THIS
   *  region" (cognition green / Work teal / System grey / Settings-Eval blue).
   *  undefined = fall back to the neutral accent. */
  tint?: string;
}

let current: NavSource | null = null;

/** Called by A10Card BEFORE delegating to the card's real onClick.
 *  `tint` = the card's region color (optional; drives the panel accent). */
export function setNavSource(rect: DOMRect, tint?: string): void {
  current = { centerY: rect.top + rect.height / 2, tint };
}

/** Read the current source (null if none / cleared). Does not consume. */
export function readNavSource(): NavSource | null {
  return current;
}

/** Clear the source (non-fullscreen card handlers, or after consume). */
export function clearNavSource(): void {
  current = null;
}
