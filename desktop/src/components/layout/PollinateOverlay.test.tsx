import type React from 'react';
/**
 * Tests for PollinateOverlay and PollinateWorkCard (design: the Pollinate overlay mockup of
 * 2026-11-26).
 *
 * Covers: a work with an open revision is a card (version track, scope, Open in Canvas,
 * Open session) and every other work a row; a click opens the big card with the backend's
 * actions; the empty state hides tabs and filters; failed versions and guard blocks never
 * use the attention or error colour; the kind filter; the Eval tab's numbers; nothing of the
 * pre-v2 gallery (Insights, publish status, run filters) is rendered or fetched.
 */
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';

const fetchWorks = vi.fn();
const fetchWorkView = vi.fn();
const fetchEval = vi.fn();
vi.mock('../../services/pollinate', async () => {
  const actual = await vi.importActual<typeof import('../../services/pollinate')>('../../services/pollinate');
  return {
    ...actual,
    pollinateService: {
      fetchWorks: () => fetchWorks(),
      fetchWorkView: (p: string) => fetchWorkView(p),
      fetchEval: () => fetchEval(),
    },
    assetThumbUrl: (p: string) => `http://x/raw?path=${p}`,
  };
});

import { PollinateContent } from './PollinateOverlay';
import { workToCamel, type PollinateWork, type PollinateWorkView } from '../../services/pollinate';
import { QueryClient as __QC, QueryClientProvider as __QCP } from '@tanstack/react-query';
import { render as __render } from '@testing-library/react';

const renderQ = (ui: React.ReactElement) =>
  __render(<__QCP client={new __QC({ defaultOptions: { queries: { retry: false } } })}>{ui}</__QCP>);

const v = (id: string, extra: Record<string, unknown> = {}) => ({ id, by: 'agent', status: 'ok', at: '2026-11-25T10:00:00Z', scope: [], ...extra });

const DECK: PollinateWork = workToCamel({
  path: 'Projects/AIDLC/assets/deck', in_workspace: true, kind: 'deck', title: 'AIDLC roadmap', brain: 'AIDLC',
  width: 1920, height: 1080, slide_count: 31, current: 'v004', latest: 'v005', status: 'failed',
  updated: '2026-11-26T08:00:00Z',
  open: { scope: ['s-7f3a', 's-91c0', 's-2c63'], note: 'titles on one line', at: '2026-11-26T08:00:00Z', session: 'S1' },
  versions: [v('v001', { by: 'user' }), v('v002'), v('v003'), v('v003-user', { by: 'user', found_on_disk: true }),
    v('v004'), v('v005', { status: 'failed' })],
  thumbnail: 'Projects/AIDLC/assets/deck/out/slides/01-s-1.png', main_output: 'Projects/AIDLC/assets/deck/out/deck.html',
});
const POSTER: PollinateWork = workToCamel({
  path: 'Knowledge/Pollinate/day30', in_workspace: true, kind: 'poster', title: 'Day 30', brain: 'General',
  width: 1080, height: 1440, current: 'v003', latest: 'v003', status: 'ok', updated: '2026-11-25T08:00:00Z',
  versions: [v('v001'), v('v002'), v('v003')], thumbnail: 'Knowledge/Pollinate/day30/out/poster.png',
  main_output: 'Knowledge/Pollinate/day30/out/poster.png',
});
const FILM: PollinateWork = workToCamel({
  path: 'Services/x/film', in_workspace: true, kind: 'video', title: 'Edge AI', brain: 'General', style: 'anatomy',
  width: 1920, height: 1080, duration_s: 83, scene_count: 9, versions: [], main_output: 'Services/x/film/out/edge.mp4',
});

const VIEW: PollinateWorkView = {
  work: DECK,
  versions: DECK.versions.map((x) => ({ ...x, guardOk: x.id === 'v005' ? false : null, failCounts: x.id === 'v005' ? { nowrap: 2 } : {} })),
  findings: [{ check: 'nowrap', level: 'fail', slide: 's-7f3a', msg: 'wraps', carried: false },
    { check: 'font_floor', level: 'warn', slide: 's-1', msg: 'small', carried: true }],
  guard: { ok: false, violations: [{ slide: 's-4e2d', kind: 'modified', detail: 'page 9 changed' }] },
  brief: '# Brief\nWho: CMHK leadership\nThesis: one line',
  outputs: [{ path: 'Projects/AIDLC/assets/deck/out/deck.html', name: 'deck.html', bytes: 2_400_000, files: null }],
  slides: [{ n: 1, id: 's-1', image: 'a.png', changed: false }, { n: 2, id: 's-7f3a', image: 'b.png', changed: true }],
  scenes: [], session: 'S1',
  actions: [{ key: 'open_canvas', primary: true, path: 'Projects/AIDLC/assets/deck/out/deck.html', session: null },
    { key: 'open_session', primary: false, path: null, session: 'S1' }],
};

const EVAL = {
  days: 15, rate: { cur: { passed: 17, revisions: 25, pct: 68 }, prev: { passed: 7, revisions: 17, pct: 41 } },
  daily: Array.from({ length: 30 }, (_, i) => ({ date: `d${i}`, firstPass: i % 3, later: 0, notPassed: 0 })),
  worksChanged: { cur: 9, prev: 3 }, versionsMedian: { cur: 4, prev: 6 }, personVersions: { cur: 7, prev: 11 },
  guardBlocks: { cur: 3, prev: 5 },
  checkHits: [{ check: 'font_floor', count: 11, topTitle: 'AIDLC roadmap', topCount: 5 }],
  guardList: [{ path: 'Projects/AIDLC/assets/deck', title: 'AIDLC roadmap', version: 'v005', at: null, violations: ['page 9 changed'] }],
};

const ATTENTION = /status-warning|status-error/;

describe('PollinateOverlay', () => {
  beforeEach(() => {
    fetchWorks.mockResolvedValue({ works: [DECK, POSTER, FILM], skipped: 0 });
    fetchWorkView.mockResolvedValue(VIEW);
    fetchEval.mockResolvedValue(EVAL);
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); });

  it('shows a work with an open revision as a card and the others as rows', async () => {
    const onContinue = vi.fn().mockResolvedValue(true);
    const close = vi.fn();
    renderQ(<PollinateContent onDispatch={() => true} close={close} onContinue={onContinue} />);
    const card = await screen.findByTestId(`pollinate-card-${DECK.path}`);
    expect(card.textContent).toContain('AIDLC roadmap');
    expect(card.textContent).toContain('31');
    expect(within(card).getByTestId('pollinate-card-scope').textContent).toContain('titles on one line');
    const track = within(card).getByTestId(`pollinate-track-${DECK.path}`);
    const nodes = track.querySelectorAll('[data-version]');
    expect(Array.from(nodes).map((n) => n.getAttribute('data-version'))).toEqual(['v001', 'v002', 'v003', 'v003-user', 'v004', 'v005']);
    expect(track.querySelector('[data-version="v004"]')!.getAttribute('data-state')).toBe('current');
    expect(track.querySelector('[data-version="v005"]')!.getAttribute('data-state')).toBe('failed');
    expect(track.querySelector('[data-version="v003-user"]')!.getAttribute('data-by')).toBe('user');
    expect(track.querySelector('[data-state="open"]')).toBeTruthy();
    expect(screen.getByTestId(`pollinate-row-${POSTER.path}`)).toBeTruthy();
    expect(screen.getByTestId(`pollinate-row-${FILM.path}`).textContent).toContain('anatomy');
    expect(screen.queryByTestId(`pollinate-row-${DECK.path}`)).toBeNull();
    fireEvent.click(within(card).getByTestId('pollinate-card-session'));
    await waitFor(() => expect(onContinue).toHaveBeenCalledWith('S1', ''));
    await waitFor(() => expect(close).toHaveBeenCalled());
  });

  it('Open session on a chat that is gone puts the work in a chat tab instead', async () => {
    const onContinue = vi.fn().mockResolvedValue(false);
    const onDispatch = vi.fn().mockReturnValue(true);
    renderQ(<PollinateContent onDispatch={onDispatch} close={() => {}} onContinue={onContinue} />);
    fireEvent.click(within(await screen.findByTestId(`pollinate-card-${DECK.path}`)).getByTestId('pollinate-card-session'));
    await waitFor(() => expect(onDispatch).toHaveBeenCalledTimes(1));
    expect(onDispatch.mock.calls[0][0]).toContain(DECK.path);
  });

  it('Open in Canvas sends the main output and closes the overlay', async () => {
    const close = vi.fn();
    const seen: string[] = [];
    const onOpen = (e: Event) => seen.push((e as CustomEvent).detail.path);
    document.addEventListener('swarm:open-file', onOpen);
    renderQ(<PollinateContent onDispatch={() => true} close={close} />);
    fireEvent.click(within(await screen.findByTestId(`pollinate-card-${DECK.path}`)).getByTestId('pollinate-card-canvas'));
    expect(close).toHaveBeenCalled();
    expect(seen).toEqual([DECK.mainOutput]);
    document.removeEventListener('swarm:open-file', onOpen);
  });

  it('a click opens the big card with the brief, the checks and the backend actions', async () => {
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    fireEvent.click(await screen.findByTestId(`pollinate-card-${DECK.path}`));
    const wc = await screen.findByTestId('pollinate-wc');
    await waitFor(() => expect(fetchWorkView).toHaveBeenCalledWith(DECK.path));
    const brief = await within(wc).findByTestId('pollinate-wc-brief');
    expect(brief.textContent).toContain('CMHK leadership');
    expect(brief.textContent).not.toContain('# Brief');
    const marks = within(wc).getAllByTestId('pollinate-wc-check');
    expect(marks.map((m) => m.getAttribute('data-ok'))).toEqual(['false', 'false']);   // guard, nowrap
    expect(within(wc).getAllByTestId('pollinate-wc-slide').map((s) => s.getAttribute('data-changed'))).toEqual(['false', 'true']);
    expect(within(wc).getByTestId('pollinate-wc-cmd-open_canvas')).toBeTruthy();
    expect(within(wc).getByTestId('pollinate-wc-cmd-open_session')).toBeTruthy();
    fireEvent.click(within(wc).getByTestId('pollinate-wc-tab-checks'));
    const findings = within(wc).getAllByTestId('pollinate-wc-finding');
    expect(findings.map((f) => f.getAttribute('data-carried'))).toEqual(['false', 'true']);
    fireEvent.click(within(wc).getByTestId('pollinate-wc-close'));
    await waitFor(() => expect(screen.queryByTestId('pollinate-wc')).toBeNull());
  });

  it('never paints a failed version, a failed check or a guard block in the attention or error colour', async () => {
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    const card = await screen.findByTestId(`pollinate-card-${DECK.path}`);
    expect(card.innerHTML).not.toMatch(ATTENTION);
    fireEvent.click(card);
    const wc = await screen.findByTestId('pollinate-wc');
    await within(wc).findByTestId('pollinate-wc-brief');
    expect(within(wc).getByTestId('pollinate-wc-brief').innerHTML).not.toMatch(ATTENTION);
    fireEvent.click(within(wc).getByTestId('pollinate-wc-tab-versions'));
    expect(within(wc).getByTestId('pollinate-wc-versions').innerHTML).not.toMatch(ATTENTION);
    fireEvent.click(within(wc).getByTestId('pollinate-wc-close'));
    fireEvent.click(screen.getByTestId('pollinate-tab-eval'));
    const guard = await screen.findByTestId('pollinate-eval-guard');
    expect(guard.innerHTML).not.toMatch(ATTENTION);
  });

  it('the kind filter keeps only that kind', async () => {
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId(`pollinate-card-${DECK.path}`);
    fireEvent.click(screen.getByTestId('pollinate-kind-video'));
    expect(screen.queryByTestId(`pollinate-card-${DECK.path}`)).toBeNull();
    expect(screen.queryByTestId(`pollinate-row-${POSTER.path}`)).toBeNull();
    expect(screen.getByTestId(`pollinate-row-${FILM.path}`)).toBeTruthy();
  });

  it('the Eval tab shows the first-pass rate against the days before and four numbers', async () => {
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    fireEvent.click(await screen.findByTestId('pollinate-tab-eval'));
    const rate = await screen.findByTestId('pollinate-eval-rate');
    expect(rate.textContent).toContain('68%');
    expect(rate.textContent).toContain('17 / 25');
    expect(screen.getByTestId('pollinate-eval').textContent).toContain('41%');
    expect(screen.getAllByTestId('pollinate-eval-number')).toHaveLength(4);
    expect(screen.getByTestId('pollinate-eval-hits').textContent).toContain('font_floor');
  });

  it('with no work registered: one empty state, no tabs, no filters, no eval fetch', async () => {
    fetchWorks.mockResolvedValue({ works: [], skipped: 0 });
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId('pollinate-empty');
    expect(screen.queryByTestId('pollinate-tabs')).toBeNull();
    expect(screen.queryByTestId('pollinate-filter')).toBeNull();
    expect(fetchEval).not.toHaveBeenCalled();
  });

  it('nothing of the pre-v2 run gallery is rendered', async () => {
    renderQ(<PollinateContent onDispatch={() => true} close={() => {}} />);
    await screen.findByTestId(`pollinate-card-${DECK.path}`);
    const text = screen.getByTestId('pollinate-overlay').textContent ?? '';
    for (const gone of ['Insights', 'Earlier runs', 'publish', 'Publish']) expect(text).not.toContain(gone);
    for (const id of ['pollinate-view-insights', 'pollinate-search', 'pollinate-asset-drawer', 'pollinate-earlier-runs']) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    const service = await import('../../services/pollinate');
    expect(Object.keys(service.pollinateService).sort()).toEqual(['fetchEval', 'fetchWorkView', 'fetchWorks']);
  });
});
