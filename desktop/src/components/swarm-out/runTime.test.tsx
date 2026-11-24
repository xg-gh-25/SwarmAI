/**
 * What a run's minutes say on the run board (RunBoard.StartAndTime), on the run card's
 * SENT station (runCard.DispatchHero) and in the history (HistoryView), in English and
 * Chinese.
 *
 * Questions: does a running card say "Running N min"; does a card whose result is back
 * say the work and whom it waits on since the result became final; does a settled row say
 * the work, its rounds and "at least" when a turn had no duration; does the SENT station
 * say the dispatch steps came before delivery; is the history median named as Hive work.
 */
process.env.TZ = 'UTC';
import { describe, it, expect, afterAll } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { SwarmOutRun } from '../../services/swarmOut';
import { StartAndTime } from './RunBoard';
import { DispatchHero } from './runCard';
import i18n from '../../i18n';

afterAll(async () => { await i18n.changeLanguage('en'); });

const NOW = Date.parse('2026-11-23T08:20:30Z');
const res = (over: Record<string, unknown> = {}) => ({ ended: 'completed', provisional: false, ended_at: '2026-11-23T06:20:00Z',
  final_at: '2026-11-23T06:20:30Z', turns: 1, active_s: 18 * 60, active_partial: false, turn_spans: [],
  work: { steps: { total: 0 } }, ...over }) as never;
const run = (over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: 'so_1', hive_id: 'h', hive: 'demo', session_id: 's', title: 't', origin: 'swarm-out',
  sent_at: '2026-11-23T08:08:00Z', state: 'running', state_reason: 'streaming', ...over,
});
const STATES: Record<string, SwarmOutRun> = {
  running: run({ stage: 'running' }),
  agent: run({ stage: 'awaiting', state: 'stopped', needs_person: false, result: res({ final_at: '2026-11-23T07:20:00Z' }) }),
  you: run({ stage: 'awaiting', state: 'stopped', needs_person: true, result: res({ active_s: 600 }) }),
  done: run({ stage: 'accepted', state: 'stopped', result: res({ turns: 2, active_s: 90 }) }),
  partial: run({ stage: 'accepted', state: 'stopped', result: res({ active_partial: true }) }),
};
const timeOf = (r: SwarmOutRun) => {
  const { unmount } = render(<StartAndTime r={r} now={NOW} />);
  const el = screen.getByTestId('so-time');
  const out = { phase: el.dataset.phase, text: el.textContent };
  unmount();
  return out;
};

describe.each([
  ['en', { running: 'Running 12 min', agent: 'Worked 18 min · waiting on agent 1 h', you: 'Worked 10 min · waiting on you 2 h',
    done: 'Worked 2 min · 2 rounds', partial: 'Worked ≥ 18 min', sent: '11/23 08:08 delivered · dispatch took 15s', median: 'Hive work median' }],
  ['zh', { running: '运行 12 min', agent: '干了 18 min · 等 agent 1 h', you: '干了 10 min · 等你 2 h',
    done: '干了 2 min · 2 轮', partial: '干了 ≥ 18 min', sent: '11/23 08:08 送达 · 派发用了 15 秒', median: 'Hive 工作中位' }],
] as const)('run time in %s', (lang, want) => {
  it('each state says what its number measures', async () => {
    await i18n.changeLanguage(lang);
    expect(timeOf(STATES.running)).toEqual({ phase: 'running', text: want.running });
    expect(timeOf(STATES.agent)).toEqual({ phase: 'waiting', text: want.agent });
    expect(timeOf(STATES.you)).toEqual({ phase: 'waiting', text: want.you });
    expect(timeOf(STATES.done)).toEqual({ phase: 'done', text: want.done });
    expect(timeOf(STATES.partial)).toEqual({ phase: 'done', text: want.partial });
  });

  it('the SENT station says the dispatch steps came before delivery; the median is Hive work', async () => {
    await i18n.changeLanguage(lang);
    render(<DispatchHero r={run({ timings: [{ step: 'pick', seconds: 5 }, { step: 'sync', seconds: 10 }] })} now={NOW} />);
    const sent = within(screen.getByTestId('swarm-out-card-track')).getAllByTestId('swarm-out-card-station')[0];
    expect(sent.textContent).toContain(want.sent.replace('11/23 ', ''));   // today: the time alone
    expect(i18n.t('swarmOut.hist.col.median')).toBe(want.median);
    expect(i18n.t('swarmOut.hist.facts', { count: 3, min: '8 min', cost: '$1' })).toContain(`${want.median} 8 min`);
  });
});
