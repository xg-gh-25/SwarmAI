/**
 * The run card, round 2: the whole reply (no "full text is N characters" note), a
 * returned repository's changed file opening its diff (taken back for review first when
 * it is not yet, then both versions read and sent to the Canvas as a read-only versions
 * document), and the "inferred from command text" label on the checks.
 */
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { swarmOutService, type SwarmOutAction, type SwarmOutRun } from '../../services/swarmOut';
import { RunOverlay } from './RunOverlay';
import { OPEN_FILE_VERSIONS_EVENT, type FileVersionsDetail } from '../file-viewer/utils/fileVersions';
import { UI_COMMAND_TABLE } from '../../utils/uiCommands';
import i18n from '../../i18n';

beforeAll(async () => { await i18n.changeLanguage('en'); });
afterAll(async () => { await i18n.changeLanguage('en'); });

vi.mock('../../services/swarmOut', () => ({
  swarmOutService: {
    accept: vi.fn(async () => undefined), manualLanded: vi.fn(async () => undefined),
    rerun: vi.fn(async () => undefined), stop: vi.fn(async () => undefined),
    discard: vi.fn(async () => ({ discarded: true, stopped: true })),
    result: vi.fn(() => new Promise(() => {})), answer: vi.fn(),
    returnPrepare: vi.fn(), returnLand: vi.fn(), returnFile: vi.fn(), refresh: vi.fn(),
  },
}));
const svc = vi.mocked(swarmOutService);

const ENDED = '2026-10-30T10:00:00Z';
const NOW = Date.parse('2026-10-31T12:00:00Z');
const TIP = 'b'.repeat(40);
const URL_ = 'https://github.com/acme/widget';

const A = (key: SwarmOutAction['key'], primary = false, variant?: SwarmOutAction['variant']): SwarmOutAction =>
  ({ key, primary, ...(variant ? { variant } : {}) });

function run(over: Partial<SwarmOutRun> = {}, result: Record<string, unknown> = {}): SwarmOutRun {
  return {
    dispatch_id: 'so_round2', hive_id: 'h1', hive: 'explorer', session_id: '9db44de7-b39e-4a35-b739-85b656a04264',
    title: 'fix the widget', origin: 'swarm-out', sent_at: '2026-10-31T08:00:00Z', waiting: false, running: false,
    state: 'stopped',
    project_agent: { brain: 'Widget', mode: 'code-change', revision: 'r1', profile: '', acceptance: 'tests pass', returns: 'commits' },
    result: { ended: 'completed', provisional: false, ended_at: ENDED, turns: 1, report_chars: 0,
      work: { repos: [], files: [], human: { approved: 0, denied: 0, unanswered: 0, answered_questions: 0, said: 0 },
        steps: { total: 3, failed: 0, agents: 0, by_tool: {} }, changes: [], files_omitted: 0 },
      pipeline: null, checks: null, execution_check: null, ...result } as unknown as SwarmOutRun['result'],
    stage: 'awaiting', actions: [A('accept', true, 'land'), A('rerun'), A('open_session'), A('discard')], why: null,
    settle: { verdict: null, code: 'waiting', why_not: ['code'], settled: null },
    ...over,
  };
}

const record = (r: SwarmOutRun, over: Record<string, unknown> = {}) => ({
  dispatch_id: r.dispatch_id,
  record: { result: { ...r.result, ...over }, fetch_error: null, checked_at: ENDED, fetched_at: ENDED },
}) as never;

const repo = {
  url: URL_, ref: 'main', dir: 'Hive/repos/github.com/acme/widget', present: true, origin_matches: true,
  base: 'a'.repeat(40), head: TIP, commits: [TIP], commits_total: 1,
  files: [{ path: 'src/x.py', added: 2, deleted: 1 }, { path: 'assets/logo.png', added: null, deleted: null }],
  files_total: 2, added: 2, deleted: 1, untracked: 0, pushed: 'no', error: null,
  bundle: { key: '0123456789abcdef', sha256: 'c'.repeat(64), size: 10 },
};

function card(r: SwarmOutRun) {
  const props = { runs: [r], asks: [], order: [r.dispatch_id], id: r.dispatch_id, now: NOW,
    onSelect: vi.fn(), onSettled: vi.fn(), onClose: vi.fn(), onFileOpened: vi.fn(), onReload: vi.fn() };
  render(<RunOverlay {...props} />);
  return props;
}

beforeEach(() => { vi.clearAllMocks(); svc.result.mockImplementation(() => new Promise(() => {})); });

describe('the run card, round 2', () => {
  it('renders the whole reply', async () => {
    // 9,000 characters: 900 words of 9, each one unique, the last one at the very end
    const words = Array.from({ length: 900 }, (_, i) => `w${String(i).padStart(4, '0')}xxxx`);
    const report = words.join(' ');
    expect(report.length).toBeGreaterThanOrEqual(8999);
    const r = run();
    svc.result.mockResolvedValue(record(r, { report, report_chars: report.length + 5000 }));
    card(r);
    const shown = await screen.findByTestId('swarm-out-review-report');
    const text = shown.textContent ?? '';
    for (const w of [words[0], words[450], words[899]]) expect(text).toContain(w);
    expect(text.length).toBeGreaterThanOrEqual(report.length);
    // no "the full reply is N characters" note, in either language
    expect(within(shown).queryByText(/characters/)).toBeNull();
    expect(i18n.exists('swarmOut.detail.fullReport')).toBe(false);
  });

  it('a changed file opens its diff', async () => {
    const r = run({}, { work: { ...run().result!.work, repos: [repo] } });
    svc.result.mockResolvedValue(record(r));
    // not taken back for review yet: the versions endpoint answers 409 until prepare ran
    let prepared = false;
    svc.returnPrepare.mockImplementation(async () => { prepared = true; return {} as never; });
    svc.returnFile.mockImplementation(async (_id: string, _url: string, path: string) => {
      if (!prepared) throw Object.assign(new Error('not taken back'), { statusCode: 409 });
      if (path === 'assets/logo.png') {
        return { path, base_sha: 'a'.repeat(40), tip_sha: TIP, base: null, tip: null, base_exists: true, tip_exists: true, binary: true, too_large: false };
      }
      return { path, base_sha: 'a'.repeat(40), tip_sha: TIP, base: 'x = 1\n', tip: 'x = 2\n', base_exists: true, tip_exists: true, binary: false, too_large: false };
    });
    const got: FileVersionsDetail[] = [];
    const listen = (e: Event) => got.push((e as CustomEvent<FileVersionsDetail>).detail);
    document.addEventListener(OPEN_FILE_VERSIONS_EVENT, listen);
    try {
      const p = card(r);
      const rows = await screen.findAllByTestId('swarm-out-review-repo-file');
      expect(rows).toHaveLength(2);

      // a binary file: a short line on its row, no diff, the card stays
      fireEvent.click(rows[1].querySelector('button')!);
      await waitFor(() => expect(within(rows[1]).getByTestId('swarm-out-review-repo-file-note').textContent).toMatch(/Binary/));
      expect(got).toHaveLength(0);
      expect(p.onClose).not.toHaveBeenCalled();

      vi.clearAllMocks();
      prepared = false;
      fireEvent.click(rows[0].querySelector('button')!);
      await waitFor(() => expect(got).toHaveLength(1));
      expect(svc.returnPrepare).toHaveBeenCalledWith('so_round2', URL_);
      const calls = svc.returnFile.mock.invocationCallOrder;
      const prep = svc.returnPrepare.mock.invocationCallOrder[0];
      expect(calls.some((n) => n > prep)).toBe(true);       // read after it was taken back
      expect(svc.returnFile).toHaveBeenLastCalledWith('so_round2', URL_, 'src/x.py');
      expect(got[0]).toEqual({ source: `hive:so_round2@${TIP.slice(0, 12)}`, path: 'src/x.py', base: 'x = 1\n', tip: 'x = 2\n' });
      // the card steps aside so the Canvas is in view
      expect(p.onClose).toHaveBeenCalledTimes(1);
      expect(p.onFileOpened).toHaveBeenCalledTimes(1);
    } finally {
      document.removeEventListener(OPEN_FILE_VERSIONS_EVENT, listen);
    }
    // no agent or backend command reaches the versions document
    expect(Object.values(UI_COMMAND_TABLE).map((e) => e.event)).not.toContain(OPEN_FILE_VERSIONS_EVENT);
  });

  it('labels inferred checks', () => {
    const checks = [{ id: 'tests', status: 'not_seen' }, { id: 'lint', status: 'ran' }];
    card(run({}, { checks, checks_inferred: true }));
    const strip = screen.getByTestId('swarm-out-review-acceptance');
    expect(within(strip).getByTestId('swarm-out-review-checks-inferred').textContent).toContain('inferred from command text');
    cleanup();
    card(run({}, { checks, checks_inferred: false }));
    expect(screen.getByTestId('swarm-out-review-acceptance').textContent).toContain('Not seen: tests');
    expect(screen.queryByTestId('swarm-out-review-checks-inferred')).toBeNull();
    cleanup();
    void i18n.changeLanguage('zh');
    card(run({}, { checks, checks_inferred: true }));
    expect(screen.getByTestId('swarm-out-review-checks-inferred').textContent).toContain('按命令文本推断');
    void i18n.changeLanguage('en');
  });

  it('says when the statuses are the Hive\'s own record', () => {
    const checks = [{ id: 'tests', status: 'ran' }];
    card(run({}, { checks, checks_inferred: false, checks_from_record: true }));
    expect(screen.getByTestId('swarm-out-review-checks-recorded').textContent).toContain('as recorded on the Hive');
    expect(screen.queryByTestId('swarm-out-review-checks-inferred')).toBeNull();
    cleanup();
    card(run({}, { checks, checks_inferred: false, checks_from_record: false }));
    expect(screen.queryByTestId('swarm-out-review-checks-recorded')).toBeNull();
  });
});
