/**
 * Turn replay (X7, chat display v8, XG 2026-10-19 — "肉眼欺骗": nothing the reader sees
 * disappears on the spot, and nothing shown jumps).
 *
 * Method: a synthetic turn (fixtures/pipelineBlocks replayTurn, and a plain turn) is
 * streamed into the real AssistantMessageView one content block per frame, with the
 * clock FROZEN — so no item can settle, and anything that folds or vanishes between
 * two frames did so on the spot. Every readable piece of text carries a `tok…` marker;
 * a frame's visible set is the markers in its text. What is tested:
 * - vanish = 0: across streaming frames the visible set only grows
 * - no remount: an element that showed a marker (a block, a checklist line, a thought,
 *   a row, a note) is still the same DOM node in the next frame
 * - the checklist is laid out in the first frame it appears and only changes status:
 *   its line order never changes, no line is ever inserted
 * - the stream-end reconcile (isStreaming → false, same blocks — and the stored copy,
 *   tool results dropped): nothing vanishes or remounts at that moment; FOLD_DELAY_MS
 *   later the pipeline block and the work fold
 * - a tab switch (a fresh mount of the stored copy) shows the same first level and the
 *   same text as the settled turn; a reconnect mid-stream (the stored copy so far
 *   replaces the live one) loses nothing
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';
import { FULL, replayTurn, tu, tr, th, tx } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

afterEach(() => { vi.useRealTimers(); });

const view = (content: ContentBlock[], isStreaming: boolean) => {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>;
};
const MARK = /tok[A-Z]+\d*/g;
const marks = (el: Element) => new Set(el.textContent?.match(MARK) ?? []);
const TRACKED = ['pipeline-block', 'checklist-line', 'work-group', 'thought', 'step-row', 'group-note', 'turn-restatement']
  .map((id) => `[data-testid="${id}"]`).join(',');

interface Replay { violations: string[]; lineOrders: string[][]; final: Set<string>; container: HTMLElement; rerender: (ui: React.ReactElement) => void }

/** Stream `blocks` one per frame, clock frozen; collect every vanish / remount. */
function replay(blocks: ContentBlock[]): Replay {
  vi.useFakeTimers();
  const violations: string[] = [];
  const lineOrders: string[][] = [];
  const { container, rerender } = render(view(blocks.slice(0, 1), true));
  let seen = marks(container);
  let tracked = [...container.querySelectorAll(TRACKED)].map((el) => ({ el, m: marks(el) }));
  for (let n = 2; n <= blocks.length; n += 1) {
    rerender(view(blocks.slice(0, n), true));
    const now = marks(container);
    for (const m of seen) if (!now.has(m)) violations.push(`frame ${n}: ${m} vanished`);
    for (const { el, m } of tracked) {
      if (!el.isConnected && m.size > 0) violations.push(`frame ${n}: ${el.getAttribute('data-testid')} showing ${[...m].join(' ')} remounted`);
    }
    const lines = [...container.querySelectorAll('[data-testid="checklist-line"]')].map((l) => (l as HTMLElement).dataset.stage ?? '');
    if (lines.length) lineOrders.push(lines);
    seen = now;
    tracked = [...container.querySelectorAll(TRACKED)].map((el) => ({ el, m: marks(el) }));
  }
  return { violations, lineOrders, final: seen, container, rerender };
}

describe('turn replay — a pipeline turn', () => {
  it('streaming: nothing vanishes, nothing remounts, the checklist only changes status', () => {
    const blocks = replayTurn();
    const r = replay(blocks);
    expect(r.violations.join(' ; ')).toBe('');
    expect(r.lineOrders.length).toBeGreaterThan(0);
    for (const order of r.lineOrders) expect(order).toEqual(FULL);
    // everything the turn says was on screen by the last streaming frame
    for (const m of ['tokRESTATE', 'tokT1', 'tokR1', 'tokS1', 'tokT3', 'tokM1', 'tokN1', 'tokANS']) expect(r.final.has(m)).toBe(true);
  });

  it('the stream-end reconcile: nothing vanishes at that moment; FOLD_DELAY_MS later the block folds', () => {
    const blocks = replayTurn();
    const r = replay(blocks);
    const before = marks(r.container);
    const tracked = [...r.container.querySelectorAll(TRACKED)].filter((el) => marks(el).size > 0);
    r.rerender(view(blocks, false));
    const at = marks(r.container);
    expect([...before].filter((m) => !at.has(m))).toEqual([]);
    expect(tracked.filter((el) => !el.isConnected).map((el) => el.getAttribute('data-testid'))).toEqual([]);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(r.container.querySelectorAll('[data-testid="checklist-line"]')).toHaveLength(0);
    const after = marks(r.container);
    expect(after.has('tokANS')).toBe(true); // the answer stays
    expect(after.has('tokRESTATE')).toBe(true); // so does the restatement
    expect(after.has('tokT1')).toBe(false); // the process folded
  });
});

describe('turn replay — a plain turn', () => {
  const plain = (): ContentBlock[] => [
    th('tokT0 first look'), tx('I read it as: tokRESTATE.'),
    tu('a', 'cat src/tokR1.ts'), tr('a'), th('tokT1 so the store is the writer'), tu('b', 'grep -rn "tokS1" src'), tr('b'),
    tu('c', 'make tokM1'), tr('c'), tx('tokN1 one more check.'), tu('d', 'make tokM1'), tr('d'),
    th('tokT2 that settles it'), tx('tokANS the answer.'),
  ];

  it('streaming: nothing vanishes and nothing remounts', () => {
    const r = replay(plain());
    expect(r.violations.join(' ; ')).toBe('');
    for (const m of ['tokRESTATE', 'tokT0', 'tokT1', 'tokR1', 'tokS1', 'tokM1', 'tokN1', 'tokT2', 'tokANS']) expect(r.final.has(m)).toBe(true);
  });

  it('the reconcile keeps everything for FOLD_DELAY_MS, then the work folds and the answer stays', () => {
    const blocks = plain();
    const r = replay(blocks);
    const before = marks(r.container);
    const tracked = [...r.container.querySelectorAll(TRACKED)].filter((el) => marks(el).size > 0);
    r.rerender(view(blocks, false));
    expect([...before].filter((m) => !marks(r.container).has(m))).toEqual([]);
    expect(tracked.filter((el) => !el.isConnected).map((el) => el.getAttribute('data-testid'))).toEqual([]);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    const after = marks(r.container);
    expect(after.has('tokANS')).toBe(true);
    expect(after.has('tokR1')).toBe(false);
  });
});

/** The stored copy of a turn: the backend persists tool results only in compact form,
 *  and older turns have none — the harshest case is no tool_result blocks at all. */
const stored = (blocks: ContentBlock[]) => blocks.filter((b) => b.type !== 'tool_result');
/** The first level of a rendered turn, in order. */
const firstLevel = (root: HTMLElement) => [...root.querySelectorAll('[data-testid="pipeline-block"],[data-testid="turn-restatement"],[data-testid="turn-result"],[data-testid="work-group"]:not([data-flat])')]
  .filter((el) => !el.parentElement?.closest('[data-testid="pipeline-block"],[data-testid="work-group"]'))
  .map((el) => el.getAttribute('data-testid'));

describe('turn replay — the stored copy, a tab switch, a reconnect', () => {
  for (const [name, turn] of [['pipeline', replayTurn], ['plain', () => [
    th('tokT0 first look'), tx('I read it as: tokRESTATE.'), tu('a', 'cat src/tokR1.ts'), tr('a'),
    th('tokT1 next'), tu('b', 'make tokM1'), tr('b'), tx('tokANS the answer.'),
  ] as ContentBlock[]]] as const) {
    it(`${name}: the stream-end reconcile to the stored copy (no tool results) loses nothing at that moment`, () => {
      const blocks = turn();
      const r = replay(blocks);
      const before = marks(r.container);
      const tracked = [...r.container.querySelectorAll(TRACKED)].filter((el) => marks(el).size > 0);
      r.rerender(view(stored(blocks), false));
      expect([...before].filter((m) => !marks(r.container).has(m))).toEqual([]);
      expect(tracked.filter((el) => !el.isConnected).map((el) => el.getAttribute('data-testid'))).toEqual([]);
    });

    it(`${name}: a tab switch (a fresh mount of the stored copy) shows the same first level as the settled turn`, () => {
      const blocks = turn();
      const r = replay(blocks);
      r.rerender(view(stored(blocks), false));
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
      const settled = firstLevel(r.container);
      const settledMarks = marks(r.container);
      expect(settled).toContain(name === 'pipeline' ? 'pipeline-block' : 'work-group');
      expect(settled).toContain('turn-result');
      expect(settledMarks.has('tokANS')).toBe(true);
      const fresh = render(view(stored(blocks), false));
      expect(firstLevel(fresh.container)).toEqual(settled);
      expect(marks(fresh.container)).toEqual(settledMarks);
    });

    it(`${name}: a reconnect mid-stream (the stored copy so far replaces the live one) loses nothing`, () => {
      const blocks = turn();
      const half = Math.floor(blocks.length / 2);
      const r = replay(blocks.slice(0, half));
      const before = marks(r.container);
      expect(before.size).toBeGreaterThan(2);
      r.rerender(view(stored(blocks.slice(0, half)), true));
      expect([...before].filter((m) => !marks(r.container).has(m))).toEqual([]);
    });
  }
});
