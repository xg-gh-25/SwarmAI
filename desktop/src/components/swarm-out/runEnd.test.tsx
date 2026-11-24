/**
 * How a run ended and what still keeps it from settling, in the person's words.
 *
 * Questions:
 *  - does every end reason the backend records (core/swarm_out_settle END_REASONS) read as
 *    its own sentence in English and in Chinese, never a raw key or code;
 *  - does every why_not value of an unsettled run (verdict, code, files, lessons) read in
 *    English and in Chinese, and is the line left out while the run runs or once it settled;
 *  - do the board's done row and the run card's header carry both lines;
 *  - does a run whose session died with its Hive stopped read Stopped, with why, not Running.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { SwarmOutRun } from '../../services/swarmOut';
import { RunOverlay } from './RunOverlay';
import { DoneRow } from './RunBoard';
import { EndLine } from './runCard';
import { END_REASONS, WHY_NOT } from './stage';
import i18n from '../../i18n';

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    accept: vi.fn(), manualLanded: vi.fn(), rerun: vi.fn(), stop: vi.fn(), discard: vi.fn(),
    answerAsk: vi.fn(), result: vi.fn(() => new Promise(() => {})), answer: vi.fn(),
    swarmRun: vi.fn(async () => { throw new Error('not read'); }), returnPrepare: vi.fn(), returnLand: vi.fn(),
    refresh: vi.fn(), runs: vi.fn(), fleet: vi.fn(),
  },
}));

const NOW = Date.parse('2026-11-24T04:00:00Z');
const CJK = /[一-鿿]/;

function run(over: Partial<SwarmOutRun> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_0a1511c01151', hive_id: 'h1', hive: 'demo', session_id: 'hs1', title: 'Weekly report',
    origin: 'swarm-out', sent_at: '2026-11-24T02:00:00Z', state: 'stopped', state_reason: 'idle',
    result: null, result_status: 'fetched',
    project_agent: { brain: 'SwarmAI', mode: 'research', revision: 'r1', profile: '', acceptance: 'a report',
      returns: 'answer', execution: 'native' },
    stage: 'awaiting', actions: [], why: null,
    settle: { code: 'none', why_not: ['verdict', 'code', 'files', 'lessons'], settled: null },
    end: { reason: 'completed', at: '2026-11-24T03:00:00Z' },
    ...over,
  } as SwarmOutRun;
}

const ZH: Record<string, string> = { verdict: '等 agent 判定', files: '文件还没进 Brain', lessons: 'lessons 还没到' };

describe('in English', () => {
  beforeAll(async () => { await i18n.changeLanguage('en'); });

  it('every end reason reads as a sentence of its own', () => {
    const seen = new Set<string>();
    for (const reason of END_REASONS) {
      const { unmount } = render(<EndLine r={run({ end: { reason, at: '2026-11-24T03:00:00Z' } })} testId="t" />);
      const text = screen.getByTestId('t-end').textContent ?? '';
      expect(text).not.toMatch(/swarmOut\.|_/);
      expect(text).not.toMatch(CJK);
      seen.add(text);
      unmount();
    }
    expect(seen.size).toBe(END_REASONS.length);
  });

  it('every why_not value reads in words, and only on an unsettled run that is not running', () => {
    for (const w of WHY_NOT) {
      const { unmount } = render(<EndLine r={run({ settle: { code: 'none', why_not: [w], settled: null } })} testId="t" />);
      const text = screen.getByTestId('t-why-not').textContent ?? '';
      expect(text).not.toMatch(/swarmOut\.|why_not/);
      expect(text).not.toMatch(CJK);
      expect(text.length).toBeGreaterThan(8);
      unmount();
    }
    const { unmount } = render(<EndLine r={run({ stage: 'running', state: 'running', end: null })} testId="t" />);
    expect(screen.queryByTestId('t-why-not')).toBeNull();
    expect(screen.queryByTestId('t-end')).toBeNull();
    unmount();
    render(<EndLine r={run({ stage: 'accepted', settle: { code: 'none', why_not: [], settled: { at: 'x', outcome: 'passed', released: true } } })} testId="t" />);
    expect(screen.queryByTestId('t-why-not')).toBeNull();
  });

  it('the done row and the run card carry how it ended and what holds it', () => {
    const r = run({ stage: 'accepted', settle: { code: 'none', why_not: ['lessons'], settled: null } });
    const { unmount } = render(<DoneRow r={r} fleet={null} now={NOW} compact={false} onOpen={vi.fn()} />);
    expect(screen.getByTestId('so-done-end').textContent).toContain('completed');
    expect(screen.getByTestId('so-done-why-not').textContent).toMatch(/lessons/i);
    unmount();
    render(<RunOverlay runs={[r]} asks={[]} order={[r.dispatch_id]} id={r.dispatch_id} now={NOW}
      onSelect={vi.fn()} onSettled={vi.fn()} onClose={vi.fn()} onFileOpened={vi.fn()} onReload={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-review-end').textContent).toContain('completed');
    expect(screen.getByTestId('swarm-out-review-why-not').textContent).toMatch(/lessons/i);
  });

  it('a run whose session died with its Hive stopped reads stopped, not running', () => {
    const r = run({ stage: 'stopped', why: 'hive_stopped', state_reason: 'hive_stopped', result: null,
      result_status: 'pending', end: null, settle: { code: 'none', why_not: ['verdict'], settled: null } });
    render(<RunOverlay runs={[r]} asks={[]} order={[r.dispatch_id]} id={r.dispatch_id} now={NOW}
      onSelect={vi.fn()} onSettled={vi.fn()} onClose={vi.fn()} onFileOpened={vi.fn()} onReload={vi.fn()} />);
    const pill = screen.getByTestId('swarm-out-review-pill');
    expect(pill.dataset.stage).toBe('stopped');
    expect(pill.textContent).toContain('Stopped');
    expect(pill.textContent).not.toContain('Running');
    const stage = screen.getByTestId('swarm-out-review-stage').textContent ?? '';
    expect(stage).toMatch(/Hive/);
    expect(stage).not.toContain('hive_stopped');
  });
});

describe('in Chinese', () => {
  beforeAll(async () => { await i18n.changeLanguage('zh'); });
  afterAll(async () => { await i18n.changeLanguage('en'); });

  it('every end reason reads in Chinese', () => {
    for (const reason of END_REASONS) {
      const { unmount } = render(<EndLine r={run({ end: { reason, at: '2026-11-24T03:00:00Z' } })} testId="t" />);
      const text = screen.getByTestId('t-end').textContent ?? '';
      expect(text).toMatch(CJK);
      expect(text).not.toMatch(/swarmOut\.|_/);
      unmount();
    }
  });

  it('every why_not value reads in Chinese, in the words the person uses', () => {
    for (const w of WHY_NOT) {
      const { unmount } = render(<EndLine r={run({ settle: { code: 'none', why_not: [w], settled: null } })} testId="t" />);
      const text = screen.getByTestId('t-why-not').textContent ?? '';
      expect(text).toMatch(CJK);
      expect(text).not.toMatch(/swarmOut\.|why_not/);
      if (ZH[w]) expect(text).toContain(ZH[w]);
      unmount();
    }
  });

  it('a stopped-Hive run says so in Chinese', () => {
    const r = run({ stage: 'stopped', why: 'hive_stopped', state_reason: 'hive_stopped', result: null, end: null });
    render(<RunOverlay runs={[r]} asks={[]} order={[r.dispatch_id]} id={r.dispatch_id} now={NOW}
      onSelect={vi.fn()} onSettled={vi.fn()} onClose={vi.fn()} onFileOpened={vi.fn()} onReload={vi.fn()} />);
    expect(screen.getByTestId('swarm-out-review-stage').textContent).toMatch(/Hive.*停/);
  });
});
