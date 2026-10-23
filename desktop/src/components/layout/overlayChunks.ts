/**
 * overlayChunks — the code of each overlay's content, loaded on demand (design
 * docs/design/left-nav-overlay-single-source.md S4).
 *
 * Every overlay's content is its own chunk: the app's first bundle no longer carries
 * them. `OVERLAY_CHUNK[id]()` is the one dynamic import per overlay — overlaySurfaces
 * builds its `lazy()` components from it, and the nav calls it on hover/focus
 * (prefetchNav) so the chunk is in the browser's module cache before the click.
 * A dynamic import resolves once and is cached, so calling it again is free.
 *
 * Key exports: `OVERLAY_CHUNK`, `loadOverlayChunk(id)`.
 */
import type { OverlayId } from './overlayIds';

export const OVERLAY_CHUNK = {
  history: () => import('./HistoryOverlay'),
  artifacts: () => import('./ArtifactsOverlay'),
  'needs-you': () => import('./NeedYouOverlay'),
  context: () => import('./CMBrainOverlay'),
  library: () => import('./LibraryOverlay'),
  'brain-hub': () => import('./BrainHub'),
  'new-brain': () => import('./NewBrainOverlay'),
  todo: () => import('./ToDoOverlay'),
  pipeline: () => import('./PipelineOverlay'),
  pollinate: () => import('./PollinateOverlay'),
  community: () => import('./CommunityOverlay'),
  jobs: () => import('./JobsRunsOverlay'),
  capabilities: () => import('./CapabilitiesOverlay'),
  hive: () => import('./HiveFleetOverlay'),
  eval: () => import('../../pages/EvalDashboard'),
  settings: () => import('../../pages/SettingsPage'),
  // swarmws: WorkspaceExplorer is also the app's own sidebar, so it is in the first
  // bundle anyway — nothing to split.
} satisfies Partial<Record<OverlayId, () => Promise<unknown>>>;

/** Start loading an overlay's code (no-op when loaded). Never throws. */
export function loadOverlayChunk(id: OverlayId): void {
  const load = (OVERLAY_CHUNK as Partial<Record<OverlayId, () => Promise<unknown>>>)[id];
  void load?.().catch(() => { /* the lazy() render reports it */ });
}
