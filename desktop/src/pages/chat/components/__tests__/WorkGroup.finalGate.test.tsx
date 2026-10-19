/**
 * Regression tests for the Final Quality Gate findings (chat display v8): each case
 * is a scenario the gate's probe reproduced, kept so it cannot come back.
 *
 * What is tested (real WorkGroupView / AssistantMessageView):
 * - a thought the user opened is never absorbed into an Explored row
 * - a thought merged into a call stays hidden when that call joins a ×N fold
 * - an open sub-agent's calls keep the 2s settle: a call that just finished stays a row
 * - an unanswered question inside a stage keeps the pipeline block open (history too)
 * - the pipeline block does not remount when its run block arrives after the first row
 * - the first stage's block does not remount when the lead thoughts join it at the settle
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React, { useState } from 'react';
import { WorkGroupView } from '../WorkGroup';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import { deriveTurnView, groupWork } from '../../turnView';
import type { WorkGroup } from '../../turnView';
import type { ContentBlock, Message } from '../../../../types';
import { runBlock, row } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
afterEach(() => { vi.useRealTimers(); });

const sh = (id: string, cmd: string, parent?: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash', ...(parent ? { parentToolUseId: parent } : {}) }) as ContentBlock;
const ok = (id: string, parent?: string) =>
  ({ type: 'tool_result', toolUseId: id, content: 'x', isError: false, truncated: false, outcome: 'ok', ...(parent ? { parentToolUseId: parent } : {}) }) as ContentBlock;
const th = (t: string) => ({ type: 'thinking', thinking: t }) as ContentBlock;
const tx = (t: string) => ({ type: 'text', text: t }) as ContentBlock;

const groupOf = (content: ContentBlock[], streaming: boolean): WorkGroup =>
  groupWork(deriveTurnView(content, streaming).units, streaming).find((x): x is WorkGroup => x.kind === 'group')!;
function Harness({ g, expanded }: { g: WorkGroup; expanded?: boolean }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <WorkGroupView group={g} expanded={expanded} onToggle={() => {}} thoughtOpen={(k) => open[k]}
      onThoughtToggle={(k, o) => setOpen((m) => ({ ...m, [k]: o }))} renderNote={(text, key) => <p key={key}>{text}</p>} />
  );
}
const view = (g: WorkGroup, expanded?: boolean) => <Harness g={g} expanded={expanded} />;
const msgView = (content: ContentBlock[], isStreaming: boolean) => {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T10:00:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>;
};

describe('DELIVER expert regressions', () => {
  it('a merged thought never goes into an Agent row (it is itself a button)', () => {
    const agent = { type: 'tool_use', id: 'ag', name: 'Agent', summary: 'Agent: review', category: 'agent' } as ContentBlock;
    const agentDone = { type: 'tool_result', toolUseId: 'ag', content: 'x', isError: false, outcome: 'ok',
      subagent: { status: 'completed', toolUses: 1, durationMs: 1000, summary: 'ok' } } as unknown as ContentBlock;
    render(view(groupOf([sh('a', 'make'), ok('a'), th('now review'), agent, agentDone, tx('done')], false), true));
    expect(screen.getAllByTestId('thought')).toHaveLength(1);
    expect(screen.getByTestId('agent-toggle').querySelector('button')).toBeNull();
  });

  it('the pipeline block stays live through narration after a stage ends mid-run', () => {
    vi.useFakeTimers();
    const head = [tx('I read it as: X.'), runBlock(), row('evaluate', 'in_progress'), sh('a', 'make'), ok('a'), row('evaluate', 'completed')];
    const { rerender } = render(msgView([...head, tx('Evaluate done, moving on.')], true));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS * 3); });
    expect(screen.getAllByTestId('checklist-line').length).toBeGreaterThan(0);
    rerender(msgView([...head, tx('Evaluate done, moving on.'), row('think', 'in_progress')], true));
    expect(screen.getAllByTestId('checklist-line').length).toBeGreaterThan(0);
  });

  it('a continuation turn whose run block has no current marks the lines before its first work done', () => {
    render(msgView([tx('Continuing.'), runBlock({ current: null }), row('build', 'in_progress'), sh('a', 'make')], true));
    const st = Object.fromEntries(screen.getAllByTestId('checklist-line').map((l) => [l.dataset.stage, l.dataset.status]));
    expect(st.evaluate).toBe('done');
    expect(st.plan).toBe('done');
    expect(st.build).toBe('running');
    expect(st.review).toBe('pending');
  });

  it('an older turn with no checklist lays its rows onto the run block\'s stage list', () => {
    render(msgView([tx('Go.'), runBlock({ checklist: undefined }), row('evaluate', 'in_progress'), sh('a', 'make')], true));
    expect(screen.getAllByTestId('checklist-line').map((l) => l.dataset.stage))
      .toEqual(['evaluate', 'think', 'plan', 'build', 'review', 'test', 'deliver', 'reflect']);
  });
});

describe('Final Quality Gate regressions', () => {
  it('a thought duration survives the stream-end reconcile that renames the message (client id → DB id)', () => {
    vi.useFakeTimers();
    const content = (done: boolean): ContentBlock[] => [sh('a', 'make'), ok('a'), th('weighing it'),
      ...(done ? [sh('b', 'make lint'), ok('b'), tx('done')] : [])];
    const live: Message = { id: 'local-c1-asst', role: 'assistant', content: content(false), timestamp: '2026-10-19T10:00:00+08:00', isError: false };
    const { rerender } = render(<ToastProvider><AssistantMessageView message={live} isStreaming isLastAssistant /></ToastProvider>);
    act(() => { vi.advanceTimersByTime(7000); });
    rerender(<ToastProvider><AssistantMessageView message={{ ...live, content: [...content(false).slice(0, -1), th('weighing it more')] }} isStreaming isLastAssistant /></ToastProvider>);
    const stored = { ...live, id: 'db-uuid-1', metadata: { client_id: 'local-c1-asst' }, content: content(true) } as Message;
    rerender(<ToastProvider><AssistantMessageView key="db-uuid-1" message={stored} isStreaming={false} isLastAssistant /></ToastProvider>);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    fireEvent.click(screen.getByTestId('group-toggle'));
    fireEvent.click(screen.getAllByTestId('row-toggle').at(-1)!);
    expect(screen.getByTestId('thought-seconds').textContent).toBe('· 7s');
  });

  it('a thought the user opened between two reads stays readable (not absorbed into Explored)', () => {
    vi.useFakeTimers();
    const head = [sh('a', 'cat a.ts'), ok('a'), th('read this one'), sh('b', 'cat b.ts')];
    const { rerender } = render(view(groupOf(head, true)));
    fireEvent.click(screen.getByTestId('thought-toggle'));
    rerender(view(groupOf([...head, ok('b'), sh('c', 'make')], true)));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getByTestId('thought-text').textContent).toBe('read this one');
  });

  it('a thought merged into a call stays merged when that call joins a ×N fold', () => {
    render(view(groupOf([sh('a', 'make a'), ok('a'), th('between'), sh('b', 'make b'), ok('b'), sh('c', 'make b'), ok('c'), tx('done')], false), true));
    expect(screen.queryAllByTestId('thought')).toHaveLength(0);
    expect(screen.getByTestId('step-fold').textContent).toContain('×2');
  });

  it('an open running sub-agent: a call that just finished stays a row until it settles', () => {
    vi.useFakeTimers();
    const agent = { type: 'tool_use', id: 'ag', name: 'Agent', summary: 'Agent: review', category: 'agent' } as ContentBlock;
    const head = [agent, sh('s1', 'cat a.ts', 'ag'), ok('s1', 'ag'), sh('s2', 'cat b.ts', 'ag')];
    const { rerender } = render(view(groupOf(head, true)));
    fireEvent.click(screen.getByTestId('agent-toggle'));
    rerender(view(groupOf([...head, ok('s2', 'ag'), sh('s3', 'make', 'ag')], true)));
    const detail = () => screen.getByTestId('agent-detail');
    expect(detail().querySelectorAll('[data-testid="step-explored"]')).toHaveLength(0); // not on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(detail().querySelectorAll('[data-testid="step-explored"]')).toHaveLength(1);
  });

  it('an unanswered question inside a stage keeps the pipeline block open, also from history', () => {
    const ask = { type: 'ask_user_question', toolUseId: 'q', questions: [{ question: 'Which one?', options: [] }] } as unknown as ContentBlock;
    render(msgView([tx('I read it as: X.'), runBlock(), row('build', 'in_progress'), sh('a', 'make'), ok('a'), ask, tx('I need your pick above.')], false));
    expect(screen.getAllByTestId('checklist-line').length).toBeGreaterThan(0);
  });

  it('the pipeline block keeps its element when the run block arrives after the first stage row', () => {
    const head = [tx('I read it as: X.'), row('evaluate', 'in_progress'), sh('a', 'cat a.ts')];
    const { rerender } = render(msgView(head, true));
    const block = screen.getByTestId('pipeline-block');
    rerender(msgView([...head, runBlock()], true));
    expect(screen.getByTestId('pipeline-block')).toBe(block);
  });

  it("the first stage's work keeps its element when the lead thoughts join it at the settle", () => {
    vi.useFakeTimers();
    const blocks = [th('lead thought'), tx('I read it as: X.'), runBlock(), row('evaluate', 'in_progress'), sh('a', 'make'), ok('a'),
      row('evaluate', 'completed'), tx('the answer')];
    const { rerender } = render(msgView(blocks.slice(0, -1), true));
    rerender(msgView(blocks, false));
    // the user keeps the block and the EVALUATE line open (each is open in the linger)
    // — an explicit choice, so it stays open past the settle: toggle until it is open.
    const openByHand = (isOpen: () => boolean, toggle: () => void) => { toggle(); if (!isOpen()) toggle(); };
    openByHand(() => screen.queryAllByTestId('checklist-line').length > 0, () => fireEvent.click(screen.getByTestId('pipeline-head')));
    const line = () => screen.getAllByTestId('checklist-line').find((l) => l.dataset.stage === 'evaluate')!;
    openByHand(() => !!line().querySelector('[data-testid="work-group"]'), () => fireEvent.click(line().querySelector('button')!));
    const group = line().querySelector('[data-testid="work-group"]');
    expect(group).not.toBeNull();
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(line().querySelector('[data-testid="work-group"]')).toBe(group);
  });
});

describe('durations never count a hidden tab', () => {
  it('a block that ended while nothing rendered for longer than the render gap shows no duration', () => {
    vi.useFakeTimers();
    const live = [sh('a', 'make'), ok('a'), sh('b', 'make test')];
    const { rerender } = render(view(groupOf(live, true)));
    act(() => { vi.advanceTimersByTime(5 * 60_000); }); // the tab was in the background
    rerender(view(groupOf([...live, ok('b'), tx('done')], false)));
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(screen.getByTestId('group-counts').textContent).not.toMatch(/\d+m\b|\d+s\b/);
  });
});
