/**
 * scrollFocus — where a chat tab's viewport should be (XG 2026-10-18).
 *
 * A tab is in one of two modes:
 * - FOLLOW: pinned to the bottom — the live work while streaming, the last
 *   message when idle.
 * - READING: the user is looking at something. The element at the top of the
 *   viewport (or the toggle they clicked) keeps its place on screen whatever
 *   changes size around it — the scroll anchoring WebKit does not do natively.
 *
 * TabView owns the mode; this module holds the decisions and the DOM measuring,
 * so the rules are testable without a layout engine.
 *
 * @exports ANCHOR_ATTR, ANCHOR_PROPS, ScrollAnchor, pickAnchor, anchorChainFor,
 *          anchorDelta, SeenState, activationLanding, sentMessageId, isToggleClick,
 *          REPLY_TOP_MARGIN
 */
import type { Message } from '../../types';

/** Marks an element the reading anchor may hold in place (message rows, turn units, step rows). */
export const ANCHOR_ATTR = 'data-scroll-anchor';
/** Spread onto an anchorable element: `<div {...ANCHOR_PROPS}>`. */
export const ANCHOR_PROPS = { [ANCHOR_ATTR]: '' } as const;
/** Space above a reply when a tab lands on its start. */
export const REPLY_TOP_MARGIN = 8;

/** An element and where its top sat relative to the viewport top when captured. */
export interface ScrollAnchor { el: Element; offset: number }

const topOf = (el: Element) => el.getBoundingClientRect().top;

/**
 * The anchor chain for the current viewport, deepest first: every marked element
 * that spans the viewport's top line, innermost to outermost. When none spans it
 * (a gap between rows), the first marked element below the line. The chain lets
 * the hold survive the innermost element unmounting (e.g. a note that became its
 * work's title when the turn ended).
 */
export function pickAnchor(container: Element): ScrollAnchor[] {
  const line = topOf(container);
  const marked = [...container.querySelectorAll(`[${ANCHOR_ATTR}]`)];
  const spanning: ScrollAnchor[] = [];
  for (const el of marked) {
    const r = el.getBoundingClientRect();
    if (r.top <= line && r.bottom > line) spanning.push({ el, offset: r.top - line });
  }
  if (spanning.length) return spanning.reverse();
  const below = marked.find((el) => topOf(el) > line);
  return below ? [{ el: below, offset: topOf(below) - line }] : [];
}

/** The chain for a clicked element: the element itself, then its marked ancestors. */
export function anchorChainFor(el: Element, container: Element): ScrollAnchor[] {
  const line = topOf(container);
  const chain: ScrollAnchor[] = [{ el, offset: topOf(el) - line }];
  let p = el.parentElement?.closest(`[${ANCHOR_ATTR}]`);
  while (p && container.contains(p)) {
    chain.push({ el: p, offset: topOf(p) - line });
    p = p.parentElement?.closest(`[${ANCHOR_ATTR}]`);
  }
  return chain;
}

/** How far the held element moved down since capture (scrollTop += delta puts it
 *  back); null when no element of the chain is still in the document. */
export function anchorDelta(container: Element, chain: ScrollAnchor[]): number | null {
  const a = chain.find((x) => x.el.isConnected && container.contains(x.el));
  if (!a) return null;
  return topOf(a.el) - topOf(container) - a.offset;
}

/** What the user last saw of a tab: its last reply, and whether it was still running. */
export interface SeenState { replyId: string | null; streaming: boolean }

/**
 * Where a tab lands when it becomes visible. A reply that finished while the user
 * was away (unread) lands on its start, so it reads from the top; everything else
 * lands at the bottom (the live work, or the last message). TabView applies the
 * reply landing only when that reply is taller than the viewport.
 */
export function activationLanding(
  seen: SeenState | null,
  lastReplyId: string | null,
  isStreaming: boolean,
): { kind: 'bottom' } | { kind: 'reply'; replyId: string } {
  if (!seen || isStreaming || !lastReplyId) return { kind: 'bottom' };
  const unread = lastReplyId !== seen.replyId || seen.streaming;
  return unread ? { kind: 'reply', replyId: lastReplyId } : { kind: 'bottom' };
}

/** Optimistic user messages: `local-…` (send) and `queued-…` (sent while busy). */
const SENT_ID = /^(local|queued)-/;

/**
 * The id of a message the user just sent in this tab, if one sits at the tail
 * (the user bubble plus its reply placeholder, or a queued bubble). Placeholders
 * (`…-asst`) are replies, not sends — a queue drain or a retry does not count.
 */
export function sentMessageId(messages: Message[]): string | null {
  for (const m of messages.slice(-2).reverse()) {
    if (m.role === 'user' && SENT_ID.test(m.id)) return m.id;
  }
  return null;
}

/**
 * A click that opens or closes something in the transcript (a step, a thought,
 * a stage, "show all") — the user is reading what they clicked. Clicks inside the
 * live work card keep the tab following: that card IS the bottom.
 */
export function isToggleClick(target: EventTarget | null): Element | null {
  if (!(target instanceof Element)) return null;
  const btn = target.closest('button');
  if (!btn || !(btn.hasAttribute('aria-expanded') || btn.hasAttribute('data-expands'))) return null;
  if (btn.closest('[data-live="true"]')) return null;
  return btn;
}
