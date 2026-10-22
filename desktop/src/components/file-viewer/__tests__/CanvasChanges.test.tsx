/**
 * CanvasChanges — the Canvas Changes tab: what THIS session changed (nav + detail),
 * and its wiring in FileViewerPanel.
 *
 * What is tested: a repo card names repo, project and branch and lists the session's
 * files with status letter, net +/- and state (the committing commit's short sha on a
 * committed file), then the commits; a file opens the session's net diff (turn-diff
 * from its first start tree to its last end tree); a commit opens its files, marks
 * the session's own, and a one-file commit opens its patch; an empty session says so;
 * a new opened file brings the Artifacts tab forward; the Changes tab hides (never
 * unmounts) the file viewer.
 *
 * Methodology: canvasSessionService + chatService.getTurnDiff mocked; the store is
 * pointed at a session and refreshed per test.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useState } from 'react';

const get = vi.fn();
const commitFiles = vi.fn();
const commitDiff = vi.fn();
const getTurnDiff = vi.fn();
vi.mock('../../../services/canvasSession', async (orig) => {
  const real = await orig<typeof import('../../../services/canvasSession')>();
  real.canvasSessionService.get = (s: string) => get(s);
  real.canvasSessionService.commitFiles = (r: string, s: string) => commitFiles(r, s);
  real.canvasSessionService.commitDiff = (r: string, s: string, p: string) => commitDiff(r, s, p);
  return real;
});
vi.mock('../../../services/chat', () => ({ chatService: { getTurnDiff: (q: unknown) => getTurnDiff(q) } }));

import { ChangesDetail, ChangesNav, diffLineClass, type ChangesSelection } from '../CanvasChanges';
import { refreshCanvasSession, resetCanvasSession, setActiveCanvasSession } from '../../../services/canvasSession';

const SHA = 'a'.repeat(40);
const REPO = {
  root: '/r/swarmai', repo: 'swarmai', project: 'SwarmAI', branch: 'main', upstream: 'origin/main',
  files: [
    { path: 'src/a.ts', status: 'modified', added: 3, deleted: 1, turns: 2, firstAt: 't1', lastAt: 't2',
      start: 's1', end: 'e2', state: 'committed', commit: SHA },
    { path: 'src/new.ts', status: 'added', added: 4, deleted: 0, turns: 1, firstAt: 't2', lastAt: 't2',
      start: 's2', end: 'e2', state: 'uncommitted', commit: null },
  ],
  commits: [{ sha: SHA, short: 'aaaaaaaa', subject: 'feat: one', at: 't3', source: 'session', files: ['src/a.ts'] }],
};

function Tab() {
  const [sel, setSel] = useState<ChangesSelection>(null);
  return <><ChangesNav selected={sel} onSelect={setSel} /><ChangesDetail selected={sel} /></>;
}

async function load(changes: unknown[]) {
  get.mockResolvedValue({ artifacts: [], changes });
  setActiveCanvasSession('s1');
  await act(async () => { await refreshCanvasSession(); });
}

beforeEach(() => {
  resetCanvasSession();
  [get, commitFiles, commitDiff, getTurnDiff].forEach((m) => m.mockReset());
});

describe('Changes tab (this session)', () => {
  it('a repo card lists the session files with net counts and state, then the commits', async () => {
    await load([REPO]);
    render(<Tab />);
    const card = await screen.findByTestId('changes-repo');
    expect(card.textContent).toMatch(/swarmai.*SwarmAI.*main/);
    const files = screen.getAllByTestId('changes-file');
    expect(files[0].textContent).toMatch(/M.*src\/a\.ts.*\+3 −1.*committed aaaaaaaa/);
    expect(files[1].textContent).toMatch(/A.*src\/new\.ts.*\+4.*uncommitted/);
    expect(screen.getByTestId('changes-commit').textContent).toContain('feat: one');
    expect(get).toHaveBeenCalledWith('s1');
  });

  it('a file opens the session net diff (first start → last end)', async () => {
    await load([REPO]);
    getTurnDiff.mockResolvedValue('diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new');
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[0]);
    expect(getTurnDiff).toHaveBeenCalledWith({ gitRoot: '/r/swarmai', start: 's1', end: 'e2', path: 'src/a.ts' });
    const patch = await screen.findByTestId('changes-patch');
    expect(patch.textContent).toContain('+new');
    expect(patch.textContent).not.toContain('diff --git');
    expect(screen.getByTestId('changes-detail').textContent).toMatch(/Changes › swarmai › src\/a\.ts.*net change over 2 turns/);
  });

  it('a pruned diff says so', async () => {
    await load([REPO]);
    getTurnDiff.mockResolvedValue(null);
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[1]);
    expect(await screen.findByText(/no longer available/)).toBeTruthy();
  });

  it('a commit opens its files, marks the session ones, a one-file commit opens its patch', async () => {
    await load([REPO]);
    commitFiles.mockResolvedValue([{ path: 'src/a.ts', insertions: 3, deletions: 1 }]);
    commitDiff.mockResolvedValue({ diff: '@@ -1 +1 @@\n+x', truncated: false });
    render(<Tab />);
    fireEvent.click(await screen.findByTestId('changes-commit'));
    expect(commitFiles).toHaveBeenCalledWith('/r/swarmai', SHA);
    expect((await screen.findByTestId('changes-commit-file')).textContent).toContain('this session');
    expect((await screen.findByTestId('changes-patch')).textContent).toContain('+x');
  });

  it('a session with no code changes says so', async () => {
    await load([]);
    render(<Tab />);
    expect(await screen.findByText(/not changed any code/)).toBeTruthy();
  });

  it('diff line classes', () => {
    expect(diffLineClass('+x')).toContain('git-added');
    expect(diffLineClass('-x')).toContain('git-deleted');
    expect(diffLineClass('+++ b/x')).toContain('text-muted');
    expect(diffLineClass(' x')).toBe('');
  });
});

describe('FileViewerPanel Canvas tabs', () => {
  it('Changes hides the viewer (mounted); a newly opened file brings Artifacts back', async () => {
    vi.resetModules();
    let mounts = 0;
    function ViewerStub() {
      const [n] = useState(() => ++mounts);
      return <div data-testid="file-viewer-stub">{n}</div>;
    }
    vi.doMock('../FileViewer', () => ({ default: ViewerStub }));
    vi.doMock('../CanvasOutputRail', () => ({ CanvasOutputRail: () => <div data-testid="rail-stub" /> }));
    const { default: FileViewerPanel } = await import('../FileViewerPanel');
    const base = {
      onClose: () => {}, pinned: false, onTogglePin: () => {}, muted: false, onToggleMute: () => {},
      referencedFiles: { written: [] } as never, tabScopeKey: 't1',
      collapse: { railed: false, outputsCollapsed: false }, setCollapse: () => {}, isOpen: true, onRevealCanvas: () => {},
    };
    const { rerender } = render(<FileViewerPanel {...base} />);
    await act(async () => { fireEvent.click(screen.getByTestId('canvas-tab-changes')); });
    expect(screen.getByTestId('file-viewer-stub').parentElement?.className).toContain('hidden');
    rerender(<FileViewerPanel {...base} initialFile={{ filePath: 'Knowledge/x.md', fileName: 'x.md' }} />);
    expect(screen.getByTestId('file-viewer-stub').parentElement?.className).not.toContain('hidden');
    expect(screen.getByTestId('canvas-tab-artifacts').getAttribute('aria-selected')).toBe('true');
    expect(mounts).toBe(1);
  });
});
