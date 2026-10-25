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
  /** The label on the nav item; also the overlay header title (XG D4). */
  navLabel: string;
  /** A second line under the overlay title. */
  subtitle?: string;
  /** Mono badge in the overlay header — each surface's own (XG D5). */
  mode: string;
  /** Overlay width tier (OverlayHost OVERLAY_WIDTH). */
  width: 's' | 'm' | 'l' | 'xl';
}

export const NAV: Record<OverlayId, NavEntry> = {
  // Chat zone (rows under the Chat hero + the Need You slot)
  history: { zone: 'chat', navLabel: 'History', mode: 'HISTORY', width: 'xl' },
  artifacts: { zone: 'chat', navLabel: 'Artifacts', mode: 'ARTIFACTS', width: 'xl' },
  'needs-you': { zone: 'chat', navLabel: 'Need You', mode: 'ATTENTION', width: 'm' },
  // Cognition zone
  context: { zone: 'cognitive', navLabel: 'C&M', subtitle: 'Global Brain', mode: 'BRAIN', width: 'xl' },
  library: { zone: 'cognitive', navLabel: 'Library', subtitle: 'Bookshelf', mode: 'LIBRARY', width: 'xl' },
  'brain-hub': { zone: 'cognitive', navLabel: 'Brain Hub', subtitle: 'what each project knows, delivers and learns', mode: 'BRAIN', width: 'xl' },
  'new-brain': { zone: 'cognitive', navLabel: 'New Brain', subtitle: 'Grow a new brain', mode: 'BRAIN', width: 'xl' },
  // Work zone
  todo: { zone: 'work', navLabel: 'ToDo', mode: 'TODO', width: 'xl' },
  // width 'm': a file tree fills ~45% of an 'xl' panel and leaves a void
  swarmws: { zone: 'work', navLabel: 'Workspace', subtitle: 'SwarmWS workspace explorer', mode: 'WORKSPACE', width: 'm' },
  pipeline: { zone: 'work', navLabel: 'Pipeline', mode: 'PIPELINE', width: 'xl' },
  pollinate: { zone: 'work', navLabel: 'Pollinate', mode: 'POLLINATE', width: 'xl' },
  community: { zone: 'work', navLabel: 'Community', mode: 'COMMUNITY', width: 'xl' },
  // System zone
  jobs: { zone: 'system', navLabel: 'Jobs & Runs', mode: 'JOBS', width: 'xl' },
  capabilities: { zone: 'system', navLabel: 'Capabilities', subtitle: 'What your AI can do', mode: 'CAPABILITIES', width: 'xl' },
  hive: { zone: 'system', navLabel: 'Hive', subtitle: 'Fleet', mode: 'HIVE', width: 'xl' },
  eval: { zone: 'system', navLabel: 'OS Eval', mode: 'EVAL', width: 'xl' },
  settings: { zone: 'system', navLabel: 'Settings', mode: 'SETTINGS', width: 'xl' },
};

/** The row for `id`, or undefined for an id that is not a nav item. */
export function navEntry(id: string | null | undefined): NavEntry | undefined {
  return id != null && Object.prototype.hasOwnProperty.call(NAV, id) ? NAV[id as OverlayId] : undefined;
}

/** The overlay accent: the item's zone colour. */
export function navTint(id: OverlayId): string {
  return ZONE_TINT[NAV[id].zone];
}
