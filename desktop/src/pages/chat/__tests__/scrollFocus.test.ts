/**
 * scrollFocus pure decisions + DOM measuring (XG 2026-10-18 "聚焦").
 *
 * What is tested (jsdom, rects stubbed):
 * - activationLanding: unread finished reply → its start; read / streaming /
 *   never-seen → bottom
 * - sentMessageId: a user's own optimistic bubble at the tail counts; a reply
 *   placeholder (queue drain, retry) or a persisted id does not
 * - tailErrorId: an error bubble at the tail only
 * - isToggleClick: disclosure buttons outside the live card only
 * - pickAnchor / anchorChainFor / anchorDelta: the innermost element spanning
 *   the viewport top, the chain surviving an unmounted inner element, delta sign
 */
import { describe, it, expect, afterEach } from 'vitest';
import {
  ANCHOR_ATTR, activationLanding, anchorChainFor, anchorDelta, isToggleClick, pickAnchor, sentMessageId,
  tailErrorId,
} from '../scrollFocus';
import { stubRect } from '../components/__tests__/scrollTestUtils';
import type { Message } from '../../../types';

// jsdom resolves '#id' through the document, so trees from earlier tests must go.
afterEach(() => { document.body.innerHTML = ''; });

const m = (id: string, role: 'user' | 'assistant'): Message => ({ id, role, content: [], timestamp: 'T' }) as Message;

describe('activationLanding', () => {
  it('a reply that finished while away lands on its start', () => {
    expect(activationLanding({ replyId: 'r1', streaming: true }, 'r1', false)).toEqual({ kind: 'reply', replyId: 'r1' });
    expect(activationLanding({ replyId: 'r1', streaming: false }, 'r2', false)).toEqual({ kind: 'reply', replyId: 'r2' });
  });
  it('everything else lands at the bottom', () => {
    expect(activationLanding({ replyId: 'r1', streaming: false }, 'r1', false)).toEqual({ kind: 'bottom' });
    expect(activationLanding({ replyId: 'r1', streaming: true }, 'r1', true)).toEqual({ kind: 'bottom' });
    expect(activationLanding(null, 'r1', false)).toEqual({ kind: 'bottom' });
    expect(activationLanding({ replyId: 'r1', streaming: true }, null, false)).toEqual({ kind: 'bottom' });
  });
});

describe('sentMessageId', () => {
  it('the user bubble a send just appended (with its placeholder)', () => {
    expect(sentMessageId([m('a', 'assistant'), m('local-1', 'user'), m('local-1-asst', 'assistant')])).toBe('local-1');
    expect(sentMessageId([m('a', 'assistant'), m('queued-x', 'user')])).toBe('queued-x');
  });
  it('not a placeholder alone, not a persisted id, not an older send', () => {
    expect(sentMessageId([m('u', 'user'), m('local-2-asst', 'assistant')])).toBeNull();
    expect(sentMessageId([m('db-1', 'user'), m('db-2', 'assistant')])).toBeNull();
    expect(sentMessageId([m('local-1', 'user'), m('a', 'assistant'), m('b', 'assistant')])).toBeNull();
    expect(sentMessageId([])).toBeNull();
  });
});

describe('tailErrorId', () => {
  it('only an error bubble at the very end', () => {
    const err = { ...m('e1', 'assistant'), isError: true } as Message;
    expect(tailErrorId([m('a', 'assistant'), err])).toBe('e1');
    expect(tailErrorId([err, m('a', 'assistant')])).toBeNull();
    expect(tailErrorId([])).toBeNull();
  });
});

describe('isToggleClick', () => {
  const html = (s: string) => { const d = document.createElement('div'); d.innerHTML = s; document.body.appendChild(d); return d; };
  it('a disclosure button (aria-expanded or data-expands), from its inner span too', () => {
    const d = html('<button aria-expanded="false"><span id="i">x</span></button><button data-expands id="b2">all</button>');
    expect(isToggleClick(d.querySelector('#i'))).toBe(d.querySelector('button'));
    expect(isToggleClick(d.querySelector('#b2'))).toBe(d.querySelector('#b2'));
  });
  it('not plain buttons, not inside the live card, not non-elements', () => {
    const d = html('<button id="c">copy</button><div data-live="true"><button aria-expanded="true" id="l">x</button></div>');
    expect(isToggleClick(d.querySelector('#c'))).toBeNull();
    expect(isToggleClick(d.querySelector('#l'))).toBeNull();
    expect(isToggleClick(null)).toBeNull();
  });
});

describe('anchors', () => {
  function tree() {
    const c = document.createElement('div');
    c.innerHTML = `<div ${ANCHOR_ATTR} id="m1"></div><div ${ANCHOR_ATTR} id="m2"><div ${ANCHOR_ATTR} id="u1"></div><div ${ANCHOR_ATTR} id="u2"><button id="b"></button></div></div>`;
    document.body.appendChild(c);
    const q = (id: string) => c.querySelector(`#${id}`)!;
    return {
      c, q,
      r: {
        c: stubRect(c, { top: 100, height: 500 }),
        m1: stubRect(q('m1'), { top: -300, height: 200 }),
        m2: stubRect(q('m2'), { top: -100, height: 900 }),
        u1: stubRect(q('u1'), { top: -100, height: 150 }),
        u2: stubRect(q('u2'), { top: 50, height: 700 }),
        b: stubRect(q('b'), { top: 400, height: 20 }),
      },
    };
  }
  it('pickAnchor: every element spanning the viewport top, innermost first', () => {
    const { c, q } = tree();
    expect(pickAnchor(c).map((a) => a.el)).toEqual([q('u2'), q('m2')]);
    expect(pickAnchor(c)[0].offset).toBe(-50);
  });
  it('pickAnchor: in a gap, the first element below the line', () => {
    const { c, q, r } = tree();
    r.m2.top = 300; r.u1.top = 300; r.u2.top = 460; r.m1.top = -300;
    expect(pickAnchor(c).map((a) => a.el)).toEqual([q('m2')]);
  });
  it('anchorDelta: how far the held element moved; the chain survives an unmount', () => {
    const { c, q, r } = tree();
    const chain = pickAnchor(c);
    r.u2.top += 120; r.m2.top += 120;
    expect(anchorDelta(c, chain)).toBe(120);
    q('u2').remove();
    r.m2.top -= 40;
    expect(anchorDelta(c, chain)).toBe(80);
    q('m2').remove();
    expect(anchorDelta(c, chain)).toBeNull();
  });
  it('anchorChainFor: the clicked element, then its marked ancestors', () => {
    const { c, q } = tree();
    const chain = anchorChainFor(q('b'), c);
    expect(chain.map((a) => a.el)).toEqual([q('b'), q('u2'), q('m2')]);
    expect(chain[0].offset).toBe(300);
  });
});
