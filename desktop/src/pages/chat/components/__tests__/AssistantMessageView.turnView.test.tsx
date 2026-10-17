/**
 * AssistantMessageView × turnView — the folded-process render contract.
 *
 * What is tested (real AssistantMessageView, only leaf contexts mocked):
 * - a 38-tool turn renders ONE process row while streaming (+ one live row) and
 *   ONE process row from history, where no tool_result was persisted (AC1/AC2)
 * - expanding the row reveals every step and every interim note (OT03: no text
 *   block is dropped by the assembly — it is behind disclosure, not lost)
 * - interrupts render outside the fold (AC4)
 * - a trailing ```next-steps block renders as chips, never as raw text, and the
 *   chips drive the existing inject-chat-input contract (AC6)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
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
function renderView(content: ContentBlock[], opts: { isStreaming?: boolean; isLastAssistant?: boolean } = {}) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={msg(content)} isStreaming={opts.isStreaming} isLastAssistant={opts.isLastAssistant} />
    </ToastProvider>,
  );
}

beforeEach(() => injectSpy.mockClear());

describe('AssistantMessageView — folded process', () => {
  it('streaming 38 tool calls → one process row + one live row, no per-tool rows (AC1)', () => {
    // Real live shape: finished tools carry their tool_result; the last is still running.
    const live = bashRun(38).flatMap((b, i) => (i < 37 ? [b, tr(`t${i}`)] : [b]));
    const { container } = renderView(live, { isStreaming: true });
    expect(screen.getAllByTestId('process-row')).toHaveLength(1);
    expect(screen.getAllByTestId('process-live')).toHaveLength(1);
    expect(screen.queryAllByTestId('step-row')).toHaveLength(0);
    expect(container.textContent).not.toContain('/Users/x/repo');
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

  it('interim notes are behind disclosure, never dropped; the outcome is always visible (OT03)', () => {
    renderView([tx('PRE_TOOL_note before'), tu('a', 'Running: ls'), tx('POST_TOOL_answer after')]);
    expect(screen.getByText(/POST_TOOL_answer/)).toBeTruthy();
    expect(screen.queryByText(/PRE_TOOL_note/)).toBeNull();
    fireEvent.click(screen.getByTestId('process-row'));
    expect(screen.getByText(/PRE_TOOL_note/)).toBeTruthy();
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

  it('an expanded segment stays expanded when streaming ends', () => {
    const th = { type: 'thinking', thinking: 'planning' } as ContentBlock;
    const view = (c: ContentBlock[], s: boolean) => (
      <ToastProvider><AssistantMessageView message={msg(c)} isStreaming={s} /></ToastProvider>
    );
    // Opened while ONLY thinking has arrived — the first tool must not re-key it.
    const { rerender } = render(view([th], true));
    fireEvent.click(screen.getByTestId('process-row'));
    rerender(view([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd')], true));
    rerender(view([th, tu('a', 'Running: ls'), tu('b', 'Running: pwd'), tx('done')], false));
    expect(screen.getAllByTestId('step-row')).toHaveLength(2);
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

  it('expanding a long LIVE segment keeps the running step visible', () => {
    const live = bashRun(20).flatMap((b, i) => (i < 19 ? [b, tr(`t${i}`)] : [b]));
    const { container } = renderView(live, { isStreaming: true });
    fireEvent.click(screen.getByTestId('process-row'));
    expect(container.querySelector('[data-testid="step-row"] .animate-spin')).not.toBeNull();
  });
});

describe('AssistantMessageView — live row between steps', () => {
  it('collapsed live row shows the newest thought as one italic line; expanded, the Thought is open', () => {
    const th = { type: 'thinking', thinking: 'Read the config first.\nThen compare both thresholds.' } as ContentBlock;
    renderView([tu('a', 'Running: ls'), tr('a'), th], { isStreaming: true });
    const line = screen.getByTestId('process-live-thinking');
    expect(line.textContent).toBe('Then compare both thresholds.');
    expect(line.className).toMatch(/italic/);
    expect(line.className).toMatch(/truncate/);
    fireEvent.click(screen.getByTestId('process-row'));
    expect(screen.queryByTestId('process-live-thinking')).toBeNull();
    const details = document.querySelector('details');
    expect(details?.open).toBe(true);
    expect(details?.textContent).toContain('Read the config first.');
  });

  it('finished turn: no live thought line, process stays folded', () => {
    const th = { type: 'thinking', thinking: 'deliberating' } as ContentBlock;
    renderView([th, tu('a', 'Running: ls'), tx('the answer')]);
    expect(screen.queryByTestId('process-live-thinking')).toBeNull();
    expect(document.querySelector('details')).toBeNull();
  });

  it('after a tool returns and before the next starts, the live row still spins', () => {
    const { container } = renderView([tu('a', 'Running: ls'), tr('a')], { isStreaming: true });
    const live = screen.getByTestId('process-live');
    expect(live.querySelector('.animate-spin')).not.toBeNull();
    expect(container.textContent).toContain('Thinking');
  });
});
