/**
 * fileChangedBroker — the SINGLE `swarm:file-changed` window listener (D1, run_5d9178bf).
 *
 * ## Why (the OT01 fragmentation this collapses)
 *
 * Five independent consumers each attached their own `window.addEventListener(
 * 'swarm:file-changed', ...)` — FileEditorCore, FileViewer, useChangeStatus,
 * useReferencedFiles, useCanvasAutoSurface. Every backend file-change event woke all
 * five, each re-parsing the detail and re-implementing its own path match. As the
 * Canvas grows to multi-repo (many files, many events), N-listeners × N-events is the
 * exact kind of cross-cutting frontend coupling that made OT01 (the #1 recurrence) so
 * hard: a change to the event shape or tab-scoping had to be mirrored in five places.
 *
 * This broker is ONE listener that fans out to N subscribers. Each subscriber still
 * receives the RAW CustomEvent and keeps its own detail parsing + per-tab filter
 * (behavior-preserving migration — the broker changes WHO listens on the window, not
 * WHAT each consumer does with the event). Per-tab routing is unchanged: subscribers
 * read `detail.tabId` (the SSE-bridge `capturedTabId` stamp) exactly as before.
 *
 * ## Guarantees
 *  - exactly ONE window listener, attached lazily on the first subscribe;
 *  - detached when the last subscriber unsubscribes (no leak across mount cycles);
 *  - a THROWING subscriber never breaks siblings (each dispatch is try/caught) — a
 *    Canvas-tracking consumer failing must not take down the others (C3 fail-open).
 *
 * Module-level singleton (not a hook) so it is independent of React mount ordering —
 * the fragile part of a hook-hosted broker. Consumers subscribe from their own effects.
 */

const EVENT_NAME = 'swarm:file-changed';

type FileChangedListener = (e: CustomEvent) => void;

const subscribers = new Set<FileChangedListener>();
let windowListener: ((e: Event) => void) | null = null;

function ensureAttached(): void {
  if (windowListener) return;
  windowListener = (e: Event) => {
    // Snapshot so a subscriber that unsubscribes during dispatch doesn't mutate
    // the set mid-iteration.
    for (const fn of Array.from(subscribers)) {
      try {
        fn(e as CustomEvent);
      } catch {
        // Fail-open per subscriber (C3): one consumer's error must never break the
        // others or the window dispatch. Swallow — Canvas tracking is best-effort.
      }
    }
  };
  window.addEventListener(EVENT_NAME, windowListener);
}

function detachIfIdle(): void {
  if (subscribers.size === 0 && windowListener) {
    window.removeEventListener(EVENT_NAME, windowListener);
    windowListener = null;
  }
}

/**
 * Subscribe to `swarm:file-changed`. Returns an idempotent unsubscribe function.
 * The underlying window listener is attached on the first subscribe and removed
 * when the last subscriber unsubscribes.
 */
export function subscribeFileChanged(fn: FileChangedListener): () => void {
  subscribers.add(fn);
  ensureAttached();
  let active = true;
  return () => {
    if (!active) return; // idempotent — double-call safe
    active = false;
    subscribers.delete(fn);
    detachIfIdle();
  };
}

/** TEST-ONLY: how many window listeners the broker currently holds (0 or 1). */
export function __brokerListenerCount(): number {
  return windowListener ? 1 : 0;
}
