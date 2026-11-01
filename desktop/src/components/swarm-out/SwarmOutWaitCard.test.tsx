/**
 * SwarmOutWaitCard — a Hive's wait drawn with the chat's own card (design
 * docs/design/2026-10-31-swarm-out-single-loop-fixes.md §2.1, §2.2).
 *
 * Questions: is every kind of wait the chat's ActFrame (ActOption for choices); does a
 * decision send only on Submit and turn into the "sent" line at once; does each phase
 * from the backend read as its own line (sent / taken / gone), and a refused one keep
 * its choice with the reason and no way to send again; is the backend's reason shown
 * when a submit fails.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { SwarmOutWaitCard } from './SwarmOutWaitCard';
import { swarmOutService, type SwarmOutAsk } from '../../services/swarmOut';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({ swarmOutService: { answer: vi.fn(async () => undefined) } }));
const answer = vi.mocked(swarmOutService.answer);
beforeEach(() => { answer.mockReset(); answer.mockResolvedValue(undefined); });

const decision: SwarmOutAsk = {
  kind: 'decision', hive: 'worker-1', hive_id: 'h1', session_id: 's1', request_id: 'dc-1',
  asked_at: '2026-10-31T08:19:44Z', run_id: 'run_8d2d6794', question: 'Which fix?', why: 'both fit',
  options: [
    { id: 'A', choice: 'One deadline', consequence: 'smallest change', reversible: true, risk: 'low' },
    { id: 'B', choice: 'Async dispatch', consequence: 'more parts', reversible: true, risk: 'high' },
  ],
};

describe('SwarmOutWaitCard', () => {
  it.each([
    ['decision', decision],
    ['approval', { ...decision, kind: 'approval' as const, request_id: 'r1', command: 'ls', options: undefined }],
    ['question', { ...decision, kind: 'question' as const, request_id: 'r2', options: undefined,
      questions: [{ question: 'Which db?', header: 'DB', multiSelect: false, options: [{ label: 'sqlite', description: '' }] }] }],
  ])('a waiting %s is the chat\'s own card', (_k, wait) => {
    const { container } = render(<SwarmOutWaitCard wait={wait} onAnswered={vi.fn()} />);
    expect(container.querySelector('[data-act-frame]')).not.toBeNull();
  });

  it('a decision is sent only on Submit, and is the sent line at once', async () => {
    const reload = vi.fn();
    render(<SwarmOutWaitCard wait={decision} onAnswered={reload} />);
    const submit = screen.getByTestId('swarm-out-wait-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(screen.getByText('A — One deadline'));
    expect(screen.getByText('A — One deadline').closest('button')!.getAttribute('data-selected')).toBe('true');
    expect(answer).not.toHaveBeenCalled();
    fireEvent.keyDown(submit, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(answer).toHaveBeenCalledWith(decision, { option_id: 'A' }));
    const line = await screen.findByTestId('swarm-out-wait-done');
    expect(line.textContent).toContain('A — One deadline');
    expect(screen.getByTestId('swarm-out-wait-phase').textContent).toContain('waiting for the Hive');
    expect(screen.queryByTestId('swarm-out-wait-submit')).toBeNull();     // no second click
    expect(reload).toHaveBeenCalled();
  });

  it('a failed submit shows the backend\'s reason and keeps the choice', async () => {
    answer.mockRejectedValue(new Error('this question was already answered'));
    render(<SwarmOutWaitCard wait={decision} onAnswered={vi.fn()} />);
    fireEvent.click(screen.getByText('B — Async dispatch'));
    fireEvent.click(screen.getByTestId('swarm-out-wait-submit'));
    expect((await screen.findByTestId('swarm-out-wait-error')).textContent).toContain('this question was already answered');
    expect(screen.getByText('B — Async dispatch').closest('button')!.getAttribute('data-selected')).toBe('true');
  });

  it.each([
    ['sent', 'waiting for the Hive'],
    ['taken', 'Hive received'],
    ['gone', 'settled somewhere else'],
  ])('a %s wait is one line with what was chosen', (phase, tail) => {
    render(<SwarmOutWaitCard wait={{ ...decision, phase: phase as SwarmOutAsk['phase'], chosen: 'A — One deadline',
      question_text: 'Which fix?' }} onAnswered={vi.fn()} />);
    const line = screen.getByTestId('swarm-out-wait-done');
    expect(line.textContent).toContain('Which fix?');
    expect(line.textContent).toContain('A — One deadline');
    expect(screen.getByTestId('swarm-out-wait-phase').textContent).toContain(tail);
  });

  it('a refused wait keeps its choice, says why by code, and offers only the session', () => {
    const open = vi.fn();
    render(<SwarmOutWaitCard wait={{ ...decision, phase: 'refused', refused: true, refused_reason: 'run_refused', chosen: 'B — Async dispatch' }}
      onAnswered={vi.fn()} onOpenSession={open} />);
    expect(screen.getByTestId('swarm-out-ask-refused').textContent).toContain('the run already moved on');
    expect(screen.getByText(/B — Async dispatch/)).toBeTruthy();
    expect(screen.queryByTestId('swarm-out-wait-submit')).toBeNull();
    fireEvent.click(screen.getByTestId('swarm-out-wait-open'));
    expect(open).toHaveBeenCalled();
  });

  it('an approval\'s choice reads as words, not a code', () => {
    render(<SwarmOutWaitCard wait={{ ...decision, kind: 'approval', phase: 'taken', chosen: 'deny', question_text: 'rm -rf x' }}
      onAnswered={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-wait-done').textContent).toContain('denied');
  });

  it('the run view has no answer controls of its own', () => {
    const src = readFileSync(resolve(__dirname, 'LiveDispatches.tsx'), 'utf8');
    expect(src).not.toMatch(/function AskCard/);
    expect(src).toMatch(/SwarmOutWaitCard/);
  });
});

describe('SwarmOutWaitCard — who answers (design 2026-10-31-hive-approvals)', () => {
  it('a wait the dispatching agent answers says so, and the person can still answer it', () => {
    render(<SwarmOutWaitCard wait={{ ...decision, approval_class: 'decision' }} onAnswered={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-wait-agent').textContent).toContain('agent that dispatched');
    expect(screen.getByTestId('swarm-out-wait-submit')).toBeTruthy();
  });

  it.each([['off_machine'], [null]])('a wait of class %s is the person\'s: no agent line', (cls) => {
    render(<SwarmOutWaitCard wait={{ ...decision, approval_class: cls }} onAnswered={vi.fn()} />);
    expect(screen.queryByTestId('swarm-out-wait-agent')).toBeNull();
  });

  it('a wait the agent answered reads as the agent\'s answer', () => {
    render(<SwarmOutWaitCard wait={{ ...decision, phase: 'taken', chosen: 'A — One deadline', answered_by: 'host' }} onAnswered={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-wait-done').textContent).toContain('Answered by the agent');
  });
});
