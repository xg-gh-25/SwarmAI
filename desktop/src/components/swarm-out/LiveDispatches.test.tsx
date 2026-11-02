/**
 * LiveDispatches — the live part of the Swarm Out dashboard.
 *
 * Questions: are a Hive's open questions shown with their answer controls (the chat's
 * own card, SwarmOutWaitCard); does an approval send exactly approve/deny; does a
 * question's answer cover every question before it can be sent; is a decision sent
 * only on Submit; is Hive-written text shown as text (no HTML rendered).
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
    const submit = (await screen.findByText('提交全部回答')).closest('button')!;
    fireEvent.click(screen.getByText('sqlite'));
    expect(submit.disabled).toBe(true);
    // a question with no options is answered in the chat card's own words field
    fireEvent.click(screen.getAllByText('其他（自己填写）')[1]);
    fireEvent.change(screen.getByPlaceholderText('输入你自己的回答…'), { target: { value: '8080' } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => expect(answer).toHaveBeenCalledWith(question, { answers: { 'Which DB?': 'sqlite', 'Which port?': '8080' } }));
  });

  it('lists dispatched runs and marks the one waiting', async () => {
    runs.mockResolvedValue({
      runs: [{ dispatch_id: 'd1', hive_id: 'h1', hive: 'worker-1', session_id: 's2', title: 'Fix login',
               origin: 'swarm-out', sent_at: '2026-10-27T02:00:00Z', state: 'waiting', state_reason: 'question' }],
      asks: [], errors: [],
    });
    // the Hive says the run waits but no question reached this Host: it is still listed
    render(<LiveDispatches now={Date.parse('2026-10-27T03:00:00Z')} />);
    const row = (await screen.findAllByTestId('swarm-out-row'))[0];
    expect(row.textContent).toContain('Fix login');
    expect(row.getAttribute('data-stage')).toBe('needs_answer');
    expect(screen.getByTestId('swarm-out-waiting-unasked').textContent).toContain('Fix login');   // the Hive page's
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
    // sent: the card is one line with what was chosen, and cannot be sent again
    expect((await screen.findByTestId('swarm-out-wait-done')).textContent).toContain('a, b');
    expect(screen.queryByText('提交回答')).toBeNull();
  });

  it('says how many questions or options were not shown (D4)', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...question, omitted: { questions: 2, options: 1 } }], errors: [] });
    render(<LiveDispatches />);
    expect((await screen.findByTestId('swarm-out-ask-omitted')).textContent).toContain('3');
  });

  it('a refused answer is shown as not used, with its reason and no controls (fixing spec B)', async () => {
    runs.mockResolvedValue({ runs: [], asks: [{ ...approval, refused: true, phase: 'refused' as const,
      refused_reason: 'changed' as const, chosen: 'approve' }], errors: [] });
    render(<LiveDispatches />);
    expect((await screen.findByTestId('swarm-out-ask-refused')).textContent).toContain('你看到以后它在 Hive 上变了');
    expect(screen.queryByText('批准')).toBeNull();
    expect(screen.queryByText('拒绝')).toBeNull();
  });

  it('a multi-select label with a comma stays one pick', async () => {
    const multi = { ...question, questions: [{ question: 'How?', header: 'H', multiSelect: true,
      options: [{ label: 'Yes, with tests', description: '' }, { label: 'Yes', description: '' }] }] };
    runs.mockResolvedValue({ runs: [], asks: [multi], errors: [] });
    render(<LiveDispatches />);
    const box = (label: string) => screen.getByText(label).closest('button')!;
    await screen.findByText('Yes, with tests');
    fireEvent.click(box('Yes, with tests'));
    expect(box('Yes, with tests').getAttribute('data-selected')).toBe('true');
    expect(box('Yes').getAttribute('data-selected')).toBe('false');
    fireEvent.click(box('Yes, with tests'));          // deselects
    expect(box('Yes, with tests').getAttribute('data-selected')).toBe('false');
    fireEvent.click(box('Yes'));
    fireEvent.click(box('Yes, with tests'));
    fireEvent.click(screen.getByText('提交回答'));
    // the picks as the chat's own card joins them (in the order picked)
    await waitFor(() => expect(answer).toHaveBeenCalledWith(multi, { answers: { 'How?': 'Yes, Yes, with tests' } }));
  });

  it('names a possible owner and marks a dispatch whose session was never named (J1)', async () => {
    runs.mockResolvedValue({
      runs: [{ dispatch_id: 'd9', hive_id: 'h1', hive: 'worker-1', session_id: null, title: 'Timed out',
               origin: 'swarm-out', sent_at: '2026-10-27T02:00:00Z', state: 'unknown', state_reason: 'no_session_id', session_unknown: true }],
      asks: [{ ...approval, dispatch_title: null, possible_dispatch_title: 'Timed out' }], errors: [] });
    render(<LiveDispatches now={Date.parse('2026-10-27T03:00:00Z')} />);
    expect((await screen.findByTestId('swarm-out-ask-source')).textContent).toBe('可能属于：Timed out');
    // an older dispatch, never named: kept in view (recently done is open)
    expect(screen.getAllByTestId('swarm-out-row').map((r) => r.getAttribute('data-id'))).toEqual(['d9']);
  });

  it('a pipeline decision is answered with one of its options, nothing typed', async () => {
    const decision = {
      kind: 'decision' as const, hive: 'worker-1', hive_id: 'h1', session_id: 's3', request_id: 'dc-1',
      asked_at: '2026-10-27T02:00:00Z', run_id: 'run_a', question: 'Which storage?', why: 'both fit',
      options: [
        { id: 'a', choice: 'files', consequence: 'simple', reversible: true, risk: 'low' as const },
        { id: 'b', choice: 'sqlite', consequence: 'migration', reversible: false, risk: 'high' as const },
      ],
    };
    runs.mockResolvedValue({ runs: [], asks: [decision], errors: [] });
    render(<LiveDispatches />);
    await screen.findByText('一个 pipeline run 需要你拍板');
    expect(screen.getByText('它自己定不了的原因：both fit')).toBeTruthy();
    expect(screen.getByText('不可撤回')).toBeTruthy();
    expect(screen.getByText('高风险')).toBeTruthy();
    expect(screen.getByTestId('swarm-out-ask').querySelector('input')).toBeNull();
    const submit = screen.getByTestId('swarm-out-wait-submit') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);                  // nothing picked yet
    fireEvent.click(screen.getByText('b — sqlite'));
    expect(answer).not.toHaveBeenCalled();               // a pick alone sends nothing
    fireEvent.click(screen.getByText('a — files'));      // and can be changed
    fireEvent.click(submit);
    await waitFor(() => expect(answer).toHaveBeenCalledWith(decision, { option_id: 'a' }));
    expect((await screen.findByTestId('swarm-out-wait-done')).textContent).toContain('a — files');
  });

  it('a pipeline decision too long to show whole offers no options', async () => {
    const decision = {
      kind: 'decision' as const, hive: 'worker-1', hive_id: 'h1', session_id: 's3', request_id: 'dc-2',
      asked_at: '2026-10-27T02:00:00Z', run_id: 'run_a', question: 'Which storage?', why: '', truncated: true,
      options: [
        { id: 'a', choice: 'files', consequence: '', reversible: true, risk: 'low' as const },
        { id: 'b', choice: 'sqlite', consequence: '', reversible: true, risk: 'low' as const },
      ],
    };
    runs.mockResolvedValue({ runs: [], asks: [decision], errors: [] });
    render(<LiveDispatches />);
    await screen.findByTestId('swarm-out-ask-truncated');
    expect(screen.queryByTestId('swarm-out-ask-decision')).toBeNull();
    expect(screen.queryByText('a — files')).toBeNull();
  });
});
