/**
 * overlayTarget — "open this overlay AT this item", across the mount boundary.
 *
 * An overlay opens on its `swarm:show-*` event, and OverlayHost mounts it only then,
 * so a listener inside the overlay misses the very event that mounted it. A caller
 * that wants a specific item (a Brain in Brain Hub, a Run in Pipelines) records it
 * here first; the overlay takes it once on mount. One slot per overlay, consumed on
 * read, so a later plain open lands on the overlay's default view.
 *
 * Exports: setOverlayTarget, takeOverlayTarget, openOverlayAt, openPipelinesFor, BRAIN_TARGET
 */

const slots = new Map<string, string>();

export function setOverlayTarget(overlay: string, target: string): void {
  slots.set(overlay, target);
}

/** The recorded target for `overlay`, removed as it is read; undefined when none. */
export function takeOverlayTarget(overlay: string): string | undefined {
  const t = slots.get(overlay);
  slots.delete(overlay);
  return t;
}

/** Record the target, then open the overlay with the same value in the event detail
 *  (an overlay that is already mounted reads the detail instead of the slot). */
export function openOverlayAt(overlay: 'brain-hub' | 'pipeline', target: string): void {
  setOverlayTarget(overlay, target);
  const detail = overlay === 'brain-hub' ? { brain: target } : { runId: target };
  window.dispatchEvent(new CustomEvent(`swarm:show-${overlay}`, { detail }));
}

/** A Pipelines target that opens the list filtered to one Brain (the Brain map's Runs). */
export const BRAIN_TARGET = 'brain:';

/** Open Pipelines on the runs of one Brain. */
export function openPipelinesFor(brain: string): void { openOverlayAt('pipeline', `${BRAIN_TARGET}${brain}`); }
