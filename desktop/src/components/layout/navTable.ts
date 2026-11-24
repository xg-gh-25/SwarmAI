/**
 * navTable — the single definition of every Left Nav item and the overlay it opens
 * (design docs/design/left-nav-overlay-single-source.md, S1).
 *
 * One row per OverlayId: the zone it sits in on the nav, the nav label, the overlay
 * header (title = the nav label, XG D4; subtitle on its own), the mode badge (each
 * surface's own, XG D5) and the width tier. Both the
 * nav (ThreeColumnLayout) and the OverlayHost read it; neither writes its own copy.
 *
 * The nav's grouping, hierarchy, order and card styles are NOT described here — they
 * stay hand-written in ThreeColumnLayout (design §3); only the per-item facts move.
 *
 * Key exports:
 * - `NAV`              Record<OverlayId, NavEntry> — every id must have a row (compile-time)
 * - `ZONE_TINT`        the zone colours, defined once
 * - `navTint(id)`      the overlay accent = the item's zone colour
 * - `navEntry(id)`     lookup tolerant of a non-nav id (undefined)

 * Which ids the AGENT may open is NOT here: it stays the explicit ALL_SHOW_EVENTS
 * literal (useExclusiveOverlay.ts), the security allowlist the backend's ui_action
 * mirrors — a flag in a display table is too easy to flip by accident.
 */
import type { OverlayId } from './overlayIds';

export type NavZone = 'chat' | 'cognitive' | 'work' | 'system';

/** Zone colours, from the theme (index.css --color-zone-*, light and dark).
 *  `chat` uses the theme colour (XG D3: no new zone colour). */
export const ZONE_TINT: Record<NavZone, string> = {
  chat: 'var(--color-primary)',
  cognitive: 'var(--color-zone-cognitive)',  // mint — the cognition zone
  work: 'var(--color-zone-work)',            // teal-blue — not green (no clash with cognition)
  system: 'var(--color-zone-system)',        // slate — low-attention OS mechanics
};

export interface NavEntry {
  zone: NavZone;
  /** i18n key of the label on the nav item; also the overlay header title (XG D4).
   *  Readers render it with t(navLabelKey). */
  navLabelKey: string;
  /** i18n key of a second line under the overlay title. */
  subtitleKey?: string;
  /** Mono badge in the overlay header — each surface's own (XG D5). */
  mode: string;
  /** Overlay width tier (OverlayHost OVERLAY_WIDTH). */
  width: 's' | 'm' | 'l' | 'xl';
  /** The surface this one is opened from: the header shows it as a breadcrumb before
   *  the title, and clicking it opens that surface (New Brain → Brain Hub). */
  parent?: OverlayId;
  /** The title is a button back to the surface's own home: the host fires the
   *  surface's `swarm:show-<id>` event with no target, which the surface reads as
   *  "go home" (Brain Hub → its gallery). Only for ids in ALL_SHOW_EVENTS. */
  titleGoesHome?: boolean;
}

export const NAV: Record<OverlayId, NavEntry> = {
  // Chat zone (rows under the Chat hero + the Need You slot)
  history: { zone: 'chat', navLabelKey: 'ui.navTable.history', mode: 'HISTORY', width: 'xl' },
  artifacts: { zone: 'chat', navLabelKey: 'ui.navTable.artifacts', mode: 'ARTIFACTS', width: 'xl' },
  'needs-you': { zone: 'chat', navLabelKey: 'ui.navTable.needYou', mode: 'ATTENTION', width: 'xl' },
  // Cognition zone
  context: { zone: 'cognitive', navLabelKey: 'ui.navTable.cm', subtitleKey: 'ui.navTable.subGlobalBrain', mode: 'BRAIN', width: 'xl' },
  library: { zone: 'cognitive', navLabelKey: 'ui.navTable.library', subtitleKey: 'ui.navTable.subBookshelf', mode: 'LIBRARY', width: 'xl' },
  'brain-hub': { zone: 'cognitive', navLabelKey: 'ui.navTable.brainHub', subtitleKey: 'ui.navTable.subBrainHub', mode: 'BRAIN', width: 'xl', titleGoesHome: true },
  'new-brain': { zone: 'cognitive', navLabelKey: 'ui.navTable.newBrain', subtitleKey: 'ui.navTable.subNewBrain', mode: 'BRAIN', width: 'xl', parent: 'brain-hub' },
  // Work zone (Inbox first: what comes in, then what to do)
  inbox: { zone: 'work', navLabelKey: 'ui.navTable.inbox', mode: 'INBOX', width: 'xl' },
  todo: { zone: 'work', navLabelKey: 'ui.navTable.todo', mode: 'TODO', width: 'xl' },
  // width 'm': a file tree fills ~45% of an 'xl' panel and leaves a void
  swarmws: { zone: 'work', navLabelKey: 'ui.navTable.workspace', subtitleKey: 'ui.navTable.subWorkspace', mode: 'WORKSPACE', width: 'm' },
  pipeline: { zone: 'work', navLabelKey: 'ui.navTable.pipeline', mode: 'PIPELINE', width: 'xl' },
  pollinate: { zone: 'work', navLabelKey: 'ui.navTable.pollinate', mode: 'POLLINATE', width: 'xl' },
  // System zone
  jobs: { zone: 'system', navLabelKey: 'ui.navTable.jobsRuns', mode: 'JOBS', width: 'xl' },
  capabilities: { zone: 'system', navLabelKey: 'ui.navTable.capabilities', subtitleKey: 'ui.navTable.subCapabilities', mode: 'CAPABILITIES', width: 'xl' },
  usage: { zone: 'system', navLabelKey: 'ui.navTable.usage', subtitleKey: 'ui.navTable.subUsage', mode: 'USAGE', width: 'xl' },
  eval: { zone: 'system', navLabelKey: 'ui.navTable.osEval', mode: 'EVAL', width: 'xl' },
  settings: { zone: 'system', navLabelKey: 'ui.navTable.settings', mode: 'SETTINGS', width: 'xl' },
};

/** The row for `id`, or undefined for an id that is not a nav item. */
export function navEntry(id: string | null | undefined): NavEntry | undefined {
  return id != null && Object.prototype.hasOwnProperty.call(NAV, id) ? NAV[id as OverlayId] : undefined;
}

/** The overlay accent: the item's zone colour. */
export function navTint(id: OverlayId): string {
  return ZONE_TINT[NAV[id].zone];
}
