/**
 * The cards that wait on the user share one frame (design
 * docs/design/2026-10-26-turn-cards-unified.md §1 D, acceptance test 4).
 *
 * What is tested (ContentBlockRenderer with each block, as the chat renders it):
 * - AskUserQuestion, the permission request and the escalation, while waiting: the
 *   one ActFrame (`data-act-frame`, a primary left bar), a header that names Swarm
 *   (never Claude), buttons in the footer on the right; a pending permission keeps
 *   its timeout in the header meta; a recommended escalation option says so
 * - once answered each is ONE grey line (`data-act-done`, no frame) that still carries
 *   the decision: each question with its choice (multi-select and custom answers),
 *   the command with Approved / Denied / Timed out, the escalation's resolution
 * - History (read-only): an undecided permission shows no buttons
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { ContentBlockRenderer } from '../ContentBlockRenderer';
import type { ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));

const ASK = (answers?: Record<string, string>): ContentBlock => ({
  type: 'ask_user_question', toolUseId: 'tu-1',
  questions: [
    { question: 'Show the buttons?', header: 'Buttons', multiSelect: false,
      options: [{ label: 'Always dim', description: 'seen, brighter on hover' }, { label: 'On hover', description: 'cleaner' }] },
    { question: 'Pick services', header: 'Services', multiSelect: true,
      options: [{ label: 'EC2', description: '' }, { label: 'S3', description: '' }] },
  ],
  ...(answers ? { answers } : {}),
}) as ContentBlock;
const PERM = (decision?: string): ContentBlock => ({
  type: 'cmd_permission_request', requestId: 'p1', toolName: 'Bash',
  toolInput: { command: 'git push origin main' }, reason: 'push to a protected branch',
  ...(decision ? { decision } : {}),
}) as ContentBlock;
const ESC = (status: 'pending' | 'resolved'): ContentBlock => ({
  type: 'escalation', id: 'e1', severity: 'high', reason: 'The migration rewrites 1.2M rows',
  options: [{ label: 'Run it now', description: 'about 4 min', recommended: true }, { label: 'Skip it', description: '' }],
  status, ...(status === 'resolved' ? { resolution: 'Run it now' } : {}),
}) as unknown as ContentBlock;

const show = (block: ContentBlock, extra: Record<string, unknown> = {}) => render(
  <ContentBlockRenderer block={block} isStreaming={false} onAnswerQuestion={vi.fn()} onPermissionDecision={vi.fn()}
    onEscalationSelect={vi.fn()} {...extra} />,
);

describe('one frame for every card that waits on the user (test 4)', () => {
  it.each([
    ['question', () => show(ASK(), { pendingToolUseId: 'tu-1' }), 'Swarm needs your decision'],
    ['permission', () => show(PERM(), { pendingPermissionRequestId: 'p1' }), 'Swarm needs your approval'],
    ['escalation', () => show(ESC('pending')), 'Swarm needs your call'],
  ])('%s: the ActFrame, a Swarm header, buttons on the right', (_n, draw, title) => {
    const { container } = draw();
    const frame = container.querySelector('[data-act-frame]') as HTMLElement;
    expect(frame).toBeTruthy();
    expect(frame.className).toContain('border-l-primary');
    expect(within(frame).getByTestId('act-title').textContent).toContain(title);
    expect(container.textContent).not.toMatch(/Claude/);
  });

  it('the question footer holds Submit on the right; the permission footer Deny and Approve', () => {
    const { container } = show(ASK(), { pendingToolUseId: 'tu-1' });
    const footer = within(container.querySelector('[data-act-frame]') as HTMLElement).getByTestId('act-footer');
    expect(footer.className).toContain('justify-end');
    expect(within(footer).getByText(/Submit/)).toBeTruthy();
    const perm = show(PERM(), { pendingPermissionRequestId: 'p1' });
    const pf = within(perm.container.querySelector('[data-act-frame]') as HTMLElement);
    expect(within(pf.getByTestId('act-footer')).getByText('Deny')).toBeTruthy();
    expect(within(pf.getByTestId('act-footer')).getByText('Approve')).toBeTruthy();
    expect(pf.getByTestId('act-meta').textContent).toMatch(/4h/);
    expect(pf.getByText('git push origin main')).toBeTruthy();
  });

  it('a recommended escalation option says so', () => {
    const { container } = show(ESC('pending'));
    const opt = within(container).getByText('Run it now').closest('button')!;
    expect(opt.textContent).toContain('Recommended');
  });
});

describe('answered: one grey line that keeps the decision (test 4)', () => {
  it('a question: every question with its choice, multi-select and custom answers too', () => {
    const { container } = show(ASK({ 'Show the buttons?': 'make them bigger', 'Pick services': 'EC2, S3' }));
    expect(container.querySelector('[data-act-frame]')).toBeNull();
    const lines = container.querySelectorAll('[data-act-done]');
    expect(lines).toHaveLength(2);
    expect(lines[0].textContent).toContain('Show the buttons?');
    expect(lines[0].textContent).toContain('make them bigger');
    expect(lines[1].textContent).toContain('Pick services');
    expect(within(lines[1] as HTMLElement).getByText('EC2')).toBeTruthy();
    expect(within(lines[1] as HTMLElement).getByText('S3')).toBeTruthy();
    expect(container.textContent).not.toMatch(/Submit/);
  });

  it.each([
    ['approve', 'Approved'],
    ['deny', 'Denied'],
  ])('a permission %s: the command and the decision', (decision, word) => {
    const { container } = show(PERM(decision));
    expect(container.querySelector('[data-act-frame]')).toBeNull();
    const line = container.querySelector('[data-act-done]')!;
    expect(line.textContent).toContain(word);
    expect(line.textContent).toContain('git push origin main');
  });

  it('a permission no hook waits on any more, and one in History, show no buttons', () => {
    const gone = show(PERM('expired'));
    expect(gone.container.querySelector('[data-act-done]')!.textContent).toContain('No longer pending');
    const hist = show(PERM(), { readOnly: true });
    expect(hist.container.querySelector('[data-act-frame]')).toBeNull();
    expect(within(hist.container).queryByText('Approve')).toBeNull();
  });

  it('an escalation: what was asked and how it was settled', () => {
    const { container } = show(ESC('resolved'));
    expect(container.querySelector('[data-act-frame]')).toBeNull();
    const line = container.querySelector('[data-act-done]')!;
    expect(line.textContent).toContain('The migration rewrites 1.2M rows');
    expect(line.textContent).toContain('Run it now');
  });
});
