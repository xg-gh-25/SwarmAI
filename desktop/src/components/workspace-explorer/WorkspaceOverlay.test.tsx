/**
 * WorkspaceOverlay (design workspace-working-location.md v5, T7–T9).
 *
 * Methodology: the real overlay, real ExplorerProvider and real services over a
 * mocked `services/api` that records every GET; the SwarmWS tree view itself
 * (VirtualizedTree) is stubbed. Plus static checks on the app shell.
 *
 * Invariants:
 * - T7 nothing is fetched at app start or polled: no ExplorerProvider in the shell,
 *   no setInterval in ExplorerContext; opening fetches ONLY the shown location
 *   (SwarmWS at depth 2); a repo is fetched when its tab is first shown (depth 1);
 *   refresh rescans (fresh=true) that location only.
 * - T8 each location is standalone: tabs carry no stats; a repo's open folders,
 *   filter and show-hidden survive switching away and back; dot entries are hidden
 *   until shown; a repo row has no write action.
 * - T9 a repo that fails to load shows its own error + Retry; SwarmWS still works.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const calls: { url: string; params: Record<string, unknown> }[] = [];
let failRepoTree = false;

const REPO = { key: 'r_abc', label: 'swarmai', kind: 'repo', path: '/Users/me/swarmai' };
const LOCS = [{ key: 'swarmws', label: 'SwarmWS', kind: 'default', path: '/Users/me/.swarm-ai/SwarmWS' }, REPO];
const REPO_TOP = [
  { name: '.kiro', path: '.kiro', type: 'directory', children: null, hidden: true, changed_count: 2 },
  { name: 'backend', path: 'backend', type: 'directory', children: null, changed_count: 1 },
  { name: 'AGENTS.md', path: 'AGENTS.md', type: 'file', children: null, git_status: 'modified' },
];
const BACKEND_KIDS = [{ name: 'main.py', path: 'backend/main.py', type: 'file', children: null }];

vi.mock('../../services/api', () => ({
  default: {
    get: vi.fn(async (url: string, cfg?: { params?: Record<string, unknown> }) => {
      const [path, qs] = url.split('?');
      const params = { ...Object.fromEntries(new URLSearchParams(qs ?? '')), ...(cfg?.params ?? {}) };
      calls.push({ url: path, params });
      if (path === '/workspace/locations') return { data: LOCS, headers: {} };
      if (path === '/workspace/summary') {
        return { data: params.location === 'swarmws' ? { changed: 3 } : { changed: 2, last_commit_at: 1 }, headers: {} };
      }
      if (path === '/workspace/tree' && params.location === REPO.key) {
        if (failRepoTree) throw new Error('boom');
        return { data: REPO_TOP, headers: {} };
      }
      if (path === '/workspace/tree') {
        return { data: [{ name: 'Knowledge', path: 'Knowledge', type: 'directory', children: [
          { name: 'Notes', path: 'Knowledge/Notes', type: 'directory', children: null }] },
          { name: '.artifacts', path: '.artifacts', type: 'directory', children: null }], headers: {} };
      }
      if (path === '/workspace/tree/expand') return { data: BACKEND_KIDS, headers: {} };
      return { data: {}, headers: {} };
    }),
    post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  },
}));
vi.mock('./VirtualizedTree', () => ({ default: () => <div data-testid="virtualized-tree" /> }));
vi.mock('react-virtualized-auto-sizer', () => {
  const A = ({ renderProp, children }: { renderProp?: (s: { height: number; width: number }) => React.ReactNode; children?: (s: { height: number; width: number }) => React.ReactNode }) =>
    <div>{(renderProp ?? children)?.({ height: 400, width: 300 })}</div>;
  return { AutoSizer: A, default: A };
});

import WorkspaceOverlay from './WorkspaceOverlay';
import { flattenRows } from './RepoPane';
import { setLastWorkspaceLocation } from '../layout/overlayQueries';

const SRC = join(__dirname, '..', '..');
const of = (url: string) => calls.filter((c) => c.url === url);

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><WorkspaceOverlay close={() => {}} /></QueryClientProvider>);
}

beforeEach(() => { calls.length = 0; failRepoTree = false; setLastWorkspaceLocation('swarmws'); sessionStorage.clear(); });
afterEach(() => cleanup());

describe('T7 — fetch only what is shown, never poll', () => {
  it('the app shell mounts no ExplorerProvider and ExplorerContext has no timer', () => {
    expect(readFileSync(join(SRC, 'components/layout/ThreeColumnLayout.tsx'), 'utf8')).not.toMatch(/<ExplorerProvider/);
    expect(readFileSync(join(SRC, 'contexts/ExplorerContext.tsx'), 'utf8')).not.toMatch(/setInterval\(/);
  });

  it('opening fetches SwarmWS only; a repo is fetched when its tab is shown', async () => {
    mount();
    await waitFor(() => expect(of('/workspace/tree').length).toBe(1));
    expect(of('/workspace/tree')[0].params).toMatchObject({ depth: '2' });
    await waitFor(() => expect(of('/workspace/summary').map((c) => c.params.location)).toEqual(['swarmws']));
    expect(calls.some((c) => c.params.location === REPO.key)).toBe(false);

    fireEvent.click(await screen.findByTestId(`workspace-tab-${REPO.key}`));
    await screen.findByTestId('repo-row-backend');
    const repoTree = of('/workspace/tree').filter((c) => c.params.location === REPO.key);
    expect(repoTree).toHaveLength(1);
    expect(repoTree[0].params).toMatchObject({ depth: 1 });
    expect(of('/workspace/tree').filter((c) => c.params.location !== REPO.key)).toHaveLength(1);
  });

  it('refresh rescans the shown location only', async () => {
    setLastWorkspaceLocation(REPO.key);
    mount();
    await screen.findByTestId('repo-row-backend');
    const before = calls.length;
    fireEvent.click(screen.getByTestId('workspace-refresh'));
    await waitFor(() => expect(of('/workspace/tree').some((c) => c.params.fresh === true)).toBe(true));
    const after = calls.slice(before);
    expect(after.every((c) => c.params.location === REPO.key)).toBe(true);
    // reopening on a remembered repo never fetched SwarmWS
    expect(calls.some((c) => c.url !== '/workspace/locations' && c.params.location !== REPO.key)).toBe(false);
  });
});

describe('SwarmWS pane (depth-2 first fetch)', () => {
  it('a restored open folder past the first fetch loads by itself', async () => {
    sessionStorage.setItem('swarmws-explorer-state', JSON.stringify({ expandedPaths: ['Knowledge', 'Knowledge/Notes'] }));
    mount();
    await waitFor(() => expect(of('/workspace/tree/expand').map((c) => c.params.path)).toEqual(['Knowledge/Notes']));
  });

  it('has no Working Files card and offers the show-hidden toggle', async () => {
    mount();
    await screen.findByTestId('virtualized-tree');
    expect(screen.queryByTestId('working-files-card')).toBeNull();
    expect(screen.getByTestId('explorer-show-hidden')).toBeTruthy();
  });
});

describe('T8 — each location is standalone', () => {
  it('tabs carry no stats; the header shows only the shown location', async () => {
    setLastWorkspaceLocation(REPO.key);
    mount();
    await screen.findByTestId('repo-row-backend');
    const tabs = screen.getByRole('tablist');
    expect(within(tabs).queryByText(/changed|ago/)).toBeNull();
    const header = screen.getAllByTestId('workspace-location-header')[0];
    expect(within(header).getByText('swarmai')).toBeTruthy();
    expect((await within(header).findByTestId('workspace-changed')).textContent).toBe('2 changed');
  });

  it('a repo keeps its open folders and filter across a switch; dot entries hidden until shown', async () => {
    setLastWorkspaceLocation(REPO.key);
    mount();
    fireEvent.click(await screen.findByTestId('repo-row-backend'));
    await screen.findByTestId('repo-row-backend/main.py');
    expect(screen.queryByTestId('repo-row-.kiro')).toBeNull();
    fireEvent.click(screen.getByTestId('repo-show-hidden'));
    expect(screen.getByTestId('repo-row-.kiro')).toBeTruthy();
    fireEvent.change(screen.getByTestId('repo-filter'), { target: { value: 'main' } });

    fireEvent.click(screen.getByTestId('workspace-tab-swarmws'));
    await screen.findByTestId('swarmws-pane');
    fireEvent.click(screen.getByTestId(`workspace-tab-${REPO.key}`));
    expect((screen.getByTestId('repo-filter') as HTMLInputElement).value).toBe('main');
    expect(screen.getByTestId('repo-row-backend/main.py')).toBeTruthy();
    expect(of('/workspace/tree/expand')).toHaveLength(1);
  });

  it('a repo row offers no write action', async () => {
    setLastWorkspaceLocation(REPO.key);
    mount();
    fireEvent.contextMenu(await screen.findByTestId('repo-row-AGENTS.md'));
    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual(['Open in Canvas', 'Copy path']);
  });
});

describe('T9 — a failing location does not take the others down', () => {
  it('shows the repo error with Retry; SwarmWS still loads', async () => {
    failRepoTree = true;
    setLastWorkspaceLocation(REPO.key);
    mount();
    expect(await screen.findByTestId('repo-load-error')).toBeTruthy();
    fireEvent.click(screen.getByTestId('workspace-tab-swarmws'));
    expect(await screen.findByTestId('swarmws-pane')).toBeTruthy();
    await waitFor(() => expect(of('/workspace/tree').some((c) => c.params.location !== REPO.key)).toBe(true));
  });
});

describe('flattenRows', () => {
  const top = [
    { name: 'src', path: 'src', type: 'directory' as const, children: null },
    { name: '.git2', path: '.git2', type: 'directory' as const, children: null, hidden: true },
  ];
  const kids = new Map([['src', [{ name: 'app.ts', path: 'src/app.ts', type: 'file' as const }]]]);
  it('opens only open folders, drops hidden, filters loaded names with ancestors', () => {
    expect(flattenRows(top, kids, new Set(), new Set(), false, '').map((r) => r.node.path)).toEqual(['src']);
    expect(flattenRows(top, kids, new Set(['src']), new Set(), true, '').map((r) => r.node.path))
      .toEqual(['src', 'src/app.ts', '.git2']);
    expect(flattenRows(top, kids, new Set(), new Set(), false, 'app').map((r) => r.node.path))
      .toEqual(['src', 'src/app.ts']);
  });
});
