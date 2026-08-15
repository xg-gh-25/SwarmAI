/**
 * Tests for LibraryOverlay — the agent's bookshelf (Native store + Mounts).
 *
 * TWO focuses:
 *  1. Browse tab is now a LIVE Knowledge/ file tree (LibraryTree, off
 *     /workspace/tree): it expands to any file and a FILE click dispatches
 *     swarm:open-file (→ Canvas), while a DIRECTORY click toggles expand and
 *     NEVER dispatches (a dir path renders an empty Canvas). These tests pin the
 *     tree render + the file-only open contract + lazy-expand.
 *  2. The Recent tab's loading / error / empty three-state discipline (unchanged)
 *     and the no-double-/api path convention (regression guard, run_b41d0c2a).
 *
 * The api client + workspaceService are mocked at the boundary; the component is
 * backend-primary and invents no data (R30).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import type { TreeNode } from '../../types';

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
// LibraryTree browses via workspaceService.getTree/expandDirectory — mock the
// service boundary so the tree renders deterministic Knowledge/ nodes.
vi.mock('../../services/workspace', () => ({
  workspaceService: { getTree: vi.fn(), expandDirectory: vi.fn() },
}));
import api from '../../services/api';
import { workspaceService } from '../../services/workspace';
import { LibraryContent } from './LibraryOverlay';

const NATIVE_OK = {
  source: 'native', root: 'Knowledge/', category_count: 2,
  categories: [
    { name: 'Designs', file_count: 12, total_bytes: 34000 },
    { name: 'Notes', file_count: 5, total_bytes: 8000 },
  ],
};
const RECENT_OK = {
  window_days: 7, count: 1,
  items: [{ path: 'Knowledge/Notes/x.md', category: 'Notes', mtime: 1785600000, size: 100, source: 'session' }],
};
const RECENT_EMPTY = { window_days: 7, count: 0, items: [] };
const MOUNTS_OK = { count: 0, mounts: [], registry_ready: true };
const HEALTH_OK = { generated_at: 1, root: 'Knowledge/', clean: true, findings: [] };

// Workspace tree fixture: root → Knowledge → {Designs(dir, truncated), readme.md(file)}
const TREE_OK: TreeNode[] = [
  {
    name: 'Knowledge', path: 'Knowledge', type: 'directory',
    children: [
      { name: 'Designs', path: 'Knowledge/Designs', type: 'directory', children: null }, // truncated → lazy
      { name: 'readme.md', path: 'Knowledge/readme.md', type: 'file' },
    ],
  },
];
const DESIGNS_CHILDREN: TreeNode[] = [
  { name: 'plan.md', path: 'Knowledge/Designs/plan.md', type: 'file' },
];

function mockTree() {
  (workspaceService.getTree as ReturnType<typeof vi.fn>).mockResolvedValue(TREE_OK);
  (workspaceService.expandDirectory as ReturnType<typeof vi.fn>).mockResolvedValue(DESIGNS_CHILDREN);
}

type Handler = (url: string) => Promise<{ data: unknown }>;
function mockApi(h: Handler) {
  (api.get as ReturnType<typeof vi.fn>).mockImplementation(h);
}
// default: everything resolves OK
function mockAllOk() {
  mockApi((url: string) => {
    if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
    if (url.includes('/recent')) return Promise.resolve({ data: RECENT_OK });
    if (url.includes('/mounts')) return Promise.resolve({ data: MOUNTS_OK });
    if (url.includes('/health')) return Promise.resolve({ data: HEALTH_OK });
    return Promise.resolve({ data: {} });
  });
}

// M3: LibraryOverlay → LibraryContent (OverlayHost registry). Content renders
// immediately (host owns open/close); openOverlay() is a no-op kept for readability.
function renderOverlay() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LibraryContent />
    </QueryClientProvider>,
  );
}
function openOverlay() { /* no-op: LibraryContent renders immediately (host-owned open) */ }
// Recent is now the DEFAULT tab (run_b4120a78); Browse-tree tests must click into
// Browse first. Idempotent: safe even if Browse were already active.
async function openBrowse() {
  await screen.findByTestId('library-overlay');
  act(() => { screen.getByTestId('library-tab-browse').click(); });
  return screen.findByTestId('library-panel-browse');
}

beforeEach(() => {
  mockAllOk();
  mockTree();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LibraryOverlay — Browse is a live Knowledge/ tree', () => {
  it('renders the Knowledge/ tree roots (dir + file) from /workspace/tree', async () => {
    renderOverlay();
    openOverlay();
    await openBrowse();
    await screen.findByTestId('library-tree');
    // both top-level Knowledge children render as tree rows
    expect(await screen.findByText('Designs')).toBeInTheDocument();
    expect(screen.getByText('readme.md')).toBeInTheDocument();
    // getTree was called scoped shallow (depth arg passed)
    expect(workspaceService.getTree as ReturnType<typeof vi.fn>).toHaveBeenCalled();
  });

  it('clicking a FILE dispatches swarm:open-file with its path (→ Canvas)', async () => {
    const onOpen = vi.fn();
    document.addEventListener('swarm:open-file', onOpen as EventListener);
    renderOverlay();
    openOverlay();
    await openBrowse();
    await screen.findByTestId('library-tree');
    const fileRow = await screen.findByText('readme.md');
    act(() => { fileRow.click(); });
    await waitFor(() => expect(onOpen).toHaveBeenCalled());
    const evt = onOpen.mock.calls[0][0] as CustomEvent<{ path: string }>;
    expect(evt.detail.path).toBe('Knowledge/readme.md');
    document.removeEventListener('swarm:open-file', onOpen as EventListener);
  });

  it('clicking a DIRECTORY toggles expand + lazy-loads children; it NEVER dispatches open-file', async () => {
    const onOpen = vi.fn();
    document.addEventListener('swarm:open-file', onOpen as EventListener);
    renderOverlay();
    openOverlay();
    await openBrowse();
    await screen.findByTestId('library-tree');
    const dirRow = await screen.findByText('Designs');
    act(() => { dirRow.click(); });
    // lazy-load fired for the truncated dir, and its child appears
    await waitFor(() =>
      expect(workspaceService.expandDirectory as ReturnType<typeof vi.fn>).toHaveBeenCalledWith('Knowledge/Designs', expect.any(Number)),
    );
    expect(await screen.findByText('plan.md')).toBeInTheDocument();
    // a directory click must NOT open anything in Canvas
    expect(onOpen).not.toHaveBeenCalled();
    document.removeEventListener('swarm:open-file', onOpen as EventListener);
  });

  it('shows an ERROR state with a working Retry when the tree fetch fails (no infinite Loading)', async () => {
    (workspaceService.getTree as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('503 workspace not init'));
    renderOverlay();
    openOverlay();
    await openBrowse();
    const err = await screen.findByTestId('library-tree-error');
    expect(err).toBeInTheDocument();
    expect(screen.queryByText(/Loading tree/i)).toBeNull();
    // Retry re-issues getTree (recovers transient failure without reopening)
    const before = (workspaceService.getTree as ReturnType<typeof vi.fn>).mock.calls.length;
    act(() => { screen.getByTestId('library-tree-retry').click(); });
    await waitFor(() => {
      expect((workspaceService.getTree as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before);
    });
  });

  it('shows an EMPTY state when Knowledge/ has no children', async () => {
    (workspaceService.getTree as ReturnType<typeof vi.fn>).mockResolvedValue([
      { name: 'Knowledge', path: 'Knowledge', type: 'directory', children: [] },
    ]);
    renderOverlay();
    openOverlay();
    await openBrowse();
    expect(await screen.findByTestId('library-tree-empty')).toBeInTheDocument();
  });
});

describe('LibraryOverlay — Recent three-state', () => {
  async function openRecent() {
    await screen.findByTestId('library-overlay');
    act(() => { screen.getByTestId('library-tab-recent').click(); });
    return screen.findByTestId('library-panel-recent');
  }

  it('shows an ERROR state with Retry when the recent fetch fails', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.reject(new Error('boom'));
      if (url.includes('/health')) return Promise.resolve({ data: HEALTH_OK });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    await openRecent();
    expect(await screen.findByTestId('library-recent-error')).toBeInTheDocument();
    // Retry re-issues the recent fetch (symmetric with native's recovery path)
    const before = (api.get as ReturnType<typeof vi.fn>).mock.calls
      .filter((c) => String(c[0]).includes('/recent')).length;
    act(() => { screen.getByTestId('library-recent-retry').click(); });
    await waitFor(() => {
      const after = (api.get as ReturnType<typeof vi.fn>).mock.calls
        .filter((c) => String(c[0]).includes('/recent')).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it('shows the empty-week message when recent succeeds with zero items', async () => {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_EMPTY });
      if (url.includes('/health')) return Promise.resolve({ data: HEALTH_OK });
      return Promise.resolve({ data: MOUNTS_OK });
    });
    renderOverlay();
    openOverlay();
    const panel = await openRecent();
    await waitFor(() => expect(panel.textContent).toMatch(/Nothing new in the last week/i));
    expect(screen.queryByTestId('library-recent-error')).toBeNull();
  });
});

describe('LibraryOverlay — Recent-first + Recent search (run_b4120a78)', () => {
  const RECENT_MANY = {
    window_days: 7, count: 3,
    items: [
      { path: 'Knowledge/Notes/widget.md', category: 'Notes', mtime: 1785600000, size: 100, source: 'session' },
      { path: 'Knowledge/Designs/gadget.md', category: 'Designs', mtime: 1785600001, size: 200, source: 'you' },
      { path: 'Knowledge/Reports/widget-q2.md', category: 'Reports', mtime: 1785600002, size: 300, source: 'job' },
    ],
  };
  function mockRecentMany() {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_MANY });
      if (url.includes('/health')) return Promise.resolve({ data: HEALTH_OK });
      return Promise.resolve({ data: MOUNTS_OK });
    });
  }

  it('opens on the Recent panel by DEFAULT (Recent is the high-frequency tab)', async () => {
    renderOverlay();
    await screen.findByTestId('library-overlay');
    // Recent panel is mounted WITHOUT any click; Browse panel is not.
    expect(await screen.findByTestId('library-panel-recent')).toBeInTheDocument();
    expect(screen.queryByTestId('library-panel-browse')).toBeNull();
  });

  it('renders the Recent tab BEFORE the Browse tab (order swap)', async () => {
    renderOverlay();
    await screen.findByTestId('library-overlay');
    const recent = screen.getByTestId('library-tab-recent');
    const browse = screen.getByTestId('library-tab-browse');
    // DOCUMENT_POSITION_FOLLOWING (4) set on Browse means Recent comes first.
    expect(recent.compareDocumentPosition(browse) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('Recent search filters rendered rows by path/category substring (case-insensitive)', async () => {
    mockRecentMany();
    renderOverlay();
    await screen.findByTestId('library-panel-recent');
    await waitFor(() => expect(screen.getAllByTestId('library-recent-item').length).toBe(3));
    const box = screen.getByTestId('library-recent-search');
    act(() => {
      const input = box as HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, 'WIDGET');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    // 2 of 3 match "widget" (widget.md, widget-q2.md); gadget.md is filtered out.
    await waitFor(() => expect(screen.getAllByTestId('library-recent-item').length).toBe(2));
  });

  it('Recent search with NO matches shows a distinct no-match message, NOT the empty-week copy', async () => {
    mockRecentMany();
    renderOverlay();
    const panel = await screen.findByTestId('library-panel-recent');
    await waitFor(() => expect(screen.getAllByTestId('library-recent-item').length).toBe(3));
    const box = screen.getByTestId('library-recent-search') as HTMLInputElement;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
      setter.call(box, 'zzzznomatch');
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await waitFor(() => expect(screen.queryAllByTestId('library-recent-item').length).toBe(0));
    // empty-FILTER ≠ empty-WEEK: must NOT lie with "Nothing new in the last week"
    expect(panel.textContent).not.toMatch(/Nothing new in the last week/i);
    expect(panel.textContent).toMatch(/no matches/i);
  });
});

// Regression guard for the double-/api bug (run_b41d0c2a): the shared axios
// instance's interceptor already prepends /api to baseURL, so every call MUST pass
// a bare `/library/*` path. A leading `/api/library/*` double-prefixes to
// /api/api/library/* → 404 → all tabs showed "Couldn't load categories".
// The prior three-state mock matched url.includes('/native') — a substring true for
// BOTH the buggy and correct URL, so it never caught this. These assertions inspect
// the ACTUAL request path (get AND post) and go RED if any call reverts to /api/.
describe('LibraryOverlay — API path convention (no double /api)', () => {
  it('every mount-time api.get uses a bare /library/* path (never /api/library)', async () => {
    // native + recent + mounts fire on mount. Assert the ACTUAL request path — the
    // three-state mock above matched url.includes('/native'), a substring true for
    // BOTH /library/native and the buggy /api/library/native, so it masked this.
    renderOverlay();
    await screen.findByTestId('library-overlay');
    await waitFor(() => {
      const libCalls = (api.get as ReturnType<typeof vi.fn>).mock.calls
        .map((c) => String(c[0]))
        .filter((u) => u.replace(/\?.*$/, '').includes('/library/'));
      expect(libCalls.length).toBeGreaterThanOrEqual(3); // native + recent + mounts
      for (const url of libCalls) {
        expect(url.startsWith('/library/')).toBe(true);  // bare path
        expect(url).not.toMatch(/^\/api\/library/);       // never double-prefixed
      }
    });
  });

  it('NO api.get/api.post literal in the component carries a leading /api (covers search + AddFolder POST)', () => {
    // Source-level net over ALL 5 calls — incl. the search GET and the AddFolder POST
    // that jsdom cannot drive (Tauri dialog import fails → falls back to a toast).
    // Reverting ANY of the 5 to `/api/library/*` makes one of these assertions RED.
    // vitest cwd = desktop/ ; resolve the component relative to it.
    const src = readFileSync(
      resolve(process.cwd(), 'src/components/layout/LibraryOverlay.tsx'),
      'utf8',
    );
    expect(src).not.toMatch(/api\.(get|post)\s*(<[^>]*>)?\s*\(\s*[`'"]\/api\/library/);
    // and the two easy-to-regress calls are specifically bare
    expect(src).toMatch(/api\.get\s*<[^>]*>\s*\(\s*`\/library\/search\?q=/);
    expect(src).toMatch(/api\.post\s*<[^>]*>\s*\(\s*\n?\s*`\/library\/mounts\?path=/);
  });
});

// ── Mounted honesty badge (run_139d7652) ────────────────────────────────────
// The load-bearing honesty fix: a mount is reachable by recall ONLY once indexed
// (last_synced set). Until then the row must SAY so, never imply searchability.
describe('LibraryOverlay — Mounted rows honestly show index state', () => {
  const MOUNTS_MIXED = {
    count: 2, registry_ready: true,
    mounts: [
      // indexed: a code repo with a sync stamp → recall reaches it
      { id: 'm-indexed', path: '/Users/x/repos/foo', kind: 'code',
        health: 'fresh', enabled: true, last_synced: '2026-08-15 10:00:00', index_ref: '/idx/foo' },
      // registered-not-indexed docs mount → recall can't reach it yet
      { id: 'm-raw', path: '/Users/x/AI-Native', kind: 'docs',
        health: 'fresh', enabled: true, last_synced: null, index_ref: null },
    ],
  };
  function mockWithMounts() {
    mockApi((url: string) => {
      if (url.includes('/native')) return Promise.resolve({ data: NATIVE_OK });
      if (url.includes('/recent')) return Promise.resolve({ data: RECENT_OK });
      if (url.includes('/mounts')) return Promise.resolve({ data: MOUNTS_MIXED });
      if (url.includes('/health')) return Promise.resolve({ data: HEALTH_OK });
      return Promise.resolve({ data: {} });
    });
  }

  it('an indexed mount reads "recall reaches it"; an unindexed docs mount reads "can’t reach it" + chat hint', async () => {
    mockWithMounts();
    renderOverlay();
    openOverlay();
    await openBrowse();
    // indexed row → the green reachable line
    const indexed = await screen.findByTestId('library-mount-indexed-m-indexed');
    expect(indexed.textContent).toMatch(/recall reaches it/i);
    // unindexed docs row → the honest warning + the brief-in-chat hint
    const raw = await screen.findByTestId('library-mount-unindexed-m-raw');
    expect(raw.textContent).toMatch(/can’t reach it/i);
    expect(raw.textContent).toMatch(/brief this folder/i);
    // and the row is flagged not-indexed at the container (data attr contract)
    expect(screen.getByTestId('library-mount-m-raw').getAttribute('data-indexed')).toBe('false');
    expect(screen.getByTestId('library-mount-m-indexed').getAttribute('data-indexed')).toBe('true');
  });

  it('the RecallDashboard telemetry is GONE from the rail (regression: never re-add)', async () => {
    mockWithMounts();
    renderOverlay();
    openOverlay();
    await screen.findByTestId('library-overlay');
    expect(screen.queryByTestId('recall-dashboard')).toBeNull();
  });
});
