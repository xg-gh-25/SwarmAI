/**
 * Stream order (XG 2026-10-22: "streaming turns 顺序不错乱").
 *
 * What is tested: a turn streamed block by block into the real AssistantMessageView
 * always reads in STREAM ORDER — every piece the reader can see (a thought, a text, a
 * tool call, a question) appears in the DOM in the order its block arrived, at every
 * frame, after items settle and fold (FOLD_DELAY_MS) mid-stream, at the stream-end
 * reconcile and in the folded history view. Nothing is re-grouped ahead of what came
 * before it.
 *
 * Method: property-based (fast-check). A random turn of thoughts, texts, tool calls
 * (with and without a result yet) and answered questions; every readable block carries
 * a unique `tokNNN` marker. A frame's order is the markers' first occurrence in the
 * container's text; it must be increasing in block index, and the newest block's
 * marker must be on screen (the step working now is shown). Fixed cases cover a
 * pipeline turn (stage rows) and a turn the user stopped (stop receipt).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import * as fc from 'fast-check';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';
import { replayTurn, tu, tr, th, tx } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { cleanup(); vi.useRealTimers(); });

const view = (content: ContentBlock[], isStreaming: boolean) => {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-22T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>;
};

const MARK = /tok[A-Z]*\d+/g;
/** The markers in the order the reader meets them (first occurrence). */
const domOrder = (el: Element): string[] => [...new Set(el.textContent?.match(MARK) ?? [])];
/** Where each marker's block is in the stream. */
const blockIndex = (blocks: ContentBlock[]): Map<string, number> => {
  const at = new Map<string, number>();
  blocks.forEach((b, i) => {
    const text = JSON.stringify(b);
    for (const m of text.match(MARK) ?? []) if (!at.has(m)) at.set(m, i);
  });
  return at;
};
/** '' when the frame reads in stream order, else the first pair out of order. */
function outOfOrder(el: Element, blocks: ContentBlock[]): string {
  const at = blockIndex(blocks);
  const seen = domOrder(el).filter((m) => at.has(m));
  for (let i = 1; i < seen.length; i += 1) {
    if (at.get(seen[i])! < at.get(seen[i - 1])!) return `${seen[i - 1]} (block ${at.get(seen[i - 1])}) shown before ${seen[i]} (block ${at.get(seen[i])})`;
  }
  return '';
}

/** Stream `blocks` one per frame; after every frame check the order, also once the
 *  frame's items have settled and folded; then the reconcile and the folded history. */
function checkStream(blocks: ContentBlock[], settleEvery = 3): string[] {
  vi.useFakeTimers();
  const bad: string[] = [];
  const { container, rerender } = render(view(blocks.slice(0, 1), true));
  for (let n = 1; n <= blocks.length; n += 1) {
    const part = blocks.slice(0, n);
    rerender(view(part, true));
    const a = outOfOrder(container, part);
    if (a) bad.push(`frame ${n}: ${a}`);
    // the step arriving now is on screen (it is what the reader follows)
    const latest = JSON.stringify(part[n - 1]).match(MARK)?.[0];
    if (latest && part[n - 1].type !== 'tool_result' && !domOrder(container).includes(latest)) bad.push(`frame ${n}: the newest step ${latest} is not shown`);
    if (n % settleEvery === 0) { // let what finished settle and fold mid-stream
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
      rerender(view(part, true));
      const b = outOfOrder(container, part);
      if (b) bad.push(`frame ${n} settled: ${b}`);
    }
  }
  rerender(view(blocks, false));
  const c = outOfOrder(container, blocks);
  if (c) bad.push(`reconcile: ${c}`);
  act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
  // the folded turn, every group opened: still in stream order
  for (const btn of [...container.querySelectorAll('[data-testid="group-toggle"],[data-testid="thinking-toggle"]')]) {
    if (btn.getAttribute('aria-expanded') === 'false') act(() => { (btn as HTMLElement).click(); });
  }
  const d = outOfOrder(container, blocks);
  if (d) bad.push(`history: ${d}`);
  cleanup();
  return bad;
}

type Step = 'th' | 'tx' | 'call' | 'q';
function turnOf(steps: Step[], lastCallOpen: boolean): ContentBlock[] {
  const out: ContentBlock[] = [];
  steps.forEach((s, i) => {
    const m = `tok${i}`;
    if (s === 'th') out.push(th(`${m} thinking about it\nmore of ${m}`));
    else if (s === 'tx') out.push(tx(`${m} a note.`));
    else if (s === 'q') out.push({ type: 'ask_user_question', toolUseId: `q${i}`,
      questions: [{ question: `${m} which way?`, header: 'Q', options: [{ label: 'a' }], multiSelect: false }],
      answers: { [`${m} which way?`]: 'a' } } as unknown as ContentBlock);
    else {
      out.push(tu(`c${i}`, `make ${m}`));
      if (!(lastCallOpen && i === steps.length - 1)) out.push(tr(`c${i}`));
    }
  });
  return out;
}

describe('stream order', () => {
  it('the check itself catches a reversed pair (not a vacuous pass)', () => {
    const el = document.createElement('div');
    el.textContent = 'tok2 then tok1';
    expect(outOfOrder(el, [tx('tok1 a'), tx('tok2 b')])).toContain('tok2 (block 1) shown before tok1 (block 0)');
    el.textContent = 'tok1 then tok2';
    expect(outOfOrder(el, [tx('tok1 a'), tx('tok2 b')])).toBe('');
  });

  it('a random turn reads in stream order at every frame, settled, reconciled and in history', () => {
    fc.assert(fc.property(
      fc.array(fc.constantFrom<Step>('th', 'tx', 'call', 'call', 'q'), { minLength: 2, maxLength: 12 }),
      fc.boolean(),
      fc.integer({ min: 1, max: 4 }),
      (steps, open, every) => {
        const bad = checkStream(turnOf(steps, open), every);
        expect(bad.join(' ; ')).toBe('');
      },
    ), { numRuns: 80 });
    // ~1s alone; 80 random turns × up to 12 rendered frames each ran past the global
    // 15s under the full suite's parallel load (seen twice). A counterexample fails
    // at once, so a longer budget hides no failure, only the contention.
  }, 60_000);

  it('regression: a thought after a note that settled empty is shown at once (the step working now)', () => {
    // found by the property above: [call, tx, th] — the note settled with no work, so
    // the thought that arrived under it showed already folded
    expect(checkStream(turnOf(['call', 'tx', 'th'], false)).join(' ; ')).toBe('');
  });

  it('identical calls that merge into ×N keep their place in the stream', () => {
    const blocks = [tx('tok0 go'), tu('a', 'make tok1'), tr('a'), tu('b', 'make tok1'), tr('b'),
      th('tok2 next'), tu('c', 'make tok3'), tr('c'), tx('tok4 done')];
    expect(checkStream(blocks).join(' ; ')).toBe('');
  });

  it('a pipeline turn (stage rows) reads in stream order', () => {
    expect(checkStream(replayTurn()).join(' ; ')).toBe('');
  });

  it('a turn the user stopped: the receipt comes last, the work before it keeps its order', () => {
    const blocks = [th('tok0 look'), tx('tok1 plan'), tu('a', 'make tok2'), tr('a'), th('tok3 then'),
      tu('b', 'make tok4'), tx('---\n*Stopped*'),
      { type: 'turn_stop', calls: [{ toolUseId: 'b', name: 'Bash', elapsedMs: 9000 }], agents: [] } as ContentBlock];
    expect(checkStream(blocks).join(' ; ')).toBe('');
  });
});
