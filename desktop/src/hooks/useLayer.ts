/**
 * Layers: what every surface that sits above the page (a dialog, a side panel, a
 * popover, a question asked in place) does with focus and Escape, in one place so
 * they all behave the same.
 *
 * - ``useLayerFocus``: focus moves in when the layer opens, Tab stays inside it,
 *   and focus goes back to what opened it when it closes.
 * - ``useEscape``: Escape goes to the topmost open layer only, so a dialog inside an
 *   overlay closes without closing the overlay.
 *
 * @exports useLayerFocus, useEscape, focusables
 */
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

/** A control a person can see: not under a closed <details> (its own summary is),
 *  not hidden. The Tab trap counts only these: a hidden last control would let
 *  Tab slip out of the layer. */
function reachable(el: HTMLElement): boolean {
  if (el.closest('[hidden], [aria-hidden="true"], [inert]')) return false;
  let child: HTMLElement = el;
  for (let n = el.parentElement; n; child = n, n = n.parentElement) {
    if (n.tagName === 'DETAILS' && !(n as HTMLDetailsElement).open && !(child.tagName === 'SUMMARY' && child.parentElement === n)) return false;
  }
  return true;
}
export const focusables = (root: HTMLElement | null) =>
  root ? Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(reachable) : [];

/** Focus for a layer, one mechanism for every layer in the overlay:
 *  - it moves in when the layer opens (to the layer, or to its first control with
 *    ``first``), and again when ``resetKey`` changes (the panel swapped a case for the
 *    form and the focused button is gone);
 *  - Tab stays inside. The trap listens on the window, not on the layer, so it still
 *    holds when focus has dropped to <body> because the focused control was removed;
 *  - focus returns to what opened the layer, or to ``fallback()`` when that is gone
 *    (the layer was opened from a place that no longer exists). */
export function useLayerFocus(open: boolean, ref: RefObject<HTMLElement | null>,
  opts: { resetKey?: string; first?: boolean; fallback?: () => HTMLElement | null } = {}) {
  const opener = useRef<HTMLElement | null>(null);
  const { resetKey, first } = opts;
  const fallback = useRef(opts.fallback);
  fallback.current = opts.fallback;
  useEffect(() => {
    if (!open) return;
    // the opener is taken once per opening: on a resetKey change the focused button
    // may already be gone, and focus sits on <body>
    const active = document.activeElement as HTMLElement | null;
    if (!opener.current && !ref.current?.contains(active)) opener.current = active;
    const target = (first && focusables(ref.current)[0]) || ref.current;
    target?.focus();
  }, [open, resetKey, first, ref]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !ref.current) return;
      const items = focusables(ref.current);
      const at = document.activeElement;
      if (!items.length) { e.preventDefault(); ref.current.focus(); return; }
      const head = items[0], tail = items[items.length - 1];
      if (!ref.current.contains(at)) { e.preventDefault(); (e.shiftKey ? tail : head).focus(); }
      else if (e.shiftKey && (at === head || at === ref.current)) { e.preventDefault(); tail.focus(); }
      else if (!e.shiftKey && at === tail) { e.preventDefault(); head.focus(); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [open, ref]);
  useEffect(() => {
    if (open) return;
    const back = opener.current;
    if (!back) return;
    opener.current = null;
    if (back.isConnected && back !== document.body) back.focus();
    else fallback.current?.()?.focus();
  }, [open]);
}

/** Escape goes to the topmost layer only. Every open layer (the Run menu, the side
 *  panel, a question asked in place) registers here; one listener, in the capture
 *  phase on the window, calls the newest one and stops the key, so a question inside
 *  the panel is cancelled without closing the panel, and the overlay host (which
 *  listens on document) never sees it. A key that ends an input-method composition
 *  is not an Escape for the layer. */
const escapeStack: { current: () => void }[] = [];
function onEscapeKey(e: KeyboardEvent) {
  if (e.key !== 'Escape' || e.isComposing || e.keyCode === 229) return;
  const top = escapeStack[escapeStack.length - 1];
  if (!top) return;
  e.stopImmediatePropagation();
  e.preventDefault();
  top.current();
}
export function useEscape(active: boolean, onEscape: () => void) {
  // the stack calls the handler of the latest render, not the one it was added with
  const latest = useRef(onEscape);
  useLayoutEffect(() => { latest.current = onEscape; });
  useEffect(() => {
    if (!active) return;
    const entry = latest;
    escapeStack.push(entry);
    if (escapeStack.length === 1) window.addEventListener('keydown', onEscapeKey, true);
    return () => {
      const i = escapeStack.lastIndexOf(entry);
      if (i >= 0) escapeStack.splice(i, 1);
      if (!escapeStack.length) window.removeEventListener('keydown', onEscapeKey, true);
    };
  }, [active]);
}

