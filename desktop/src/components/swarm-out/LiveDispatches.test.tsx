/**
 * LiveDispatches — the live part of the Swarm Out dashboard.
 *
 * Questions: are a Hive's open questions shown with their answer controls; does an
 * approval send exactly approve/deny; does a question's answer cover every question
 * before it can be sent; is Hive-written text shown as text (no HTML rendered).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LiveDispatches } from './LiveDispatches';
import { swarmOutService } from '../../services/swarmOut';

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), answer: vi.fn(async () => undefined) },
}));

const runs = vi.mocked(swarmOutService.runs);
const answer = vi.mocked(swarmOutService.answer);

const approval = {
  kind: 'approval' as const, hive: 'worker-1', hive_id: 'h1', session_id: 's1', request_id: 'r1',
  asked_at: '2026-10-27T02:00:00Z', command: 'rm -rf /tmp/x <img src=x onerror=alert(1)>', reason: 'recursive delete',
};
const question = {
  kind: 'question' as const, hive: 'worker-1', hive_id: 'h1', session_id: 's2', request_id: 'r2',
  asked_at: '2026-10-27T02:00:00Z',
  questions: [
    { question: 'Which DB?', header: 'DB', multiSelect: false, options: [{ label: 'sqlite', description: '' }] },
    { question: 'Which port?', header: 'Port', multiSelect: false, options: [] },
  ],
};

beforeEach(() => {
  runs.mockReset();
  answer.mockClear();
});

describe('LiveDispatches', () => {
  it('shows an approval and sends approve', async () => {
    runs.mockResolvedValue({ runs: [], asks: [approval], errors: [] });
    render(<LiveDispatches />);
    await screen.findByText('批准');
    expect(document.querySelector('img')).toBeNull();          // Hive text is not HTML
    expect(screen.getByText(/onerror=alert/)).toBeTruthy();
    fireEvent.click(screen.getByText('批准'));
    await waitFor(() => expect(answer).toHaveBeenCalledWith(approval, { decision: 'approve' }));
  });

  it('sends a question only when every question has an answer', async () => {
    runs.mockResolvedValue({ runs: [], asks: [question], errors: [] });
    render(<LiveDispatches />);
    const submit = await screen.findByText('提交回答');
    fireEvent.click(screen.getByText('sqlite'));
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('回答：Which port?'), { target: { value: '8080' } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(answer).toHaveBeenCalledWith(question, { answers: { 'Which DB?': 'sqlite', 'Which port?': '8080' } }));
  });

  it('lists dispatched runs and marks the one waiting', async () => {
    runs.mockResolvedValue({
      runs: [{ dispatch_id: 'd1', hive_id: 'h1', hive: 'worker-1', session_id: 's2', title: 'Fix login',
               origin: 'swarm-out', sent_at: '2026-10-27T02:00:00Z', waiting: true }],
      asks: [], errors: [],
    });
    render(<LiveDispatches />);
    expect(await screen.findByText('Fix login')).toBeTruthy();
    expect(screen.getByTestId('swarm-out-run').textContent).toContain('在等你');
  });
});
