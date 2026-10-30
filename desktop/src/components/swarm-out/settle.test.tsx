/**
 * Accepting a dispatched task on its card (SettleBar; backend core/swarm_out_settle,
 * design docs/design/2026-10-30-dispatch-center.md §4-§6).
 *
 * Questions: is a run to accept listed under 等你 only, and counted there; does pass /
 * fail send the person's verdict on the result the card shows (its ended_at), fail only
 * with a sentence; does a commits task offer no second accept button, only landing in
 * the details or "landed by hand"; does giving up ask twice; does a settled run show
 * nothing to decide; does the 3-day reminder show.
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SettleBar } from './LiveDispatches';
import { runGroups } from './live';
import { activityOf } from './useSwarmOutActivity';
import { swarmOutService, type SwarmOutRun } from '../../services/swarmOut';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('zh'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { verdict: vi.fn(async () => undefined), manualLanded: vi.fn(async () => undefined),
    abandon: vi.fn(async () => undefined) },
}));

const verdict = vi.mocked(swarmOutService.verdict);
const manual = vi.mocked(swarmOutService.manualLanded);
const abandon = vi.mocked(swarmOutService.abandon);

const ENDED = '2026-10-30T10:00:00Z';
const NOW = Date.parse('2026-10-30T12:00:00Z');

function run(over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_aaaaaaaaaaaa', hive_id: 'h1', hive: 'explorer', session_id: 's1', title: 'Dive deep',
    origin: 'swarm-out', sent_at: '2026-10-01T10:00:00Z', waiting: false, running: false, state: 'stopped',
    project_agent: { brain: 'SwarmAI', mode: 'dive', revision: 'r1', profile: '', delivery: 'document',
      acceptance: 'A report naming the five riskiest changes', returns: 'document' },
    result: { ended: 'completed', provisional: false, ended_at: ENDED } as SwarmOutRun['result'],
    next: 'accept', settle: { verdict: null, code: 'none', why_not: ['verdict'], settled: null },
    ...over,
  };
}

beforeEach(() => { verdict.mockClear(); manual.mockClear(); abandon.mockClear(); });

describe('runs to accept', () => {
  it('are under 等你 only, whatever their age, and counted there', () => {
    const toAccept = run();
    const settled = run({ dispatch_id: 'so_bbbbbbbbbbbb', sent_at: '2026-10-30T11:00:00Z', next: null,
      settle: { verdict: 'pass', code: 'none', why_not: [], settled: { at: ENDED, outcome: 'passed', released: true } } });
    const plain = run({ dispatch_id: 'so_cccccccccccc', sent_at: '2026-10-30T11:00:00Z', next: null, settle: undefined });
    const asking = run({ dispatch_id: 'so_dddddddddddd', state: 'waiting', waiting: true, next: 'answer', cleanup_soon: true });
    const g = runGroups([toAccept, settled, plain, asking], NOW);
    expect(g.toSettle.map((r) => r.dispatch_id)).toEqual(['so_aaaaaaaaaaaa']);
    expect(g.stopped.map((r) => r.dispatch_id)).toEqual(['so_cccccccccccc']);   // settled: history's
    // the waiting run is counted once, by its unasked question (also when its records go
    // soon); the run to accept by toSettle
    expect(activityOf({ runs: [toAccept, settled, plain, asking], asks: [], errors: [] }).waiting).toBe(2);
  });
});

describe('SettleBar', () => {
  it('passes on the result the card shows', async () => {
    render(<SettleBar r={run()} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle-acceptance').textContent).toContain('five riskiest');
    fireEvent.click(screen.getByText('通过'));
    await waitFor(() => expect(verdict).toHaveBeenCalledWith('so_aaaaaaaaaaaa', 'pass', '', ENDED));
  });

  it('fails only with a sentence', async () => {
    render(<SettleBar r={run()} onShowCode={() => {}} />);
    fireEvent.click(screen.getByText('不通过'));
    const confirm = screen.getByTestId('swarm-out-settle-fail-confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/哪里不行/), { target: { value: 'misses the numbers' } });
    fireEvent.click(confirm);
    await waitFor(() => expect(verdict).toHaveBeenCalledWith('so_aaaaaaaaaaaa', 'fail', 'misses the numbers', ENDED));
  });

  it('a commits task has no accept button: land it in the details, or landed by hand', async () => {
    const show = vi.fn();
    const r = run({ next: 'review_code', project_agent: { ...run().project_agent!, returns: 'commits', delivery: 'bundle' },
      settle: { verdict: null, code: 'waiting', why_not: ['verdict', 'code'], settled: null } });
    render(<SettleBar r={r} onShowCode={show} />);
    expect(screen.queryByTestId('swarm-out-settle-pass')).toBeNull();
    expect(screen.queryByTestId('swarm-out-settle-fail')).toBeNull();
    fireEvent.click(screen.getByText('看代码'));
    expect(show).toHaveBeenCalled();
    fireEvent.click(screen.getByText('我已手工落地'));
    await waitFor(() => expect(manual).toHaveBeenCalledWith('so_aaaaaaaaaaaa'));
  });

  it('after a fail offers no new verdict, only giving up, and asks twice', async () => {
    render(<SettleBar r={run({ settle: { verdict: 'fail', code: 'none', why_not: ['verdict'], settled: null } })} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle').getAttribute('data-ask')).toBe('failed');
    expect(screen.queryByTestId('swarm-out-settle-pass')).toBeNull();
    fireEvent.click(screen.getByText('放弃'));
    expect(abandon).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(/确认放弃/));
    await waitFor(() => expect(abandon).toHaveBeenCalledWith('so_aaaaaaaaaaaa'));
  });

  it('unsealed commits: land by hand, or give up knowing they are deleted', async () => {
    const r = run({ next: 'review_code', project_agent: { ...run().project_agent!, returns: 'commits' },
      settle: { verdict: null, code: 'unsealed', why_not: ['verdict', 'code'], settled: null } });
    render(<SettleBar r={r} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle').getAttribute('data-ask')).toBe('unsealed');
    expect(screen.getByText('我已手工落地')).toBeTruthy();
    fireEvent.click(screen.getByText('放弃'));
    expect(screen.getByText(/没封进 bundle 的提交会一起删掉/)).toBeTruthy();
  });

  it('a stopped run with no result can be given up', () => {
    render(<SettleBar r={run({ result: null })} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle').getAttribute('data-ask')).toBe('noResult');
    expect(screen.queryByTestId('swarm-out-settle-pass')).toBeNull();
    expect(screen.getByTestId('swarm-out-settle-abandon')).toBeTruthy();
  });

  it('a file that never came: wait, or give up', () => {
    render(<SettleBar r={run({ settle: { verdict: 'pass', code: 'none', why_not: ['files'], settled: null } })} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle').getAttribute('data-ask')).toBe('filesMissing');
    expect(screen.queryByTestId('swarm-out-settle-pass')).toBeNull();
    expect(screen.getByTestId('swarm-out-settle-abandon')).toBeTruthy();
  });

  it('a reminder alone still offers giving up', () => {
    render(<SettleBar r={run({ next: 'running', cleanup_soon: true })} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle-abandon')).toBeTruthy();
  });

  it('shows nothing once settled, and the reminder before records go', () => {
    const { container } = render(<SettleBar r={run({ next: null,
      settle: { verdict: 'pass', code: 'none', why_not: [], settled: { at: ENDED, outcome: 'passed', released: false } } })}
      onShowCode={() => {}} />);
    expect(container.textContent).toBe('');
    render(<SettleBar r={run({ cleanup_soon: true })} onShowCode={() => {}} />);
    expect(screen.getByTestId('swarm-out-settle-cleanup').textContent).toContain('3 天后清理');
  });
});
