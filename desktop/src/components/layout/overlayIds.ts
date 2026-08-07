/**
 * overlayIds — the SINGLE SOURCE OF TRUTH for the set of fullscreen-overlay ids
 * (OverlayHost subsystem, run_06c49540). A zero-dependency leaf module so both the
 * state authority (contexts/OverlayContext) and the registry (overlayRegistry) can
 * import the `OverlayId` type from ONE place without a cycle.
 *
 * WHY THIS IS THE SSOT (not a 4th hand-synced list):
 *   • `OVERLAY_IDS` (this tuple) → the compile-time `OverlayId` union.
 *   • `overlayRegistry.registerOverlay(spec)` types `spec.id: OverlayId`, so a
 *     surface registered with an id NOT in this tuple is a COMPILE ERROR — the
 *     registry is bound to this tuple by the type system, not by convention.
 *   • `overlayIds.test.ts` asserts set-equality BOTH directions
 *     (registeredOverlayIds() === OVERLAY_IDS) so a tuple entry with no registration
 *     (or vice-versa) fails a test. Between the type binding and that test, this
 *     tuple cannot silently drift from the actual registered surfaces.
 *
 * Relationship to ALL_SHOW_EVENTS (useExclusiveOverlay.ts): that list is the
 * agent-openable SUBSET (library/settings/eval/hive are deliberately nav-card/
 * deep-link only, a security boundary, banned from the agent ui_action allowlist —
 * hive because it controls AWS credentials + live cloud infra). overlayIds.test.ts
 * asserts the subset is STRICTLY smaller than OVERLAY_IDS and that every
 * ALL_SHOW_EVENTS suffix ∈ OVERLAY_IDS (the events⊆ids seam the union alone cannot
 * cover, since ALL_SHOW_EVENTS is a separate literal). (Counts are asserted by the
 * test, not stated here — a hardcoded number in prose only goes stale.)
 */

/** Every registered fullscreen-surface id. THE source of the OverlayId union +
 *  the value `overlayRegistry` type-binds each `spec.id` against. Order is
 *  irrelevant (set semantics); keep it grouped by region for readability. */
export const OVERLAY_IDS = [
  // BRAIN region
  'brain-hub',
  'swarmws',
  'context',
  'library',
  'new-brain',
  // WORK region
  'history',
  'todo',
  'jobs',
  'pipeline',
  'pollinate',
  'capabilities',
  'needs-you',
  // SYSTEM region
  'hive',
  'settings',
  'eval',
] as const;

/** The id of a registered fullscreen surface — a closed union, so a typo
 *  (`openOverlay('histroy')`) is a COMPILE ERROR, not a silent null-render. */
export type OverlayId = (typeof OVERLAY_IDS)[number];

/** Runtime membership test / type guard for `OverlayId`. Use at any boundary where
 *  an id arrives as a raw `string` (e.g. a persisted value, a URL param) and must
 *  be narrowed before use. NOTE: the `swarm:show-<id>` event path does NOT need
 *  this — its listeners are bound only to ALL_SHOW_EVENTS, so its sliced id is
 *  always a registered id (guarding it would be dead code for an impossible state,
 *  PIT77). */
export function isOverlayId(x: string): x is OverlayId {
  return (OVERLAY_IDS as readonly string[]).includes(x);
}
