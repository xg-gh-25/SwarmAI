/**
 * Tests for LibraryOverlay (design Knowledge/Mockups/2026-11-24-cm-library-redesign.html).
 *
 *  1. Recent: one number against last week; the source split whose legend filters the
 *     list; the list grouped by local day (three days open, the rest behind "Earlier");
 *     a CDF row's "+N units"; the muted line when the weekly job archived logs; the
 *     loading / error / empty states.
 *  2. The one search box: a query replaces the tab body with the recall hits.
 *  3. Browse: the live Knowledge/ tree (LibraryTree: file opens, a directory toggles)
 *     and the mounts (all muted: stale is a date, a gone path a line; no unmount action).
 *  4. How it works: the four steps with live numbers.
 *  5. The no-double-/api path convention (regression guard, run_b41d0c2a).
 *
 * The api client + workspaceService are mocked at the boundary; the component invents
 * no data (R30).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor, within, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { TreeNode } from '../../types';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock('../../services/workspace', () => ({
  workspaceService: { getTree: vi.fn(), expandDirectory: vi.fn() },
}));
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }));
import api from '../../services/api';
import { workspaceService } from '../../services/workspace';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { LibraryContent } from './LibraryOverlay';
import { groupByDay, readableName, sourceCounts } from './libraryRecent';

const NATIVE_OK = {
  source: 'native', root: 'Knowledge/', category_count: 2,
  categories: [
    { name: 'Designs', file_count: 12, total_bytes: 34_000_000 },
    { name: 'Notes', file_count: 5, total_bytes: 8000 },
  ],
};

/** epoch seconds at local noon, `daysAgo` days before today */
function noon(daysAgo: number, minute = 0): number {
  const d = new Date();
  d.setHours(12, minute, 0, 0);
  d.setDate(d.getDate() - daysAgo);
  return d.getTime() / 1000;
}
const item = (path: string, source: string, daysAgo: number, extra: Record<string, unknown> = {}) => ({
  path, category: path.split('/')[1], mtime: noon(daysAgo), size: 100, source, cdf_units: 0, ...extra,
});
const RECENT_OK = {
  window_days: 7, count: 6, this_week: 6, last_week: 126,
  by_source: { cdf: 2, chat: 2, job: 2 },
  items: [
    item('Knowledge/Library/uv.md', 'cdf', 0, { cdf_units: 4 }),
    item('Knowledge/Mockups/2026-11-24-cm-library-redesign.html', 'chat', 0),
    item('Knowledge/JobResults/2026-11-23-smoke-canary.md', 'job', 1),
    item('Knowledge/Notes/flock-is-unreliable-cand-67f29fddef1c-unit.md', 'cdf', 2, { cdf_units: 1 }),
    item('Knowledge/Designs/plan.md', 'chat', 4),
    item('Knowledge/DailyActivity/2026-11-19.md', 'job', 5),
  ],
};
const RECENT_EMPTY = { window_days: 7, count: 0, this_week: 0, last_week: 3, by_source: { cdf: 0, chat: 0, job: 0 }, items: [] };
const MOUNTS_NONE = { count: 0, mounts: [], registry_ready: true };
const HEALTH_NONE = { generated_at: 1, root: 'Knowledge/', clean: true, findings: [] };

const TREE_OK: TreeNode[] = [
  {
    name: 'Knowledge', path: 'Knowledge', type: 'directory',
    children: [
      { name: 'Designs', path: 'Knowledge/Designs', type: 'directory', children: null }, // truncated → lazy
      { name: 'readme.md', path: 'Knowledge/readme.md', type: 'file' },
    ],
  },
];
const DESIGNS_CHILDREN: TreeNode[] = [{ name: 'plan.md', path: 'Knowledge/Designs/plan.md', type: 'file' }];

// `mounts` may be a function: it is called on every read (a list that changes between reads)
type Routes = { native?: unknown; recent?: unknown; mounts?: unknown; health?: unknown; search?: unknown };
function mockApi(r: Routes = {}) {
  (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    const pick = (v: unknown, fallback: unknown) => (v instanceof Error ? Promise.reject(v) : Promise.resolve({ data: v ?? fallback }));
    if (url.includes('/native')) return pick(r.native, NATIVE_OK);
    if (url.includes('/recent')) return pick(r.recent, RECENT_OK);
    if (url.includes('/mounts')) return pick(typeof r.mounts === 'function' ? (r.mounts as () => unknown)() : r.mounts, MOUNTS_NONE);
    if (url.includes('/health')) return pick(r.health, HEALTH_NONE);
    if (url.includes('/search')) return pick(r.search, { query: '', count: 0, hits: [] });
    return Promise.resolve({ data: {} });
  });
}

function renderOverlay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { qc, ...render(<QueryClientProvider client={qc}><LibraryContent /></QueryClientProvider>) };
}
async function openTab(key: 'recent' | 'browse' | 'how') {
  await screen.findByTestId('library-overlay');
  act(() => { screen.getByTestId(`library-tab-${key}`).click(); });
  return screen.findByTestId(`library-panel-${key}`);
}
const rows = () => screen.queryAllByTestId('library-recent-item');
async function openBrowse() {
  await openTab('browse');
  await screen.findByTestId('library-tree');
  await screen.findByText('readme.md');
}

beforeEach(() => {
  mockApi();
  (workspaceService.getTree as ReturnType<typeof vi.fn>).mockResolvedValue(TREE_OK);
  (workspaceService.expandDirectory as ReturnType<typeof vi.fn>).mockResolvedValue(DESIGNS_CHILDREN);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('readableName / groupByDay', () => {
  it('strips the extension, a leading date and a CDF unit suffix; dashes become spaces', () => {
    expect(readableName('Knowledge/Mockups/2026-11-24-cm-library-redesign.html')).toBe('cm library redesign');
    expect(readableName('Knowledge/Notes/publish-py-drops-cand-0b5436b57671a481-unit.md')).toBe('publish py drops');
    expect(readableName('Knowledge/DailyActivity/2026-11-24.md')).toBe('2026-11-24');
    expect(readableName('Knowledge/Library/Links/aws.amazon.com.md')).toBe('aws.amazon.com');
  });

  it('counts by source from the feed, or from the items of an older feed (session / you are chat)', () => {
    expect(sourceCounts(RECENT_OK)).toEqual({ cdf: 2, chat: 2, job: 2 });
    expect(sourceCounts({ items: [{ source: 'session' }, { source: 'you' }, { source: 'job' }, { source: 'cdf' }] }))
      .toEqual({ cdf: 1, chat: 2, job: 1 });
    expect(sourceCounts(undefined)).toEqual({ cdf: 0, chat: 0, job: 0 });
  });

  it('groups by local day, newest first: today, yesterday, then MM-DD', () => {
    const groups = groupByDay(RECENT_OK.items as never, { today: 'T', yesterday: 'Y' });
    expect(groups.map((g) => g.label).slice(0, 2)).toEqual(['T', 'Y']);
    const d = new Date(noon(2) * 1000);
    expect(groups[2].label).toBe(`${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
    expect(groups.map((g) => g.items.length)).toEqual([2, 1, 1, 1, 1]);
  });
});

describe('LibraryOverlay — Recent', () => {
  it('opens on Recent; tabs are Recent, Browse, How it works in that order', async () => {
    renderOverlay();
    expect(await screen.findByTestId('library-panel-recent')).toBeInTheDocument();
    const tabs = ['recent', 'browse', 'how'].map((k) => screen.getByTestId(`library-tab-${k}`));
    expect(tabs[0].compareDocumentPosition(tabs[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tabs[1].compareDocumentPosition(tabs[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('leads with this week\'s count against last week, and a legend chip per source', async () => {
    renderOverlay();
    expect((await screen.findByTestId('library-recent-big-value')).textContent).toBe('6');
    expect(screen.getByTestId('library-recent-last-week').textContent).toMatch(/last week 126/);
    for (const s of ['cdf', 'chat', 'job']) expect(screen.getByTestId(`library-source-chip-${s}`).textContent).toMatch(/2$/);
  });

  it('groups rows by day: three days open, the rest behind "Earlier N days · M files"', async () => {
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    expect(screen.getByText('Today')).toBeInTheDocument();
    expect(screen.getByText('Yesterday')).toBeInTheDocument();
    expect(rows()).toHaveLength(4);                       // today 2 + yesterday 1 + 2 days ago 1
    const more = screen.getByTestId('library-recent-more');
    expect(more.textContent).toMatch(/2 days · 2 files/);
    act(() => { more.click(); });
    expect(rows()).toHaveLength(6);
    expect(screen.queryByTestId('library-recent-more')).toBeNull();
  });

  it('a legend chip filters the list to its source; clicking it again clears the filter', async () => {
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    act(() => { screen.getByTestId('library-recent-more').click(); });
    const chip = screen.getByTestId('library-source-chip-job');
    act(() => { chip.click(); });
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(rows().map((r) => r.getAttribute('data-source'))).toEqual(['job', 'job']);
    act(() => { chip.click(); });
    expect(rows()).toHaveLength(6);
    // the bar's segment picks the same filter
    act(() => { screen.getByTestId('library-split-cdf').click(); });
    expect(rows().map((r) => r.getAttribute('data-source'))).toEqual(['cdf', 'cdf']);
  });

  it('a CDF row says how many units CDF wrote into it; other rows do not; a row opens its file', async () => {
    const onOpen = vi.fn();
    document.addEventListener('swarm:open-file', onOpen as EventListener);
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    const [uv, mockup] = rows();
    expect(uv.textContent).toMatch(/Library · uv/);
    expect(within(uv).getByTestId('library-recent-units').textContent).toBe('+4 units');
    expect(mockup.textContent).toMatch(/Mockups · cm library redesign/);
    expect(within(mockup).queryByTestId('library-recent-units')).toBeNull();
    expect(uv.textContent).toMatch(/12:00$/);
    act(() => { uv.click(); });
    expect((onOpen.mock.calls[0][0] as CustomEvent<{ path: string }>).detail.path).toBe('Knowledge/Library/uv.md');
    document.removeEventListener('swarm:open-file', onOpen as EventListener);
  });

  it('names what Swarm archived by itself this week, only when it archived something this week', async () => {
    mockApi({ health: { ...HEALTH_NONE, auto_archived: { at: Date.now() / 1000 - 3600, count: 566 } } });
    renderOverlay();
    expect((await screen.findByTestId('library-auto-archived')).textContent).toMatch(/566 files this week/);
    cleanup();
    mockApi({ health: { ...HEALTH_NONE, auto_archived: { at: Date.now() / 1000 - 9 * 86400, count: 566 } } });
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    expect(screen.queryByTestId('library-auto-archived')).toBeNull();
  });

  it('says the list is cut when the week has more files than the feed lists', async () => {
    mockApi({ recent: { ...RECENT_OK, count: 260, this_week: 260 } });
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    expect(screen.queryByTestId('library-recent-capped')).toBeNull();          // the rest is still folded
    act(() => { screen.getByTestId('library-recent-more').click(); });
    expect(screen.getByTestId('library-recent-capped').textContent).toBe('Only the newest 6 of 260 are listed.');
  });

  it('a filter whose source fell to 0 on a refetch is dropped for good: it does not come back with the source', async () => {
    const { qc } = renderOverlay();
    await screen.findByTestId('library-recent-days');
    act(() => { screen.getByTestId('library-recent-more').click(); });
    act(() => { screen.getByTestId('library-source-chip-job').click(); });
    expect(rows()).toHaveLength(2);
    mockApi({ recent: { ...RECENT_OK, by_source: { cdf: 2, chat: 2, job: 0 }, items: RECENT_OK.items.filter((i) => i.source !== 'job') } });
    await act(async () => { await qc.invalidateQueries({ queryKey: ['library-recent'] }); });
    await waitFor(() => expect(screen.queryByTestId('library-source-chip-job')).toBeNull());
    expect(rows()).toHaveLength(4);
    mockApi();
    await act(async () => { await qc.invalidateQueries({ queryKey: ['library-recent'] }); });
    const chip = await screen.findByTestId('library-source-chip-job');
    expect(chip.getAttribute('aria-pressed')).toBe('false');
    expect(rows()).toHaveLength(6);
  });

  it('a chip whose source has no rows listed shows a line, not a blank', async () => {
    mockApi({ recent: { ...RECENT_OK, by_source: { cdf: 2, chat: 2, job: 3 }, items: RECENT_OK.items.filter((i) => i.source !== 'job') } });
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    act(() => { screen.getByTestId('library-source-chip-job').click(); });
    expect(screen.getByTestId('library-recent-none')).toBeInTheDocument();
  });

  it('shows an error with a working Retry when the recent fetch fails', async () => {
    mockApi({ recent: new Error('boom') });
    renderOverlay();
    expect(await screen.findByTestId('library-recent-error')).toBeInTheDocument();
    const count = () => (api.get as ReturnType<typeof vi.fn>).mock.calls.filter((c) => String(c[0]).includes('/recent')).length;
    const before = count();
    act(() => { screen.getByTestId('library-recent-retry').click(); });
    await waitFor(() => expect(count()).toBeGreaterThan(before));
  });

  it('an empty week says so, without the number or the list', async () => {
    mockApi({ recent: RECENT_EMPTY });
    renderOverlay();
    const panel = await screen.findByTestId('library-panel-recent');
    await waitFor(() => expect(panel.textContent).toMatch(/Nothing new in the last week/));
    expect(screen.queryByTestId('library-recent-big')).toBeNull();
    expect(screen.queryByTestId('library-recent-error')).toBeNull();
  });
});

describe('LibraryOverlay — one search box for all tabs', () => {
  const SEARCH = {
    query: 'uv', count: 2, hits: [
      { domain: 'library', title: 'uv', source: 'Knowledge/Library/uv.md' },
      { domain: 'codeintel', title: 'run_uv', source: 'core/uv.py', mount_id: 'm1' },
    ],
  };

  it('shows the store size in the box; a query replaces the tab body with the recall hits until cleared', async () => {
    mockApi({ search: SEARCH, mounts: { count: 1, registry_ready: true, mounts: [] } });
    renderOverlay();
    await screen.findByTestId('library-panel-recent');
    expect((await screen.findByTestId('library-search-hint')).textContent).toBe('17 files · 1 mount');
    const input = screen.getByTestId('library-search-input');
    fireEvent.change(input, { target: { value: 'uv' } });
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByTestId('library-search-results')).toBeInTheDocument();
    expect(screen.queryByTestId('library-panel-recent')).toBeNull();
    await waitFor(() => expect(screen.getAllByTestId('library-search-hit')).toHaveLength(2));
    expect(String((api.get as ReturnType<typeof vi.fn>).mock.calls.find((c) => String(c[0]).includes('/search'))?.[0])).toBe('/library/search?q=uv');
    act(() => { screen.getByTestId('library-search-clear').click(); });
    expect(await screen.findByTestId('library-panel-recent')).toBeInTheDocument();
    expect(screen.queryByTestId('library-search-results')).toBeNull();
  });

  it('a failed search says so with a Retry, never "no hits"', async () => {
    mockApi({ search: { query: 'uv', hits: [], error: 'search unavailable' } });
    renderOverlay();
    await screen.findByTestId('library-panel-recent');
    const input = screen.getByTestId('library-search-input');
    fireEvent.change(input, { target: { value: 'uv' } });
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByTestId('library-search-error')).toBeInTheDocument();
    expect(screen.getByTestId('library-search-results').textContent).not.toMatch(/No recall hits/);
  });

  it('Escape in the box clears the query (and does not reach the overlay)', async () => {
    mockApi({ search: SEARCH });
    renderOverlay();
    await screen.findByTestId('library-panel-recent');
    const input = screen.getByTestId('library-search-input');
    fireEvent.change(input, { target: { value: 'uv' } });
    fireEvent.submit(input.closest('form')!);
    await screen.findByTestId('library-search-results');
    const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    act(() => { input.dispatchEvent(ev); });
    expect(ev.defaultPrevented).toBe(true);
    expect(await screen.findByTestId('library-panel-recent')).toBeInTheDocument();
    expect((input as HTMLInputElement).value).toBe('');
  });

  it('the hint waits for the mounts: no "0 mounts" while they load', async () => {
    (api.get as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      url.includes('/mounts') ? new Promise(() => {}) : url.includes('/native') ? Promise.resolve({ data: NATIVE_OK })
        : url.includes('/recent') ? Promise.resolve({ data: RECENT_OK }) : Promise.resolve({ data: HEALTH_NONE }));
    renderOverlay();
    await screen.findByTestId('library-recent-days');
    expect(screen.queryByTestId('library-search-hint')).toBeNull();
    await openTab('browse');
    expect(screen.getByTestId('library-mounts-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('library-mounted-empty')).toBeNull();
  });

  it('the search is shared: from Browse it replaces the Browse body too', async () => {
    mockApi({ search: SEARCH });
    renderOverlay();
    await openTab('browse');
    const input = screen.getByTestId('library-search-input');
    fireEvent.change(input, { target: { value: 'uv' } });
    fireEvent.submit(input.closest('form')!);
    expect(await screen.findByTestId('library-search-results')).toBeInTheDocument();
    expect(screen.queryByTestId('library-panel-browse')).toBeNull();
  });
});

describe('LibraryOverlay — Browse is a live Knowledge/ tree', () => {
  it('renders the Knowledge/ tree roots (dir + file) from /workspace/tree', async () => {
    renderOverlay();
    await openBrowse();
    expect(await screen.findByText('Designs')).toBeInTheDocument();
    expect(screen.getByText('readme.md')).toBeInTheDocument();
    expect(workspaceService.getTree as ReturnType<typeof vi.fn>).toHaveBeenCalled();
  });

  it('clicking a FILE dispatches swarm:open-file with its path (→ Canvas)', async () => {
    const onOpen = vi.fn();
    document.addEventListener('swarm:open-file', onOpen as EventListener);
    renderOverlay();
    await openBrowse();
    act(() => { screen.getByText('readme.md').click(); });
    await waitFor(() => expect(onOpen).toHaveBeenCalled());
    expect((onOpen.mock.calls[0][0] as CustomEvent<{ path: string }>).detail.path).toBe('Knowledge/readme.md');
    document.removeEventListener('swarm:open-file', onOpen as EventListener);
  });

  it('clicking a DIRECTORY toggles expand + lazy-loads children; it NEVER dispatches open-file', async () => {
    const onOpen = vi.fn();
    document.addEventListener('swarm:open-file', onOpen as EventListener);
    renderOverlay();
    await openBrowse();
    act(() => { screen.getByText('Designs').click(); });
    await waitFor(() =>
      expect(workspaceService.expandDirectory as ReturnType<typeof vi.fn>).toHaveBeenCalledWith('Knowledge/Designs', expect.any(Number)),
    );
    expect(await screen.findByText('plan.md')).toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
    document.removeEventListener('swarm:open-file', onOpen as EventListener);
  });

  it('shows an ERROR state with a working Retry when the tree fetch fails', async () => {
    (workspaceService.getTree as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('503'));
    renderOverlay();
    await openTab('browse');
    expect(await screen.findByTestId('library-tree-error')).toBeInTheDocument();
    const before = (workspaceService.getTree as ReturnType<typeof vi.fn>).mock.calls.length;
    act(() => { screen.getByTestId('library-tree-retry').click(); });
    await waitFor(() => expect((workspaceService.getTree as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before));
  });

  it('with no mounts: a short line and the Add folder button', async () => {
    renderOverlay();
    await openTab('browse');
    expect((await screen.findByTestId('library-mounted-empty')).textContent).toBe('No folders mounted.');
    expect(screen.getByTestId('library-add-folder')).toBeInTheDocument();
  });
});

describe('LibraryOverlay — mounts are muted: no state is a decision offered here', () => {
  const ago = (days: number) => new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 19).replace('T', ' ');
  const MOUNTS = {
    count: 3, registry_ready: true,
    mounts: [
      { id: 'fresh', path: '/Users/x/repos/foo', kind: 'code', health: 'fresh', enabled: true, last_synced: ago(2) },
      { id: 'stale', path: '/Users/x/Desktop/AI-Native', kind: 'code', health: 'stale', enabled: true, last_synced: ago(25) },
      { id: 'gone', path: '/Users/x/old-docs', kind: 'docs', health: 'missing', enabled: true, last_synced: ago(40) },
    ],
  };

  it('a stale index is a muted "synced N days ago"; a missing path says so, muted too', async () => {
    mockApi({ mounts: MOUNTS });
    renderOverlay();
    await openTab('browse');
    const stale = await screen.findByTestId('library-mount-state-stale');
    expect(stale.textContent).toBe('code · indexed');
    expect(stale.getAttribute('style')).toBeNull();
    expect(screen.getByTestId('library-mount-sync-stale').textContent).toBe('synced 25 days ago');
    const gone = screen.getByTestId('library-mount-state-gone');
    expect(gone.textContent).toBe('docs · path is gone');
    expect(screen.getByTestId('library-mount-sync-fresh').textContent).toMatch(/^synced \d\d-\d\d$/);
    expect(screen.getByTestId('library-mount-fresh').textContent).toMatch(/foo/);
    // nothing in the mounts section carries the warning colour
    expect(screen.getByTestId('library-mounted-section').innerHTML).not.toMatch(/status-warning/);
  });

  it('a mount not indexed yet says so, muted', async () => {
    mockApi({ mounts: { count: 1, registry_ready: true, mounts: [{ id: 'raw', path: '/x/docs', kind: 'docs', health: 'fresh', enabled: true, last_synced: null }] } });
    renderOverlay();
    await openTab('browse');
    const state = await screen.findByTestId('library-mount-state-raw');
    expect(state.textContent).toBe('docs · no text content');
    expect(state.getAttribute('style')).toBeNull();
    expect(screen.getByTestId('library-mount-raw').getAttribute('data-indexed')).toBe('false');
  });
});

describe('LibraryOverlay — a code folder indexed in the background', () => {
  const one = (synced: string | null) => ({
    count: 1, registry_ready: true,
    mounts: [{ id: 'big', path: '/x/big-repo', kind: 'code', health: 'fresh', enabled: true, last_synced: synced }],
  });

  it('after Add folder answers "indexing", the mounts are re-read until the row is indexed, then no more', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let reads = 0;
      // reads 1 and 2 (open, the re-read right after the add) still have no index
      mockApi({ mounts: () => (++reads <= 2 ? one(null) : one('2026-11-24 10:00:00')) });
      (openDialog as ReturnType<typeof vi.fn>).mockResolvedValue('/x/big-repo');
      (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { kind: 'code', status: 'indexing' } });
      renderOverlay();
      await openTab('browse');
      expect((await screen.findByTestId('library-mount-big')).getAttribute('data-indexed')).toBe('false');
      // an unindexed mount alone is not polled: only an add that is still indexing starts it
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(reads).toBe(1);
      act(() => { screen.getByTestId('library-add-folder').click(); });
      await waitFor(() => expect(reads).toBe(2));
      expect(screen.getByTestId('library-mount-big').getAttribute('data-indexed')).toBe('false');
      await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
      await waitFor(() => expect(screen.getByTestId('library-mount-big').getAttribute('data-indexed')).toBe('true'));
      const after = reads;
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(reads).toBe(after);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('LibraryOverlay — How it works', () => {
  it('the four steps carry live numbers: written by source, stored size, mounts', async () => {
    mockApi({ mounts: { count: 1, registry_ready: true, mounts: [] } });
    renderOverlay();
    await openTab('how');
    await waitFor(() => expect(screen.getByTestId('library-how-stored').textContent).toMatch(/17/));
    const written = screen.getByTestId('library-how-written').textContent;
    expect(written).toMatch(/WRITTEN · 7 DAYS/);
    expect(written).toMatch(/Swarm distilled 2/);
    expect(written).toMatch(/From chats 2 · Scheduled jobs 2/);
    expect(screen.getByTestId('library-how-stored').textContent).toMatch(/Knowledge\/ 34 MB/);
    expect(screen.getByTestId('library-how-stored').textContent).toMatch(/\+ 1 mount, indexed in place/);
    expect(screen.getByTestId('library-how-indexed').textContent).toMatch(/Full text/);
    expect(screen.getByTestId('library-how-recalled').textContent).toMatch(/Every session/);
  });
});

// Regression guard for the double-/api bug (run_b41d0c2a): the axios instance already
// prepends /api, so every call passes a bare `/library/*` path.
describe('LibraryOverlay — API path convention (no double /api)', () => {
  it('every mount-time api.get uses a bare /library/* path (never /api/library)', async () => {
    renderOverlay();
    await screen.findByTestId('library-overlay');
    await waitFor(() => {
      const libCalls = (api.get as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0])).filter((u) => u.includes('/library/'));
      expect(libCalls.length).toBeGreaterThanOrEqual(4); // native + recent + mounts + health
      for (const url of libCalls) expect(url.startsWith('/library/')).toBe(true);
    });
  });

  it('NO api.get/api.post literal in the component carries a leading /api (covers search + AddFolder POST)', () => {
    const src = readFileSync(resolve(process.cwd(), 'src/components/layout/LibraryOverlay.tsx'), 'utf8');
    expect(src).not.toMatch(/api\.(get|post)\s*(<[^>]*>)?\s*\(\s*[`'"]\/api\/library/);
    expect(src).toMatch(/api\.get\s*<[^>]*>\s*\(\s*`\/library\/search\?q=/);
    expect(src).toMatch(/api\.post\s*<[^>]*>\s*\(\s*\n?\s*`\/library\/mounts\?path=/);
  });
});
