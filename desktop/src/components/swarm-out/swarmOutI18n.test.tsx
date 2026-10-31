/**
 * Swarm Out speaks the app's language, and its colours say one thing each.
 *
 * Questions:
 *  - In English, does every part of the panel (run view with every kind of question,
 *    Hive, run state and result; the details; the history; the setup guide; the pure
 *    labels for every branch) render without a single Chinese character, attributes
 *    (title, aria-label, placeholder) included? Fixture data is English, so any CJK
 *    is copy that did not go through the locale files.
 *  - Does a language switch reach a panel already on screen?
 *  - Do the status colours follow ./tone: a failed run red, a stopped one amber, a
 *    done one green, a running one blue; a Hive's memory bar green, amber when it is
 *    saturated; the ready-Hive number green with room and amber when every Hive is full?
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutFleet, type SwarmOutResult, type SwarmOutRun, type SwarmOutRuns } from '../../services/swarmOut';
import { SwarmOutDashboard } from './SwarmOutDashboard';
import { HiveGuide } from './HiveGuide';
import { judgeSummary } from './judge';
import { ENDINGS, endedLabel, gateLine, HIVE_STATUSES, hiveStatusLabel, lessonsLine, resultErrorLabel, runStats, stateLabel, workLines } from './live';
import { hiveTone, runTone } from './tone';
import i18n from '../../i18n';

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: { runs: vi.fn(), fleet: vi.fn(), answer: vi.fn(), hives: vi.fn(), result: vi.fn(), refresh: vi.fn() },
}));

beforeAll(async () => { await i18n.changeLanguage('en'); });

const CJK = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/;
const cjkIn = (s: string): string[] => (s.match(new RegExp(`.{0,20}${CJK.source}+.{0,20}`, 'g')) ?? []);

const NOW = new Date(2026, 9, 28, 12, 0).getTime();
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString();

const result = (over: Partial<SwarmOutResult> = {}, work: Partial<SwarmOutResult['work']> = {}): SwarmOutResult => ({
  ended: 'completed', provisional: false, outcome_recorded: true, hive_records_outcomes: true, error: null, ended_at: ago(0.5), turns: 1,
  older_turns_not_read: false, active_s: 300, tokens: { input: 10, output: 5, cache_read: 900, cache_write: 85 }, cost_usd: 1.234, cost_partial: true,
  report_chars: 12, kinds: ['answer'], pipeline: null, dropped: 0, ...over,
  work: { repos: null, changes: [], files: [], files_omitted: 0,
    steps: { total: 3, failed: 1, agents: 0, by_tool: { Bash: 2, Read: 1 } },
    human: { approved: 1, denied: 0, unanswered: 1, answered_questions: 1 }, ...work },
});

const repo = (over = {}) => ({
  url: 'https://github.com/acme/widget', ref: 'main', dir: 'Hive/repos/github.com/acme/widget', present: true,
  origin_matches: true, base: 'a'.repeat(40), head: 'b'.repeat(40), commits: ['b'.repeat(40)], commits_total: 3,
  files: [{ path: 'src/x.py', added: 120, deleted: 30 }], files_total: 4, added: 120, deleted: 30, untracked: 2,
  pushed: 'no' as const, error: null, ...over,
});

const LESSONS = { returned: 3, sent: 3, written: 1, rejected: 1, pending: 1, skipped: 1, refused: 1, truncated: false, error: null };

/** A result that walks every branch of the card's lines. */
const RICH = result({
  ended: 'error', error: { code: 'MODEL_ERROR' }, kinds: ['pipeline', 'code', 'files'],
  pipeline: { run_id: 'r1', profile: 'full', status: 'completed', landing: 'landed', landed_sha: '7d34ab1',
    audit: [{ dim: 'Quality', verdict: 'WARN', detail: '1 finding' }, { dim: 'Speed', verdict: 'N/A', detail: null }] },
  project_agent: { brain: 'widget', mode: 'build', profile: 'full', delivery: 'bundle' },
  checks: [{ id: 'tests', status: 'ran' }, { id: 'lint', status: 'not_seen' }, { id: 'types', status: 'errored' }, { id: 'e2e', status: 'unknown' }],
  checks_partial: true,
  gate_refusals: { no_git_push: 2, integrity: 1, 'error:scope': 1 },
  lessons: LESSONS,
}, {
  repos: [repo(), repo({ dir: 'Hive/repos/a/gone', present: false }), repo({ dir: 'Hive/repos/a/other', origin_matches: false }),
    repo({ dir: 'Hive/repos/a/broken', error: 'git diff failed' }), repo({ dir: 'Hive/repos/a/odd', error: 'something new' }),
    repo({ dir: null, url: null, pushed: 'nothing_to_push' }), repo({ dir: 'Hive/repos/a/p', pushed: 'yes' }), repo({ dir: 'Hive/repos/a/u', pushed: 'unknown' })],
  changes: [{ root: null, repo: 'sidecar', files: 2, added: 5, deleted: 1, landed: ['c0ffee1'] }, { root: null, repo: null, files: 1, added: 1, deleted: 0, landed: [] }],
  files: [{ kind: 'content' }, { kind: 'knowledge' }, { kind: 'external-diff' }, { kind: 'other' }],
  files_omitted: 2,
});

const run = (id: string, over: Partial<SwarmOutRun> = {}): SwarmOutRun => ({
  dispatch_id: id, hive_id: 'h1', hive: 'explorer', session_id: `s-${id}`, title: `task ${id}`,
  origin: 'swarm-out', sent_at: ago(1), waiting: false, running: false, state: 'stopped', state_reason: 'idle',
  result: null, result_status: 'fetched', ...over,
});

const RUNS: SwarmOutRuns = {
  runs: [
    run('rich', { result: RICH, project_agent: { brain: 'widget', mode: 'build', revision: 'r', profile: 'full', delivery: 'bundle', acceptance: 'tests green' },
      stage: 'awaiting', why: null, actions: [{ key: 'accept', primary: true, variant: 'land' }, { key: 'rerun', primary: false },
        { key: 'open_session', primary: false }, { key: 'discard', primary: false }] }),
    run('halted', { result: result({ ended: 'error' }), stage: 'stopped', why: 'error',
      actions: [{ key: 'rerun', primary: true }, { key: 'discard', primary: false }] }),
    run('done', { result: result({ project_agent: null, lessons: { ...LESSONS, error: 'not_supported' } }) }),
    run('stopped', { result: result({ ended: 'stopped', project_agent: { brain: 'General', mode: null, profile: null, delivery: null }, gate_refusals: null }) }),
    run('paused', { result: result({ ended: 'paused', provisional: true, cost_usd: null, active_s: null, ended_at: null }) }),
    run('norecord', { result: result({ ended: 'no_record', outcome_recorded: false, hive_records_outcomes: false }) }),
    run('exited', { result: result({ ended: 'no_record', outcome_recorded: false, hive_records_outcomes: true, active_s: 30 }) }),
    run('pending', { result_status: 'pending' }),
    run('failedfetch', { result_status: 'failed', result_error: 'http_502' }),
    run('gone', { state_reason: 'hive_gone', result_status: 'failed', result_error: 'session_gone' }),
    run('off', { state_reason: 'hive_stopped' }),
    run('running', { state: 'running', running: true, state_reason: 'preparing' }),
    run('running2', { state: 'running', running: true, state_reason: 'streaming' }),
    run('unknown', { state: 'unknown', running: null }),
    run('nosession', { state: 'unknown', running: null, session_unknown: true, session_id: null, sent_at: ago(40) }),
    run('waits', { state: 'waiting', waiting: true }),
    run('old', { sent_at: ago(24 * 5), result: result({ ended: 'crashed', error: { code: 'crashed' } }) }),
  ],
  asks: [
    { kind: 'approval', hive: 'explorer', hive_id: 'h1', session_id: 's-x', request_id: 'a1', asked_at: ago(1),
      command: 'rm -rf build', reason: 'clean the build', tool_name: 'Bash', truncated: true, dispatch_title: null, possible_dispatch_title: 'task maybe' },
    { kind: 'approval', hive: 'explorer', hive_id: 'h1', session_id: 's-y', request_id: 'a2', asked_at: ago(1),
      command: 'ls', reason: 'look', tool_name: 'Bash', truncated: false, dispatch_title: null },
    { kind: 'question', hive: 'explorer', hive_id: 'h1', session_id: 's-z', request_id: 'q1', asked_at: ago(1), dispatch_title: 'task q',
      questions: [{ question: 'Which branch?', header: 'b', multiSelect: false, options: [{ label: 'main', description: 'the default' }] },
        { question: 'Anything else?', header: 'c', multiSelect: false, options: [] }],
      omitted: { questions: 1, options: 1 } },
    { kind: 'approval', hive: 'explorer', hive_id: 'h1', session_id: 's-r', request_id: 'r1', asked_at: ago(1),
      command: 'ls', reason: 'x', tool_name: 'Bash', dispatch_title: 'task refused', refused: true },
  ],
  errors: ['could not read the questions of Hive demo', 'could not list the running Hives', 'could not read a Hive record'],
};

const hive = (name: string, over: Partial<SwarmOutFleet['hives'][number]> = {}) => ({
  name, status: 'running', region: 'us-east-1', version: '2.3.0-hive3', instance_type: 'm7g.xlarge',
  reachable: true, streaming: 1, saturated: false, memory_percent: 60, ...over,
});
const FLEET: SwarmOutFleet = {
  running: 3,
  hives: [hive('explorer'), hive('demo', { streaming: 4, saturated: true, memory_percent: 93 }), hive('flaky', { reachable: false, streaming: null, saturated: null, memory_percent: null }),
    hive('old', { status: 'stopped', reachable: null, streaming: null, saturated: null, memory_percent: null }),
    hive('broken', { status: 'error', reachable: null, streaming: null, saturated: null, memory_percent: null })],
};

beforeEach(() => {
  vi.mocked(swarmOutService.runs).mockResolvedValue(RUNS);
  vi.mocked(swarmOutService.fleet).mockResolvedValue(FLEET);
  vi.mocked(swarmOutService.result).mockResolvedValue({ dispatch_id: 'rich', record: {
    result: { ...RICH, report: 'All done.', report_chars: 900, error: { code: 'MODEL_ERROR', message: 'throttled' },
      work: { ...RICH.work, repos: [{ ...repo(), files_total: 9, commits: ['b'.repeat(40)], commits_total: 3, bundle: { error: 'too big' } }],
        files: [{ path: 'Hive/docs/a.md', kind: 'content' }] } },
    fetch_error: null, checked_at: ago(0), fetched_at: ago(0) } });
});

const panel = () => screen.getByTestId('swarm-out-dashboard');

describe('in English, nothing on the panel is Chinese', () => {
  it('the run view, and a run’s big card on every tab and box', async () => {
    render(<SwarmOutDashboard now={NOW} onOpenHive={() => {}} />);
    await screen.findAllByTestId('swarm-out-row');
    await screen.findAllByTestId('swarm-out-fleet-hive');
    fireEvent.click(screen.getByTestId('swarm-out-section-done'));      // every row on screen
    expect(cjkIn(panel().outerHTML)).toEqual([]);
    const rich = screen.getAllByTestId('swarm-out-row').find((c) => c.textContent?.includes('task rich'))!;
    fireEvent.click(rich);
    const overlay = await screen.findByTestId('swarm-out-review-overlay');
    await within(overlay).findByTestId('swarm-out-review-report');
    for (const tab of ['autonomy', 'process', 'cost', 'lessons', 'deliverable']) {
      fireEvent.click(within(overlay).getByTestId(`swarm-out-review-tab-${tab}`));
      expect(cjkIn(overlay.outerHTML)).toEqual([]);
    }
    fireEvent.click(within(overlay).getByTestId('swarm-out-cmd-rerun'));
    expect(cjkIn(overlay.outerHTML)).toEqual([]);
    fireEvent.click(within(overlay).getByTestId('swarm-out-review-cancel'));
    fireEvent.click(within(overlay).getByTestId('swarm-out-cmd-discard'));
    expect(cjkIn(overlay.outerHTML)).toEqual([]);
    fireEvent.click(within(overlay).getByTestId('swarm-out-review-cancel'));
    fireEvent.keyDown(window, { key: 'j' });                             // the stopped run
    expect(within(overlay).getByTestId('swarm-out-review-stage').textContent).toContain('Stopped · ended with an error');
    expect(cjkIn(overlay.outerHTML)).toEqual([]);
  });

  it('the history view', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('swarm-out-row');
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await screen.findAllByTestId('history-run');
    expect(cjkIn(panel().outerHTML)).toEqual([]);
  });

  it('the setup guide, with no Hive and with none running', () => {
    const { container, rerender } = render(<HiveGuide hives={[]} onOpenHive={() => {}} />);
    expect(cjkIn(container.innerHTML)).toEqual([]);
    rerender(<HiveGuide hives={[{ name: 'w1', status: 'stopped', region: null }]} onOpenHive={() => {}} />);
    expect(container.textContent).toContain('w1 (Off)');
    expect(cjkIn(container.innerHTML)).toEqual([]);
  });

  it('every label function, on every branch', () => {
    const out = [
      ...ENDINGS.map((e) => endedLabel(result({ ended: e, provisional: true })).text),
      ...HIVE_STATUSES.map(hiveStatusLabel),
      ...['session_gone', 'hive_silent', 'hive_not_running', 'hive_unusable', 'unexpected_body', 'no session', 'http_404'].map(resultErrorLabel),
      ...RUNS.runs.map(stateLabel),
      ...workLines(RICH), ...workLines(result()), ...runStats(RICH).flatMap((x) => [x.label, x.value, x.sub ?? '', x.title ?? '']),
      lessonsLine(RICH) ?? '', lessonsLine(result({ lessons: { ...LESSONS, sent: 0, returned: 0, error: 'x' } })) ?? '',
      gateLine(RICH) ?? '', gateLine(result({ project_agent: { brain: 'b', mode: null, profile: null, delivery: null }, gate_refusals: null })) ?? '',
      ...[['PASS', 'PASS', 'PASS', 'PASS', 'PASS'], ['PASS', 'PASS', 'PENDING', 'PASS', 'PASS'], ['PASS', 'PASS', 'PASS', 'NA', 'PASS'],
        ['PASS', 'WARN', 'PASS', 'NA', 'PASS'], ['BLOCK', 'PASS', 'PASS', 'PASS', 'NA']].map((v) =>
        judgeSummary({ outcome: '', verdicts: v as never, numbers: ['1', '2', '3', '4', '5'] })),
    ];
    expect(out.flatMap(cjkIn)).toEqual([]);
    expect(endedLabel(result({ ended: 'turn_limit' })).text).toBe('Hit the turn limit');
    expect(workLines(RICH)).toContain('acme/widget: 3 commits, changed 4 files +120 −30, not pushed, plus 2 untracked files');
  });
});

describe('a language switch reaches a panel already on screen', () => {
  it('English, then Chinese', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('swarm-out-row');
    expect(screen.getByRole('button', { name: 'Runs' })).toBeTruthy();
    await act(async () => { await i18n.changeLanguage('zh'); });
    try {
      expect(screen.getByRole('button', { name: '运行' })).toBeTruthy();
      expect(screen.getByTestId('swarm-out-fleet').textContent).toContain('机群');
      expect(screen.getByTestId('swarm-out-section-you').textContent).toContain('等你');
    } finally {
      await act(async () => { await i18n.changeLanguage('en'); });
    }
  });
});

describe('status colours follow ./tone', () => {
  it('each run and Hive gets the tone of its state', () => {
    const by = Object.fromEntries(RUNS.runs.map((r) => [r.dispatch_id, runTone(r)]));
    expect(by).toMatchObject({ rich: 'error', done: 'ok', stopped: 'attention', paused: 'unknown', norecord: 'unknown',
      pending: 'unknown', running: 'busy', unknown: 'unknown', waits: 'attention', old: 'error' });
    expect(FLEET.hives.map(hiveTone)).toEqual(['ok', 'attention', 'error', 'unknown', 'error']);
  });

  it('rows, slots and the numbers are coloured by that tone', async () => {
    render(<SwarmOutDashboard now={NOW} />);
    const rows = await screen.findAllByTestId('swarm-out-row');
    const dot = (t: string) => (rows.find((c) => c.textContent?.includes(t))!.querySelector('[data-tone]') as HTMLElement).dataset.tone;
    expect(dot('task rich')).toBe('attention');          // awaiting acceptance: it waits on you
    expect(dot('task halted')).toBe('error');            // stopped
    expect(dot('task waits')).toBe('attention');         // needs your answer
    expect(dot('task running')).toBe('busy');

    await screen.findAllByTestId('swarm-out-fleet-hive');
    const explorer = screen.getAllByTestId('swarm-out-fleet-hive').find((h) => h.textContent?.includes('explorer'))!;
    const demo = screen.getAllByTestId('swarm-out-fleet-hive').find((h) => h.textContent?.includes('demo'))!;
    const fill = (h: HTMLElement) => (within(h).getByTestId('swarm-out-memory').firstElementChild as HTMLElement);
    expect(within(explorer).getByTestId('swarm-out-memory').dataset.percent).toBe('60');
    expect(fill(explorer).style.width).toBe('60%');
    expect(fill(explorer).style.background).toBe('var(--color-status-success)');
    expect(fill(demo).style.background).toBe('var(--color-status-warning)');
    // explorer takes a task now, demo is saturated: room left, so the number is green
    expect(screen.getByTestId('kpi-free').dataset.tone).toBe('ok');
    expect(screen.getByTestId('kpi-waiting').dataset.tone).toBe('attention');
  });

  it('every Hive full: the free number is amber', async () => {
    vi.mocked(swarmOutService.fleet).mockResolvedValue({ running: 1, hives: [hive('demo', { streaming: 4, saturated: true, memory_percent: 93 })] });
    render(<SwarmOutDashboard now={NOW} />);
    await screen.findAllByTestId('swarm-out-fleet-hive');
    expect(screen.getByTestId('kpi-free').dataset.tone).toBe('attention');
  });
});
