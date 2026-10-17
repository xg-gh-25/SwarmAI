/**
 * AssistantMessageView × turnView — the folded-process render contract.
 *
 * What is tested (real AssistantMessageView, only leaf contexts mocked):
 * - a 38-tool turn renders ONE open, fixed-height live card while streaming and
 *   ONE folded card from history, where no tool_result was persisted (AC1/AC2)
 * - between-tool notes stay in the stream, in order (OT03: never folded away)
 * - chat display v6 timing: a thought streams open and folds 2s after the next
 *   item; a finished card folds 2s after it stops being live; a user's
 *   open/closed choice beats both; history mounts folded
 * - adjacent finished reads of one file merge into one row
 * - interrupts render outside the fold (AC4)
 * - a trailing ```next-steps block renders as chips, never as raw text, and the
 *   chips drive the existing inject-chat-input contract (AC6)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));
const injectSpy = vi.fn();
vi.mock('../../injectChatInput', () => ({
  dispatchInjectChatInput: (d: unknown) => injectSpy(d),
  INJECT_CHAT_INPUT: 'swarm:inject-chat-input',
}));

const tu = (id: string, summary: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary, category: 'bash' }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const tr = (toolUseId: string): ContentBlock =>
  ({ type: 'tool_result', toolUseId, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const bashRun = (n: number) =>
  Array.from({ length: n }, (_, i) => tu(`t${i}`, `Running: cd /Users/x/repo; grep -n "needle${i}" f${i}.ts`));

function msg(content: ContentBlock[]): Message {
  return { id: 'm1', role: 'assistant', content, timestamp: new Date().toISOString(), isError: false };
}
/** The same message re-rendered as it streams: `rerender(viewOf(next, s))`. */
const viewOf = (c: ContentBlock[], s: boolean) => (
  <ToastProvider><AssistantMessageView message={msg(c)} isStreaming={s} /></ToastProvider>
);
function renderView(content: ContentBlock[], opts: { isStreaming?: boolean; isLastAssistant?: boolean } = {}) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={msg(content)} isStreaming={opts.isStreaming} isLastAssistant={opts.isLastAssistant} />
    </ToastProvider>,
  );
}

beforeEach(() => injectSpy.mockClear());

describe('AssistantMessageView — folded process', () => {
  it('streaming 38 tool calls → one OPEN live card, paged from the end, running step last (AC1, v6)', () => {
    // Real live shape: finished tools carry their tool_result; the last is still running.
    const live = bashRun(38).flatMap((b, i) => (i < 37 ? [b, tr(`t${i}`)] : [b]));
    const { container } = renderView(live, { isStreaming: true });
    expect(screen.getAllByTestId('process-row')).toHaveLength(1);
    // XG 2026-10-17: the work in progress is shown, not folded away.
    const steps = screen.getByTestId('process-steps');
    expect(steps.className).toMatch(/h-\[300px\]/);
    expect(steps.className).toMatch(/overflow-y-auto/);
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(12);
    expect(rows[rows.length - 1].querySelector('.animate-spin')).not.toBeNull();
    expect(rows[rows.length - 1].textContent).toContain('needle37');
    expect(screen.queryByTestId('process-live')).toBeNull();
    expect(container.textContent).not.toContain('/Users/x/repo');
  });

  it('folding the live card by hand still shows what just finished and what runs now', () => {
    const live = bashRun(38).flatMap((b, i) => (i < 37 ? [b, tr(`t${i}`)] : [b]));
    renderView(live, { isStreaming: true });
    fireEvent.click(screen.getByTestId('process-row'));
    expect(screen.queryByTestId('process-steps')).toBeNull();
    expect(screen.getAllByTestId('step-row')).toHaveLength(1);
    expect(screen.getByTestId('process-live-recent').textContent).toContain('needle36');
    expect(screen.getByTestId('process-live').textContent).toContain('needle37');
  });

  it('history (no tool_result) → one folded row, no spinner; expanding shows all 38 (AC2)', () => {
    const { container } = renderView(bashRun(38), { isStreaming: false });
    expect(screen.getAllByTestId('process-row')).toHaveLength(1);
    expect(screen.queryAllByTestId('process-live')).toHaveLength(0);
    expect(container.querySelector('.animate-spin')).toBeNull();
    fireEvent.click(screen.getByTestId('process-row'));
    fireEvent.click(screen.getByTestId('show-all-steps'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(38);
  });

  it('between-tool notes stay in the stream in order; the outcome is always visible (OT03)', () => {
    // XG 2026-10-17 "遵守 streaming response 的顺序": a note is never folded away.
    renderView([tx('PRE_TOOL_note before'), tu('a', 'Running: ls'), tx('POST_TOOL_answer after')]);
    const note = screen.getByText(/PRE_TOOL_note/);
    const row = screen.getByTestId('process-row');
    const answer = screen.getByText(/POST_TOOL_answer/);
    // DOM order = stream order: note, then the folded process, then the answer.
    expect(note.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(row.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
  });

  it('an interrupt renders outside the fold (AC4)', () => {
    const ask = {
      type: 'ask_user_question', toolUseId: 'q1',
      questions: [{ question: 'INTERRUPT_Q which option?', header: 'h', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
    } as ContentBlock;
    renderView([tu('a', 'Running: ls'), ask]);
    expect(screen.getByText(/INTERRUPT_Q/)).toBeTruthy();
  });
});

describe('AssistantMessageView — next steps (AC6)', () => {
  const withSteps = [tu('a', 'Running: ls'), tx('Done.\n\n```next-steps\nStart the pipeline\nOnly fix labels\n```')];

  it('renders chips, never the raw block', () => {
    const { container } = renderView(withSteps, { isLastAssistant: true });
    expect(screen.getAllByTestId('next-step-chip')).toHaveLength(2);
    expect(container.textContent).not.toContain('```');
    expect(container.textContent).not.toContain('next-steps');
  });

  it('clicking the text fills the input; the arrow sends', () => {
    renderView(withSteps, { isLastAssistant: true });
    fireEvent.click(screen.getAllByTestId('next-step-fill')[0]);
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'Start the pipeline', focus: true, autoSend: false });
    fireEvent.click(screen.getAllByTestId('next-step-send')[1]);
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'Only fix labels', focus: true, autoSend: true });
  });

  it('no chips while streaming, on a non-last message, or without a block', () => {
    renderView(withSteps, { isLastAssistant: true, isStreaming: true });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
    renderView(withSteps, { isLastAssistant: false });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
    renderView([tx('plain')], { isLastAssistant: true });
    expect(screen.queryAllByTestId('next-step-chip')).toHaveLength(0);
  });
});

describe('AssistantMessageView — action row gating is unchanged', () => {
  it('a turn that ended mid-process (notes, no answer) still shows the action row', () => {
    renderView([tx('working note'), tu('a', 'Running: ls')], { isLastAssistant: true });
    expect(screen.getByTitle('Copy message')).toBeTruthy();
  });
});

describe('AssistantMessageView — REVIEW fixes', () => {
  it('an unrecognised summary shows the raw text without an invented verb', () => {
    renderView([{ type: 'tool_use', id: 'z', name: 'Weird', summary: 'Weird: some context', category: 'fallback' } as ContentBlock]);
    fireEvent.click(screen.getByTestId('process-row'));
    const row = screen.getByTestId('step-row');
    expect(row.textContent).toContain('Weird: some context');
    expect(row.textContent).not.toContain('Ran');
  });

  it('hotkeys do not fire from a hidden (background) tab', () => {
    const { container } = render(
      <ToastProvider>
        <div aria-hidden="true">
          <AssistantMessageView message={msg([tx('Done.\n```next-steps\nhidden tab action\n```')])} isLastAssistant />
        </div>
      </ToastProvider>,
    );
    expect(container.querySelectorAll('[data-testid="next-step-chip"]').length).toBe(1);
    fireEvent.keyDown(window, { key: '1' });
    expect(injectSpy).not.toHaveBeenCalled();
  });

  it('hotkey 1 fills the first chip from a visible tab', () => {
    renderView([tx('Done.\n```next-steps\nvisible action\n```')], { isLastAssistant: true });
    fireEvent.keyDown(window, { key: '1' });
    expect(injectSpy).toHaveBeenLastCalledWith({ text: 'visible action', focus: true, autoSend: false });
  });
});

describe('AssistantMessageView — REVIEW round 2', () => {
  it('an interrupt is NOT inside any process block (AC4, structural)', () => {
    const ask = {
      type: 'ask_user_question', toolUseId: 'q1',
      questions: [{ question: 'INTERRUPT_Q2?', header: 'h', options: [{ label: 'A', description: 'a' }], multiSelect: false }],
    } as ContentBlock;
    const { container } = renderView([tu('a', 'Running: ls'), ask]);
    const q = screen.getByText(/INTERRUPT_Q2/);
    const proc = container.querySelector('[data-testid="process-block"]');
    expect(proc).not.toBeNull();
    expect(proc!.contains(q)).toBe(false);
  });

  it('a long expanded list is capped with a "show all" control', () => {
    renderView(bashRun(40));
    fireEvent.click(screen.getByTestId('process-row'));
    expect(screen.getAllByTestId('step-row').length).toBeLessThan(40);
    fireEvent.click(screen.getByTestId('show-all-steps'));
    expect(screen.getAllByTestId('step-row')).toHaveLength(40);
  });

  it('a segment the user chose to keep open stays open when streaming ends', () => {
    vi.useFakeTimers();
    try {
      const th = { type: 'thinking', thinking: 'planning' } as ContentBlock;
      // Chosen while ONLY thinking has arrived — the first tool must not re-key it.
      const { rerender } = render(viewOf([th], true));
      fireEvent.click(screen.getByTestId('process-row')); // fold
      fireEvent.click(screen.getByTestId('process-row')); // open again: a choice
      rerender(viewOf([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd')], true));
      rerender(viewOf([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tx('done')], false));
      act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 3); });
      expect(screen.getAllByTestId('step-row')).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('while streaming, a half-arrived next-steps fence never renders', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tx('answer\n```next-st')], { isStreaming: true, isLastAssistant: true });
    expect(container.textContent).not.toContain('```');
  });
});

describe('AssistantMessageView — Gate-2 fixes', () => {
  it('a finished thinking-only segment has a readable label, not a bare chevron', () => {
    const th = { type: 'thinking', thinking: 'deliberating' } as ContentBlock;
    renderView([th, tx('the answer')]);
    expect(screen.getByTestId('process-row').textContent).toMatch(/Thought/);
  });

  it('a long LIVE segment keeps the running step visible', () => {
    const live = bashRun(20).flatMap((b, i) => (i < 19 ? [b, tr(`t${i}`)] : [b]));
    const { container } = renderView(live, { isStreaming: true });
    expect(container.querySelector('[data-testid="step-row"] .animate-spin')).not.toBeNull();
  });
});

describe('AssistantMessageView — live card between steps', () => {
  it('the newest thought streams OPEN in the live card; folded by hand, it still WRAPS', () => {
    const th = { type: 'thinking', thinking: 'Read the config first.\nThen compare both thresholds.' } as ContentBlock;
    renderView([tu('a', 'Running: ls'), tr('a'), th], { isStreaming: true });
    const row = screen.getByTestId('thinking-row');
    expect(row.dataset.open).toBe('true');
    expect(screen.getByTestId('thinking-text').textContent).toContain('Read the config first.');
    expect(row.querySelector('.animate-spin')).not.toBeNull();
    fireEvent.click(screen.getByTestId('process-row'));
    const line = screen.getByTestId('process-live-thinking');
    // XG 2026-10-18 "该换行就换行": both lines, wrapped — not the last line in a truncated slot.
    expect(line.textContent).toBe('Read the config first.\nThen compare both thresholds.');
    expect(line.className).toMatch(/whitespace-pre-wrap/);
    expect(line.className).not.toMatch(/truncate/);
  });

  it('a long or failed outcome wraps on its own line; a short one stays on the row', () => {
    const res = (id: string, outcome: string, isError = false) =>
      ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome }) as ContentBlock;
    const long = 'error: the build failed because the module could not be resolved from src/x.ts';
    renderView([tu('a', 'Running: npm test'), res('a', 'ok'), tu('b', 'Running: make'), res('b', long, true),
      tu('c', 'Running: ls')], { isStreaming: true });
    const [short, wrapped] = screen.getAllByTestId('step-outcome');
    expect(short.tagName).toBe('SPAN');
    expect(wrapped.tagName).toBe('DIV');
    expect(wrapped.textContent).toBe(long);
    expect(wrapped.className).toMatch(/whitespace-pre-wrap/);
    expect(wrapped.className).not.toMatch(/truncate/);
  });

  it('finished turn from history: the card is folded, no live line', () => {
    const th = { type: 'thinking', thinking: 'deliberating' } as ContentBlock;
    renderView([th, tu('a', 'Running: ls'), tx('the answer')]);
    expect(screen.queryByTestId('process-live-thinking')).toBeNull();
    expect(screen.queryByTestId('process-steps')).toBeNull();
    expect(screen.getByTestId('process-row').textContent).toMatch(/Worked/);
    expect(screen.getByTestId('process-row').textContent).toMatch(/1 thought/);
  });

  it('history: expanding the card shows the thought folded to one line', () => {
    const th = { type: 'thinking', thinking: 'first line of the thought\nmore' } as ContentBlock;
    renderView([th, tu('a', 'Running: ls'), tx('the answer')]);
    fireEvent.click(screen.getByTestId('process-row'));
    const row = screen.getByTestId('thinking-row');
    expect(row.dataset.open).toBe('false');
    expect(row.textContent).toContain('first line of the thought');
    expect(screen.queryByTestId('thinking-text')).toBeNull();
  });

  it('a finished step shows what it returned (outcome), errors in red', () => {
    const res = (id: string, outcome: string, isError = false) =>
      ({ type: 'tool_result', toolUseId: id, content: 'x', isError, truncated: false, outcome }) as ContentBlock;
    renderView([tu('a', 'Running: npm test'), res('a', '12 passed in 3.1s'),
      tu('b', 'Running: make'), res('b', 'make: *** no rule', true), tu('c', 'Running: ls')], { isStreaming: true });
    const outcomes = screen.getAllByTestId('step-outcome');
    expect(outcomes.map((o) => o.textContent)).toEqual(['12 passed in 3.1s', 'make: *** no rule']);
    expect(outcomes[1].className).toMatch(/text-red-400/);
  });

  it('after a tool returns and before the next starts, the open card still spins at the bottom', () => {
    renderView([tu('a', 'Running: ls'), tr('a')], { isStreaming: true });
    const idle = screen.getByTestId('process-live-idle');
    expect(idle.querySelector('.animate-spin')).not.toBeNull();
    expect(idle.textContent).toContain('Thinking');
  });

  it('before any content: one "Thinking" line with a spinner, nothing to expand', () => {
    renderView([], { isStreaming: true });
    const row = screen.getByTestId('process-row');
    expect(row.tagName).toBe('DIV');
    expect(row.textContent).toContain('Thinking');
    expect(row.querySelector('.animate-spin')).not.toBeNull();
  });

  it('adjacent finished reads of one file are ONE row with every range', () => {
    const rd = (id: string): ContentBlock =>
      ({ type: 'tool_use', id, name: 'Read', summary: 'Reading /r/src/a.ts', category: 'read' }) as ContentBlock;
    const res = (id: string, outcome: string) =>
      ({ type: 'tool_result', toolUseId: id, content: 'x', isError: false, truncated: false, outcome }) as ContentBlock;
    renderView([rd('a'), res('a', 'lines 1–200'), rd('b'), res('b', 'lines 200–400'), rd('c'), res('c', 'lines 400–480'),
      tu('d', 'Running: ls')], { isStreaming: true });
    const rows = screen.getAllByTestId('step-row');
    expect(rows).toHaveLength(2);
    expect(screen.getByTestId('step-repeat').textContent).toBe('3×');
    expect(rows[0].textContent).toContain('lines 1–200, lines 200–400, lines 400–480');
  });
});

describe('AssistantMessageView — v6 fold timing (XG 2026-10-17 "延后2秒 再折叠")', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });
  const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
  const thoughtOpen = () => screen.getByTestId('thinking-row').dataset.open;

  it('a thought stays open while it streams, and 2s after the next step arrives', () => {
    const { rerender } = render(viewOf([th('step one')], true));
    expect(thoughtOpen()).toBe('true');
    rerender(viewOf([th('step one, then two')], true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 5); });
    expect(thoughtOpen()).toBe('true'); // still the newest item: never folds mid-thought
    rerender(viewOf([th('step one, then two'), tu('a', 'Running: ls')], true));
    expect(thoughtOpen()).toBe('true');
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS - 100); });
    expect(thoughtOpen()).toBe('true');
    act(() => { vi.advanceTimersByTime(200); });
    expect(thoughtOpen()).toBe('false');
    // The card itself is still live, so it stays open.
    expect(screen.getByTestId('process-steps')).toBeTruthy();
  });

  it('a reply after the thought: the card folds 2s later (not at once)', () => {
    const { rerender } = render(viewOf([th('plan the answer')], true));
    rerender(viewOf([th('plan the answer'), tx('Here is the answer')], true));
    expect(screen.getByTestId('process-steps')).toBeTruthy();
    expect(thoughtOpen()).toBe('true');
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.queryByTestId('process-steps')).toBeNull();
    expect(screen.getByTestId('process-row').textContent).toMatch(/Thought/);
  });

  it('a thought the user opened again stays open', () => {
    const { rerender } = render(viewOf([th('one')], true));
    rerender(viewOf([th('one'), tu('a', 'Running: ls')], true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(thoughtOpen()).toBe('false');
    fireEvent.click(screen.getByTestId('thinking-row').querySelector('button')!);
    rerender(viewOf([th('one'), tu('a', 'Running: ls'), tr('a'), tu('b', 'Running: pwd')], true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 3); });
    expect(thoughtOpen()).toBe('true');
  });

  it('a card the user folded while live stays folded after it finishes', () => {
    const { rerender } = render(viewOf([tu('a', 'Running: ls')], true));
    fireEvent.click(screen.getByTestId('process-row'));
    rerender(viewOf([tu('a', 'Running: ls'), tr('a'), tx('done')], false));
    expect(screen.queryByTestId('process-steps')).toBeNull();
  });
});

describe('AssistantMessageView — a running sub-agent keeps the chat alive (XG 2026-10-18 "不能断流")', () => {
  const agent = { type: 'tool_use', id: 'ag', name: 'Agent', summary: 'Agent: Review spec', category: 'agent' } as ContentBlock;
  const sub = (i: number) => ({
    type: 'tool_use', id: `s${i}`, name: 'Bash', summary: `Running: grep -n "x${i}" f.ts`, category: 'bash',
    parentToolUseId: 'ag',
  }) as ContentBlock;
  const subs = (n: number) => Array.from({ length: n }, (_, i) => sub(i));
  const withProgress = (content: ContentBlock[], progress: Message['subAgentProgress'], isStreaming = true) => render(
    <ToastProvider>
      <AssistantMessageView message={{ ...msg(content), subAgentProgress: progress }} isStreaming={isStreaming} />
    </ToastProvider>,
  );

  it('its newest steps and live status show nested under the Agent step', () => {
    withProgress([agent, ...subs(8)], { ag: { parentToolUseId: 'ag', status: 'running', toolUses: 8, durationMs: 75_000 } });
    const detail = screen.getByTestId('agent-detail');
    expect(detail.textContent).toContain('Sub-agent');
    expect(detail.textContent).toContain('8 steps');
    expect(detail.textContent).toContain('1m 15s');
    expect(detail.querySelector('.animate-spin')).not.toBeNull();
    const nested = screen.getByTestId('agent-steps');
    expect(nested.querySelectorAll('[data-testid="step-row"]')).toHaveLength(6);
    expect(nested.textContent).toContain('x7');
    expect(nested.textContent).toContain('2 more');
    // Only the Agent itself is a step of the main card.
    expect(screen.getByTestId('process-row').textContent).toContain('1 step');
  });

  it('before its first step: the tool it is on, from the progress event', () => {
    withProgress([agent], { ag: { parentToolUseId: 'ag', status: 'running', lastToolName: 'Read' } });
    expect(screen.getByTestId('agent-detail').textContent).toContain('now Read');
  });

  it('folded by hand, the live row still shows the sub-agent newest step', () => {
    withProgress([agent, ...subs(3)], {});
    fireEvent.click(screen.getByTestId('process-row'));
    expect(screen.getByTestId('process-live-subagent').textContent).toContain('x2');
  });

  it('from history: folded to a count, opens on click', () => {
    renderView([agent, ...subs(3), { type: 'tool_result', toolUseId: 'ag', isError: false, outcome: 'done' } as ContentBlock,
      tx('Review passed.')]);
    fireEvent.click(screen.getByTestId('process-row'));
    const detail = screen.getByTestId('agent-detail');
    expect(detail.textContent).toContain('3 steps');
    expect(screen.queryByTestId('agent-steps')).toBeNull();
    fireEvent.click(detail.querySelector('button')!);
    expect(screen.getByTestId('agent-steps').querySelectorAll('[data-testid="step-row"]')).toHaveLength(3);
  });
});
