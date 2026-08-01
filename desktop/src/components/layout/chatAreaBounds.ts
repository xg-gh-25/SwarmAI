/**
 * chatAreaBounds — the live viewport rect of the chat MESSAGE area (the flex-1
 * column between the leftNav and the Radar sidebar).
 *
 * run_a95e266a: the fullscreen card-detail Modal must be bounded to the chat
 * message area, NOT the viewport. The Radar sidebar on the right has a DYNAMIC
 * width (240–600px, user-draggable, localStorage-persisted, hidden when the file
 * panel opens), so the modal cannot subtract a fixed radar width — it must track
 * the message column's real rect.
 *
 * Design (mirrors navSource): a module-level value + subscriber set, fed by a
 * single ResizeObserver on the message-area container (registered by ChatPage via
 * `observeChatArea(el)`). The Modal reads the current rect on open AND subscribes
 * so it re-bounds live if the radar is dragged / hidden / the window resizes.
 *
 * Why module-level (NOT context): the rect changes on every radar-drag frame —
 * routing it through React context would re-render the whole shell. Same rationale
 * as navSource.
 */

export interface ChatAreaRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

let current: ChatAreaRect | null = null;
const subscribers = new Set<(r: ChatAreaRect | null) => void>();
let ro: ResizeObserver | null = null;
let observedEl: HTMLElement | null = null;

function publish(r: ChatAreaRect | null): void {
  current = r;
  subscribers.forEach((fn) => fn(r));
}

function measure(el: HTMLElement): ChatAreaRect {
  const b = el.getBoundingClientRect();
  return { left: b.left, top: b.top, width: b.width, height: b.height };
}

/** ChatPage registers its message-area container here. Returns a cleanup fn.
 *  A ResizeObserver + window resize/scroll keep the rect live (radar drag resizes
 *  this element; window resize / layout shifts move it). */
export function observeChatArea(el: HTMLElement | null): () => void {
  if (!el) return () => {};
  // Defensive: if a previous observer is still live (a second caller, or a
  // remount whose cleanup hasn't run — StrictMode runs cleanup first so this is
  // belt-and-suspenders), disconnect it so we never leak a dangling RO.
  ro?.disconnect();
  observedEl = el;
  publish(measure(el));

  const remeasure = () => { if (observedEl) publish(measure(observedEl)); };

  ro = new ResizeObserver(remeasure);
  ro.observe(el);
  // The element's SIZE change is caught by RO; its POSITION can shift without a
  // size change (e.g. leftNav toggle) — window resize covers the common cases.
  window.addEventListener('resize', remeasure);

  return () => {
    ro?.disconnect();
    ro = null;
    window.removeEventListener('resize', remeasure);
    if (observedEl === el) { observedEl = null; publish(null); }
  };
}

/** Read the current chat-area rect (null if not yet observed). Does not consume. */
export function readChatAreaRect(): ChatAreaRect | null {
  return current;
}

/** Subscribe to live rect changes (radar drag / hide / window resize). */
export function subscribeChatArea(fn: (r: ChatAreaRect | null) => void): () => void {
  subscribers.add(fn);
  return () => { subscribers.delete(fn); };
}
