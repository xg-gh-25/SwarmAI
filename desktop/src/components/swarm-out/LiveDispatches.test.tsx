/**
 * LiveDispatches — the live part of the Swarm Out dashboard.
 *
 * Questions: are a Hive's open questions shown with their answer controls; does an
 * approval send exactly approve/deny; does a question's answer cover every question
 * before it can be sent; is Hive-written text shown as text (no HTML rendered).
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LiveDispatches } from './LiveDispatches';
import { swarmOutService } from '../../services/swarmOut';
import i18n from '../../i18n';

// These cases check the Chinese copy (zh.json); swarmOutI18n.test.tsx checks English.
beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

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
               origin: 'swarm-out', sent_at: '2026-10-27T02:00:00Z', waiting: true, state: 'waiting', running: false }],
      asks: [], errors: [],
    });
    // the Hive says the run waits but no question reached this Host: it is still listed
    render(<LiveDispatches now={Date.parse('2026-10-27T03:00:00Z')} />);
    expect(await screen.findByText('Fix login')).toBeTruthy();
    expect(screen.getByTestId('swarm-out-run').textContent).toContain('在等你');
  });

  it('names the tool and the dispatch, and refuses to offer approve for a cut-off command (fixing spec B)', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...approval, tool_name: 'Bash', truncated: true, dispatch_title: 'Fix login' }], errors: [] });
    render(<LiveDispatches />);
    expect((await screen.findByTestId('swarm-out-ask-tool')).textContent).toBe('Bash');
    expect(screen.getByTestId('swarm-out-ask-source').textContent).toBe('Fix login');
    expect(screen.getByTestId('swarm-out-ask-truncated')).toBeTruthy();
    expect(screen.queryByText('批准')).toBeNull();
    expect(screen.getByText('拒绝')).toBeTruthy();
  });

  it('says when an ask is from a session this Host did not send', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...approval, dispatch_title: null }], errors: [] });
    render(<LiveDispatches />);
    expect((await screen.findByTestId('swarm-out-ask-source')).textContent).toContain('不是这里派出的');
  });

  it('a multi-select question takes several picks, joined like the chat does (J3)', async () => {
    const multi = { ...question, questions: [{ question: 'Which?', header: 'W', multiSelect: true,
      options: [{ label: 'a', description: '' }, { label: 'b', description: '' }] }] };
    runs.mockResolvedValue({ runs: [], asks: [multi], errors: [] });
    render(<LiveDispatches />);
    fireEvent.click(await screen.findByText('a'));
    fireEvent.click(screen.getByText('b'));
    fireEvent.click(screen.getByText('提交回答'));
    await waitFor(() => expect(answer).toHaveBeenCalledWith(multi, { answers: { 'Which?': 'a, b' } }));
  });

  it('says how many questions or options were not shown (D4)', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...question, omitted: { questions: 2, options: 1 } }], errors: [] });
    render(<LiveDispatches />);
    expect((await screen.findByTestId('swarm-out-ask-omitted')).textContent).toContain('3');
  });

  it('a refused answer is shown as not delivered, with no controls (fixing spec B)', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...approval, refused: true }], errors: [] });
    render(<LiveDispatches />);
    expect(await screen.findByTestId('swarm-out-ask-refused')).toBeTruthy();
    expect(screen.queryByText('批准')).toBeNull();
    expect(screen.queryByText('拒绝')).toBeNull();
  });

  it('a multi-select label with a comma stays one pick', async () => {
    const multi = { ...question, questions: [{ question: 'How?', header: 'H', multiSelect: true,
      options: [{ label: 'Yes, with tests', description: '' }, { label: 'Yes', description: '' }] }] };
    runs.mockResolvedValue({ runs: [], asks: [multi], errors: [] });
    render(<LiveDispatches />);
    const withTests = await screen.findByText('Yes, with tests');
    fireEvent.click(withTests);
    expect(withTests.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('Yes').getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(withTests);                       // deselects
    expect(withTests.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByText('Yes'));
    fireEvent.click(withTests);
    fireEvent.click(screen.getByText('提交回答'));
    // option order, not click order
    await waitFor(() => expect(answer).toHaveBeenCalledWith(multi, { answers: { 'How?': 'Yes, with tests, Yes' } }));
  });

  it('names a possible owner and marks a dispatch whose session was never named (J1)', async () => {
    runs.mockResolvedValue({
      runs: [{ dispatch_id: 'd9', hive_id: 'h1', hive: 'worker-1', session_id: null, title: 'Timed out',
               origin: 'swarm-out', sent_at: '2026-10-27T02:00:00Z', waiting: false, session_unknown: true }],
      asks: [{ ...approval, dispatch_title: null, possible_dispatch_title: 'Timed out' }], errors: [] });
    render(<LiveDispatches now={Date.parse('2026-10-27T03:00:00Z')} />);
    expect((await screen.findByTestId('swarm-out-ask-source')).textContent).toBe('可能属于：Timed out');
    expect(screen.getByTestId('swarm-out-run-unknown')).toBeTruthy();
  });
});
