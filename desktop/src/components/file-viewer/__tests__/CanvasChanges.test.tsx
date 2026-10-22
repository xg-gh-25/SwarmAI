/**
 * CanvasChanges — the Canvas Changes tab (nav + detail), and its wiring in
 * FileViewerPanel.
 *
 * What is tested: a repo card shows name, project, branch and ahead / uncommitted /
 * worktrees counts; no upstream reads "no upstream", a git error is shown on its
 * card; clicking a commit opens its files in the detail card, a single-file commit
 * opens its patch at once and the patch lines carry add/del classes; the
 * uncommitted and worktree counts open their lists; the panel's Changes tab hides
 * (never unmounts) the file viewer and the Artifacts tab brings it back.
 *
 * Methodology: canvasChangesService mocked; the module store reset per test.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useState } from 'react';

const repos = vi.fn();
const commitFiles = vi.fn();
const commitDiff = vi.fn();
vi.mock('../../../services/canvasChanges', () => ({
  canvasChangesService: {
    repos: () => repos(),
    commitFiles: (r: string, s: string) => commitFiles(r, s),
    commitDiff: (r: string, s: string, p: string) => commitDiff(r, s, p),
  },
}));

import { ChangesDetail, ChangesNav, diffLineClass, resetRepoChanges, type ChangesSelection } from '../CanvasChanges';

const REPO = {
  name: 'swarmai', project: 'SwarmAI', root: '/r/swarmai', branch: 'main', upstream: 'origin/main',
  ahead: 2, dirty_count: 1, dirty: [{ path: 'a.md', status: '?' }],
  worktrees: [{ path: '/s/wt', branch: 'kiro/x', prunable: true, detached: false }],
  commits: [
    { sha: 'a'.repeat(40), short: 'aaaaaaaa', subject: 'feat: one', at: '2026-10-22T10:00:00+08:00', files: 1, insertions: 3, deletions: 1 },
    { sha: 'b'.repeat(40), short: 'bbbbbbbb', subject: 'fix: two', at: '2026-10-22T09:00:00+08:00', files: 2, insertions: 5, deletions: 0 },
  ],
};

function Tab() {
  const [sel, setSel] = useState<ChangesSelection>(null);
  return <><ChangesNav selected={sel} onSelect={setSel} /><ChangesDetail selected={sel} /></>;
}

beforeEach(() => {
  resetRepoChanges();
  repos.mockReset(); commitFiles.mockReset(); commitDiff.mockReset();
});

describe('Changes tab', () => {
  it('a repo card names the repo, project and branch, with its counts', async () => {
    repos.mockResolvedValue([REPO, { ...REPO, name: 'other', project: null, root: '/r/o', ahead: null, commits: [], dirty_count: 0, worktrees: [] }]);
    render(<Tab />);
    const cards = await screen.findAllByTestId('changes-repo');
    expect(cards[0].textContent).toMatch(/swarmai.*SwarmAI.*main/);
    expect(cards[0].textContent).toMatch(/2ahead.*1uncommitted.*1worktrees · 1 stale/);
    expect(cards[0].getAttribute('title') ?? cards[0].querySelector('[title]')?.getAttribute('title')).toBe('/r/swarmai');
    expect(cards[1].textContent).toContain('no upstream');
  });

  it('a git error is shown on its card', async () => {
    repos.mockResolvedValue([{ name: 'x', project: null, root: '/r/x', error: 'git status: boom' }]);
    render(<Tab />);
    expect((await screen.findByTestId('changes-repo')).textContent).toContain('git status: boom');
  });

  it('a commit opens its files; a one-file commit opens its patch with add/del lines', async () => {
    repos.mockResolvedValue([REPO]);
    commitFiles.mockResolvedValue([{ path: 'src/a.ts', insertions: 3, deletions: 1 }]);
    commitDiff.mockResolvedValue({ diff: 'diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n ctx', truncated: false });
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-commit'))[0]);
    expect(commitFiles).toHaveBeenCalledWith('/r/swarmai', 'a'.repeat(40));
    expect((await screen.findByTestId('changes-detail')).textContent).toContain('Changes › swarmai › aaaaaaaa');
    const patch = await screen.findByTestId('changes-patch');
    expect(patch.textContent).toContain('+new');
    expect(patch.textContent).not.toContain('diff --git');
    expect(commitDiff).toHaveBeenCalledWith('/r/swarmai', 'a'.repeat(40), 'src/a.ts');
  });

  it('the uncommitted and worktree counts open their lists', async () => {
    repos.mockResolvedValue([REPO]);
    render(<Tab />);
    await screen.findAllByTestId('changes-repo');
    fireEvent.click(screen.getByText('uncommitted'));
    expect(screen.getByTestId('changes-detail').textContent).toContain('a.md');
    fireEvent.click(screen.getByText(/worktrees/));
    expect(screen.getByTestId('changes-detail').textContent).toMatch(/kiro\/x.*stale/);
  });

  it('diff line classes', () => {
    expect(diffLineClass('+x')).toContain('git-added');
    expect(diffLineClass('-x')).toContain('git-deleted');
    expect(diffLineClass('+++ b/x')).toContain('text-muted');
    expect(diffLineClass(' x')).toBe('');
  });
});

describe('FileViewerPanel Canvas tabs', () => {
  it('Changes hides the file viewer without unmounting it; Artifacts brings it back', async () => {
    vi.resetModules();
    let mounts = 0;
    function ViewerStub() {
      const [n] = useState(() => ++mounts);
      return <div data-testid="file-viewer-stub">{n}</div>;
    }
    vi.doMock('../FileViewer', () => ({ default: ViewerStub }));
    vi.doMock('../CanvasOutputRail', () => ({ CanvasOutputRail: () => <div data-testid="rail-stub" /> }));
    repos.mockResolvedValue([REPO]);
    const { default: FileViewerPanel } = await import('../FileViewerPanel');
    render(<FileViewerPanel onClose={() => {}} pinned={false} onTogglePin={() => {}} muted={false}
      onToggleMute={() => {}} referencedFiles={{ written: [] } as never} tabScopeKey="t1"
      collapse={{ railed: false, outputsCollapsed: false }} setCollapse={() => {}} isOpen onRevealCanvas={() => {}} />);
    const viewer = screen.getByTestId('file-viewer-stub');
    await act(async () => { fireEvent.click(screen.getByTestId('canvas-tab-changes')); });
    expect(viewer.parentElement?.className).toContain('hidden');
    expect(await screen.findByText(/Select a commit/)).toBeTruthy();
    expect(await screen.findAllByTestId('changes-repo')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('canvas-tab-artifacts'));
    expect(screen.getByTestId('file-viewer-stub').parentElement?.className).not.toContain('hidden');
    expect(mounts).toBe(1);
  });
});
