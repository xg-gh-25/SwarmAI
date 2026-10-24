/**
 * TabView viewport focus — follow vs reading (XG 2026-10-18 "聚焦").
 *
 * What is tested (real TabView + MessageStore, bubbles mocked, jsdom with
 * fakeScroller metrics, stubbed rects and a hand-fired ResizeObserver):
 * - a send in this tab brings a reading user back to the bottom; a reply
 *   placeholder alone (queue drain / retry) does not
 * - while reading, a size change keeps the element at the viewport top in place
 * - clicking a toggle holds the clicked row in place and shows "back to latest"
 *   while streaming; a click inside the live card keeps following
 * - the pill click follows again
 * - an error that ends the turn brings a reading user to the bottom
 * - only the user leaves the bottom: a scroll with no wheel / touch / held pointer /
 *   scroll key (the engine clamping after a size change) is pinned back
 * - while its history loads, an empty tab shows a spinner, not the welcome screen
 * - activation: a reply that finished while away and is taller than the viewport
 *   lands on its start; a short one, or an already-seen one, lands at the bottom
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, act, fireEvent } from '@testing-library/react';
import { messageStoreRegistry } from '../../../../stores/MessageStore';
import type { Message } from '../../../../types';
import { afterActivation, fakeScroller, stubRect } from './scrollTestUtils';
import { REPLY_TOP_MARGIN } from '../../scrollFocus';
import { USER_SCROLL_GRACE_MS } from '../TabView';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }) }));
vi.mock('../MessageBubble', () => ({
  MessageBubble: ({ message }: { message: Message }) => (
    <div data-testid={`bubble-${message.id}`}>
      <button type="button" aria-expanded="false" data-testid={`toggle-${message.id}`}>open</button>
      <div data-live="true"><button type="button" aria-expanded="true" data-testid={`live-${message.id}`}>live</button></div>
    </div>
  ),
}));
vi.mock('../WelcomeScreen', () => ({ WelcomeScreen: () => <div data-testid="welcome" /> }));
vi.mock('../../../../components/chat', () => ({ ChatErrorMessage: () => <div /> }));
vi.mock('../../../../components/common', () => ({ Spinner: () => <div /> }));

import { TabView } from '../TabView';

let roCallback: ResizeObserverCallback | null = null;
class MockResizeObserver {
  constructor(cb: ResizeObserverCallback) { roCallback = cb; }
  observe() {}
  unobserve() {}
  disconnect() {}
}
const fireResize = () => act(() => { roCallback?.([], {} as ResizeObserver); });

beforeEach(() => {
  roCallback = null;
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = MockResizeObserver;
  messageStoreRegistry.clear();
});
afterEach(() => { cleanup(); messageStoreRegistry.clear(); });

const msg = (id: string, role: 'user' | 'assistant' = 'assistant'): Message =>
  ({ id, role, content: [{ type: 'text', text: id }], timestamp: 'T' }) as unknown as Message;

function props(tabId: string, isActive: boolean, isStreaming = false) {
  const noop = () => {};
  return {
    tabId, isActive, messages: [] as Message[], isStreaming,
    pendingQuestion: null, activeTabPendingQuestion: null, pendingPermissionRequestId: null,
    contextWarning: null, isWaitingForBusy: false, onCancelBusyWait: noop, hasMoreMessages: false,
    isLoadingOlderMessages: false, onLoadOlder: noop, onAnswerQuestion: noop, onPermissionDecision: noop,
    onEscalationSelect: noop, onCancelQueued: noop, onContinue: noop, onRetryQueueTimeout: noop,
  };
}
const scrollerOf = (c: HTMLElement) => c.querySelector('.chat-scroll-gutter') as HTMLElement;
const rowOf = (c: HTMLElement, id: string) => c.querySelector(`[data-msg-id="${id}"]`) as HTMLElement;

/** Mount active + let the activation frames pass; the scroller starts at the bottom. */
async function mounted(tabId: string, msgs: Message[], streaming = false) {
  messageStoreRegistry.getOrCreate(tabId).replace(msgs);
  const r = render(<TabView {...props(tabId, true, streaming)} />);
  const s = fakeScroller(scrollerOf(r.container), 3000, 500);
  await afterActivation();
  act(() => { scrollerOf(r.container).scrollTop = s.bottom; });
  s.userScroll(s.bottom);
  return { ...r, s };
}

describe('a send brings the view back', () => {
  it('a reading user who sends lands at the bottom', async () => {
    const { s } = await mounted('S1', [msg('u0', 'user'), msg('a0')]);
    s.userScroll(1000);
    act(() => { messageStoreRegistry.getOrCreate('S1').appendMany([msg('local-1', 'user'), msg('local-1-asst')]); });
    await afterActivation(); // the store notifies React on the next frame
    expect(s.top).toBe(s.bottom);
  });

  it('a queued send lands at the bottom too', async () => {
    const { s } = await mounted('S2', [msg('u0', 'user'), msg('a0')], true);
    s.userScroll(1000);
    act(() => { messageStoreRegistry.getOrCreate('S2').append(msg('queued-x', 'user')); });
    await afterActivation(); // the store notifies React on the next frame
    expect(s.top).toBe(s.bottom);
  });

  it('a reply placeholder alone (queue drain, retry) does not move a reading user', async () => {
    const { s, getByTestId } = await mounted('S3', [msg('u0', 'user'), msg('a0')]);
    s.userScroll(1000);
    act(() => { messageStoreRegistry.getOrCreate('S3').append(msg('local-2-asst')); });
    await afterActivation(); // the store notifies React on the next frame
    expect(getByTestId('bubble-local-2-asst')).toBeTruthy(); // it did render
    expect(s.top).toBe(1000);
  });
});

describe('only the user leaves the bottom (XG 2026-10-19)', () => {
  /** Move the clock past the grace window of the last user input. */
  const pastGrace = () => {
    const later = performance.now() + 10 * USER_SCROLL_GRACE_MS;
    vi.spyOn(performance, 'now').mockReturnValue(later);
  };
  afterEach(() => { vi.restoreAllMocks(); });

  it('a scroll the engine makes while following is pinned back, and the pill stays hidden', async () => {
    const { s, queryByTestId } = await mounted('F1', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    s.engineScroll(s.bottom - 800); // e.g. a clamp after a turn re-lays out
    expect(s.top).toBe(s.bottom);
    s.setHeights(3400);
    fireResize(); // the stream keeps growing: still following
    expect(s.top).toBe(s.bottom);
    expect(queryByTestId('streaming-pill')).toBeNull();
  });

  it('the same scroll with a wheel is the user reading', async () => {
    const { s } = await mounted('F2', [msg('u0', 'user'), msg('a0')], true);
    s.userScroll(1000);
    s.setHeights(3400);
    fireResize();
    expect(s.top).toBe(1000);
  });

  it('a scroll while a pointer is held on the scroller (scrollbar drag) is the user', async () => {
    const r = await mounted('F3', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    fireEvent.pointerDown(scrollerOf(r.container));
    r.s.engineScroll(1000);
    fireEvent.pointerUp(window);
    r.s.setHeights(3400);
    fireResize();
    expect(r.s.top).toBe(1000);
  });

  it('typing a space in the composer is not a scroll', async () => {
    const r = await mounted('F6', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    const ta = document.createElement('textarea');
    document.body.appendChild(ta);
    fireEvent.keyDown(ta, { key: ' ' });
    r.s.engineScroll(1000);
    expect(r.s.top).toBe(r.s.bottom);
    ta.remove();
  });

  it('a held pointer cleared by a context menu does not keep counting', async () => {
    const r = await mounted('F7', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    fireEvent.pointerDown(scrollerOf(r.container));
    fireEvent(window, new MouseEvent('contextmenu'));
    r.s.engineScroll(1000);
    expect(r.s.top).toBe(r.s.bottom);
  });

  it('a released pointer stops counting once the grace window passes', async () => {
    const r = await mounted('F8', [msg('u0', 'user'), msg('a0')], true);
    fireEvent.pointerDown(scrollerOf(r.container));
    fireEvent.pointerUp(window);
    pastGrace();
    r.s.engineScroll(1000);
    expect(r.s.top).toBe(r.s.bottom);
  });

  it('momentum from a wheel DOWN does not license an upward engine move', async () => {
    const r = await mounted('F9', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    fireEvent.wheel(scrollerOf(r.container), { deltaY: 120 });
    r.s.engineScroll(1000);
    expect(r.s.top).toBe(r.s.bottom);
  });

  it('a scroll key is the user', async () => {
    const r = await mounted('F4', [msg('u0', 'user'), msg('a0')], true);
    pastGrace();
    fireEvent.keyDown(document.body, { key: 'PageUp' }); // WebKit: a click leaves focus on <body>
    r.s.engineScroll(1000);
    r.s.setHeights(3400);
    fireResize();
    expect(r.s.top).toBe(1000);
  });

  it('a second send merged into the queued bubble brings a reading user back', async () => {
    const { s } = await mounted('F5', [msg('u0', 'user'), msg('a0'), msg('queued-q', 'user')], true);
    s.userScroll(1000);
    act(() => {
      messageStoreRegistry.getOrCreate('F5').updateById('queued-q', (m) => ({ ...m, timestamp: 'T2' }));
    });
    await afterActivation();
    expect(s.top).toBe(s.bottom);
  });
});

describe('an error bubble brings the view back', () => {
  it('a reading user sees the error that ended the turn', async () => {
    const { s } = await mounted('E1', [msg('u0', 'user'), msg('a0')]);
    s.userScroll(1000);
    act(() => { messageStoreRegistry.getOrCreate('E1').append({ ...msg('err-1'), isError: true } as Message); });
    await afterActivation();
    expect(s.top).toBe(s.bottom);
  });
});

describe('loading history', () => {
  it('an empty tab whose history is loading shows a spinner, then its messages', async () => {
    const r = render(<TabView {...props('H1', true)} isLoadingHistory />);
    expect(r.getByTestId('tab-loading')).toBeTruthy();
    expect(r.queryByTestId('welcome')).toBeNull();
    act(() => { messageStoreRegistry.getOrCreate('H1').replace([msg('u1', 'user'), msg('a1')]); });
    await afterActivation();
    r.rerender(<TabView {...props('H1', true)} isLoadingHistory={false} />);
    expect(r.queryByTestId('tab-loading')).toBeNull();
    expect(r.getByTestId('bubble-a1')).toBeTruthy();
  });

  it('an empty tab that is not loading shows the welcome screen', () => {
    const r = render(<TabView {...props('H2', true)} />);
    expect(r.getByTestId('welcome')).toBeTruthy();
    expect(r.queryByTestId('tab-loading')).toBeNull();
  });
});

describe('reading holds what is on screen', () => {
  it('a size change above keeps the row at the viewport top in place', async () => {
    const { container, s } = await mounted('R1', [msg('a1'), msg('a2')]);
    stubRect(scrollerOf(container), { top: 0, height: 500 });
    stubRect(rowOf(container, 'a1'), { top: -1200, height: 1000 });
    const a2 = stubRect(rowOf(container, 'a2'), { top: -200, height: 1500 });
    s.userScroll(1000); // reading; a2 spans the top line
    a2.top += 150; // content above it grew
    fireResize();
    expect(s.top).toBe(1150);
  });

  it('a toggle click holds the clicked row in place and offers "back to latest"', async () => {
    const { container, s, queryByTestId, getByTestId } = await mounted('R2', [msg('a1')], true);
    stubRect(scrollerOf(container), { top: 0, height: 500 });
    const btn = getByTestId('toggle-a1');
    const b = stubRect(btn, { top: 300, height: 20 });
    expect(queryByTestId('streaming-pill')).toBeNull();
    fireEvent.click(btn);
    expect(queryByTestId('streaming-pill')).not.toBeNull();
    const before = s.top;
    s.setHeights(3400);
    b.top -= 100; // content above the clicked row shrank
    fireResize();
    expect(s.top).toBe(before - 100);
  });

  it('a click inside the live card keeps following', async () => {
    const { s, queryByTestId, getByTestId } = await mounted('R3', [msg('a1')], true);
    fireEvent.click(getByTestId('live-a1'));
    expect(queryByTestId('streaming-pill')).toBeNull();
    s.setHeights(3600);
    fireResize();
    expect(s.top).toBe(s.bottom);
  });

  it('the pill follows again', async () => {
    const { s, getByTestId, queryByTestId } = await mounted('R4', [msg('a1')], true);
    fireEvent.click(getByTestId('toggle-a1'));
    s.setHeights(3800);
    fireEvent.click(getByTestId('streaming-pill').querySelector('button')!);
    expect(s.top).toBe(s.bottom);
    expect(queryByTestId('streaming-pill')).toBeNull();
  });
});

describe('activation landing for a reply that finished while away', () => {
  async function awayAndBack(tabId: string, opts: { leftStreaming: boolean; replyHeight: number }) {
    messageStoreRegistry.getOrCreate(tabId).replace([msg('u1', 'user'), msg('r1')]);
    const r = render(<TabView {...props(tabId, true, opts.leftStreaming)} />);
    const s = fakeScroller(scrollerOf(r.container), 4000, 500);
    await afterActivation();
    act(() => { r.rerender(<TabView {...props(tabId, false, opts.leftStreaming)} />); });
    act(() => { r.rerender(<TabView {...props(tabId, false, false)} />); });
    stubRect(scrollerOf(r.container), { top: 0, height: 500 });
    stubRect(rowOf(r.container, 'r1'), { top: 700, height: opts.replyHeight });
    const start = s.top;
    act(() => { r.rerender(<TabView {...props(tabId, true, false)} />); });
    return { ...r, s, start };
  }

  it('unread and taller than the viewport: lands on its start, and stays there', async () => {
    const { s, start } = await awayAndBack('L1', { leftStreaming: true, replyHeight: 1200 });
    expect(s.top).toBe(Math.min(start + 700 - REPLY_TOP_MARGIN, s.bottom));
    const landed = s.top;
    s.setHeights(4400);
    fireResize();
    expect(s.top).toBe(landed);
  });

  it('unread but short: the bottom', async () => {
    const { s } = await awayAndBack('L2', { leftStreaming: true, replyHeight: 300 });
    expect(s.top).toBe(s.bottom);
  });

  it('already seen (left idle, nothing new): the bottom', async () => {
    const { s } = await awayAndBack('L3', { leftStreaming: false, replyHeight: 1200 });
    expect(s.top).toBe(s.bottom);
  });
});
