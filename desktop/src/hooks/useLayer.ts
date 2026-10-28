/**
 * Layers: what every surface that sits above the page (a dialog, a side panel, a
 * popover, a question asked in place) does with focus and Escape, in one place so
 * they all behave the same.
 *
 * - ``useLayerFocus``: focus moves in when the layer opens, Tab stays inside it,
 *   and focus goes back to what opened it when it closes.
 * - ``useEscape``: Escape goes to the topmost open layer only, so a dialog inside an
 *   overlay closes without closing the overlay, and a field inside the layer that takes
 *   Escape for itself (a rename, a search box) keeps it.
 * - ``useDialogLayer``: both, for a dialog that draws its own frame.
 *
 * @exports useLayerFocus, useEscape, useDialogLayer, focusables
 */
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, iframe, audio[controls], video[controls], [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

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

const trapStack: object[] = [];

/** Focus for a layer, one mechanism for every layer in the overlay:
 *  - it moves in when the layer opens (to the layer, or to its first control with
 *    ``first``; a control inside that already has focus, an autoFocus field, keeps
 *    it), and again when ``resetKey`` changes (the panel swapped a case for the
 *    form and the focused button is gone);
 *  - Tab stays inside the topmost layer (``trap: false`` for a layer that leaves the
 *    rest of the window usable: the overlay host, beside the left nav). The trap
 *    listens on the window, not on the layer, so it still holds when focus has dropped
 *    to <body> because the focused control was removed;
 *  - focus returns to what opened the layer, or to ``fallback()`` when that is gone
 *    (the layer was opened from a place that no longer exists). */
export function useLayerFocus(open: boolean, ref: RefObject<HTMLElement | null>,
  opts: { resetKey?: string; first?: boolean; trap?: boolean; fallback?: () => HTMLElement | null } = {}) {
  const opener = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  const { resetKey, first, trap = true } = opts;
  const fallback = useRef(opts.fallback);
  fallback.current = opts.fallback;
  // The opener is read in the render that opens the layer: by the time an effect runs,
  // a field inside with autoFocus has already taken focus and the opener is unknown.
  if (open && !wasOpen.current && !opener.current) {
    const active = typeof document === 'undefined' ? null : document.activeElement as HTMLElement | null;
    if (active && !ref.current?.contains(active)) opener.current = active;
  }
  wasOpen.current = open;
  useEffect(() => {
    if (!open) return;
    // the opener is taken once per opening: on a resetKey change the focused button
    // may already be gone, and focus sits on <body>
    const active = document.activeElement as HTMLElement | null;
    if (!opener.current && !ref.current?.contains(active)) opener.current = active;
    // a control inside that already took focus (an autoFocus field) keeps it
    if (ref.current?.contains(active) && active !== ref.current) return;
    const target = (first && focusables(ref.current)[0]) || ref.current;
    target?.focus();
  }, [open, resetKey, first, ref]);
  useEffect(() => {
    if (!open || !trap) return;
    // Only the topmost layer traps Tab: with a confirm open over a dialog, the dialog's
    // trap would otherwise pull focus back into itself on every Tab.
    const token = {};
    trapStack.push(token);
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !ref.current || trapStack[trapStack.length - 1] !== token) return;
      const items = focusables(ref.current);
      const at = document.activeElement;
      if (!items.length) { e.preventDefault(); ref.current.focus(); return; }
      const head = items[0], tail = items[items.length - 1];
      if (!ref.current.contains(at)) { e.preventDefault(); (e.shiftKey ? tail : head).focus(); }
      else if (e.shiftKey && (at === head || at === ref.current)) { e.preventDefault(); tail.focus(); }
      else if (!e.shiftKey && at === tail) { e.preventDefault(); head.focus(); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      const i = trapStack.lastIndexOf(token);
      if (i >= 0) trapStack.splice(i, 1);
    };
  }, [open, ref, trap]);
  useEffect(() => {
    if (open) return;
    const back = opener.current;
    if (!back) return;
    opener.current = null;
    if (back.isConnected && back !== document.body) back.focus();
    else fallback.current?.()?.focus();
  }, [open]);
}

/** Escape goes to the topmost layer only, and every surface that closes on Escape
 *  registers here (a dialog, the overlay host, a menu, a popover, a question asked in
 *  place); nothing else listens for Escape on the document or the window (a test in
 *  ``__tests__/escapeOwnership.test.ts`` keeps it that way).
 *
 *  Who gets the key depends on where focus is:
 *  - focus inside the top layer (its ``ref`` contains the focused element): the
 *    control under it has the first say. A rename field, a search box, a slash-command
 *    list or the editor take Escape for themselves and mark it handled
 *    (``preventDefault`` or ``stopPropagation``); only an Escape nobody handled closes
 *    the layer, after it has bubbled up to the window.
 *  - focus outside the top layer, or a layer that gives no ``ref`` (a menu opened by
 *    the mouse while focus stays in the terminal, which would otherwise take the key
 *    for the shell): the layer takes it first, in the capture phase, and stops it.
 *  A key that ends an input-method composition is not an Escape for the layer. */
type EscapeEntry = { current: () => void; ref?: RefObject<HTMLElement | null> };
const escapeStack: EscapeEntry[] = [];
const isEscape = (e: KeyboardEvent) => e.key === 'Escape' && !e.isComposing && e.keyCode !== 229;
const focusInside = (top: EscapeEntry, e: KeyboardEvent) =>
  !!top.ref?.current && e.target instanceof Node && top.ref.current.contains(e.target);
function onEscapeCapture(e: KeyboardEvent) {
  const top = escapeStack[escapeStack.length - 1];
  if (!top || !isEscape(e) || focusInside(top, e)) return;
  e.stopImmediatePropagation();
  e.preventDefault();
  top.current();
}
function onEscapeBubble(e: KeyboardEvent) {
  const top = escapeStack[escapeStack.length - 1];
  if (!top || !isEscape(e) || e.defaultPrevented || !focusInside(top, e)) return;
  e.preventDefault();
  top.current();
}
export function useEscape(active: boolean, onEscape: () => void, ref?: RefObject<HTMLElement | null>) {
  // the stack calls the handler of the latest render, not the one it was added with
  const latest = useRef<EscapeEntry>({ current: onEscape, ref });
  useLayoutEffect(() => { latest.current.current = onEscape; latest.current.ref = ref; });
  useEffect(() => {
    if (!active) return;
    const entry = latest.current;
    escapeStack.push(entry);
    if (escapeStack.length === 1) {
      window.addEventListener('keydown', onEscapeCapture, true);
      window.addEventListener('keydown', onEscapeBubble);
    }
    return () => {
      const i = escapeStack.lastIndexOf(entry);
      if (i >= 0) escapeStack.splice(i, 1);
      if (!escapeStack.length) {
        window.removeEventListener('keydown', onEscapeCapture, true);
        window.removeEventListener('keydown', onEscapeBubble);
      }
    };
  }, [active]);
}

/** A dialog: Escape on the stack (the dialog's own controls first) and focus managed,
 *  for a dialog that draws its own frame instead of using ``Modal``. Give the element
 *  ``tabIndex={-1}``, ``role="dialog"``, ``aria-modal`` and a label. */
export function useDialogLayer(open: boolean, ref: RefObject<HTMLElement | null>, onEscape: () => void,
  opts: { first?: boolean; trap?: boolean } = {}) {
  useEscape(open, onEscape, ref);
  useLayerFocus(open, ref, opts);
}
