/**
 * CanvasChanges — the Canvas Changes tab: the code THIS session's pipeline runs
 * changed (nav + detail), and its wiring in FileViewerPanel.
 *
 * What is tested: a run card names the run, its project and status; per repo it
 * lists the files uncommitted in the run's worktree (status letter, +/-), then the
 * commits it landed (committed / pushed); a worktree released without landing says
 * so; a file opens its worktree diff (turn-diff between the two trees of its row); a
 * commit opens its own files and a one-file commit its patch; a session whose runs
 * changed nothing says so; the tab reads Changes only; a newly opened file brings the
 * Artifacts tab forward; the Changes tab hides (never unmounts) the file viewer.
 *
 * Methodology: canvasSessionService + chatService.getTurnDiff mocked; the store is
 * pointed at a session and refreshed per test.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useState } from 'react';

const artifacts = vi.fn();
const changes = vi.fn();
const commitDiff = vi.fn();
const getTurnDiff = vi.fn();
vi.mock('../../../services/canvasSession', async (orig) => {
  const real = await orig<typeof import('../../../services/canvasSession')>();
  real.canvasSessionService.artifacts = (s: string) => artifacts(s);
  real.canvasSessionService.changes = (s: string) => changes(s);
  real.canvasSessionService.commitDiff = (r: string, s: string, p: string) => commitDiff(r, s, p);
  return real;
});
vi.mock('../../../services/chat', () => ({ chatService: { getTurnDiff: (q: unknown) => getTurnDiff(q) } }));

import { ChangesDetail, ChangesNav, diffLineClass, type ChangesSelection } from '../CanvasChanges';
import { refreshCanvasChanges, resetCanvasSession, setActiveCanvasSession } from '../../../services/canvasSession';

const SHA = 'a'.repeat(40);
const RUN = {
  run: 'run_1', title: 'Make the gate exact', status: 'running', project: 'SwarmAI', createdAt: 't0',
  repos: [{
    root: '/r/swarmai', repo: 'swarmai', worktree: '/wts/run_1/swarmai', branch: 'run/run_1', released: false,
    files: [
      { path: 'src/a.ts', absolutePath: '/wts/run_1/swarmai/src/a.ts', status: 'modified', added: 3, deleted: 1, start: 'b1', end: 't1' },
      { path: 'src/new.ts', absolutePath: '/wts/run_1/swarmai/src/new.ts', status: 'added', added: 4, deleted: 0, start: 'b1', end: 't1' },
    ],
    commits: [{ sha: SHA, short: 'aaaaaaaa', subject: 'feat: one', at: 't3', pushed: false,
      files: [{ path: 'src/a.ts', added: 3, deleted: 1 }] }],
  }],
};
const RELEASED = { ...RUN, run: 'run_2', title: 'Gone', status: 'failed',
  repos: [{ ...RUN.repos[0], worktree: null, branch: null, files: [], commits: [], released: true }] };

function Tab() {
  const [sel, setSel] = useState<ChangesSelection>(null);
  return <><ChangesNav selected={sel} onSelect={setSel} /><ChangesDetail selected={sel} /></>;
}

async function load(runs: unknown[]) {
  changes.mockResolvedValue(runs);
  setActiveCanvasSession('s1');
  await act(async () => { await refreshCanvasChanges(); });
}

beforeEach(() => {
  resetCanvasSession();
  [artifacts, changes, commitDiff, getTurnDiff].forEach((m) => m.mockReset());
});

describe('Changes tab (this session\'s runs)', () => {
  it('a run card lists the worktree files, then the landed commits; a released run says so', async () => {
    await load([RUN, RELEASED]);
    render(<Tab />);
    const [card, gone] = await screen.findAllByTestId('changes-run');
    expect(card.textContent).toMatch(/Make the gate exact.*SwarmAI.*running.*run\/run_1/);
    const files = screen.getAllByTestId('changes-file');
    expect(files[0].textContent).toMatch(/M.*a\.ts.*src.*\+3 −1/);
    expect(files[0].textContent).not.toContain('uncommitted'); // the state lives in the card title
    expect(files[1].textContent).toMatch(/A.*new\.ts.*src.*\+4/);
    // every worktree file row has a copy-path button (§2b)
    expect(files[0].querySelector('[data-testid="changes-row-copy"]')).toBeTruthy();
    expect(screen.getByTestId('changes-commit').textContent).toMatch(/aaaaaaaa.*feat: one.*committed/);
    expect(gone.textContent).toMatch(/Gone.*failed/);
    expect(screen.getByTestId('changes-released').textContent).toContain('Released without landing');
    expect(changes).toHaveBeenCalledWith('s1');
    expect(artifacts).not.toHaveBeenCalled();
  });

  it('a file opens its worktree diff (the two trees of its row)', async () => {
    await load([RUN]);
    getTurnDiff.mockResolvedValue('diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new');
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[0]);
    expect(getTurnDiff).toHaveBeenCalledWith({ gitRoot: '/r/swarmai', start: 'b1', end: 't1', path: 'src/a.ts' });
    const patch = await screen.findByTestId('changes-patch');
    expect(patch.textContent).toContain('+new');
    expect(patch.textContent).not.toContain('diff --git');
    const title = screen.getByTestId('changes-detail-title');
    expect(title.textContent).toMatch(/a\.ts· uncommitted · run\/run_1\+3 −1$/);
    expect(title.getAttribute('title')).toBe('Make the gate exact › swarmai › src/a.ts');
    expect(screen.getAllByTestId('changes-file')[0].className).toContain('canvas-row-sel');
    expect(screen.getAllByTestId('changes-file')[1].className).not.toContain('canvas-row-sel');
  });

  it('a diff no longer there says so', async () => {
    await load([RUN]);
    getTurnDiff.mockResolvedValue(null);
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[1]);
    expect(await screen.findByText(/no longer available/)).toBeTruthy();
  });

  it('a commit opens its own files, a one-file commit its patch', async () => {
    await load([RUN]);
    commitDiff.mockResolvedValue({ diff: '@@ -1 +1 @@\n+x', truncated: false });
    render(<Tab />);
    fireEvent.click(await screen.findByTestId('changes-commit'));
    expect((await screen.findByTestId('changes-commit-file')).textContent).toMatch(/src\/a\.ts.*\+3 −1/);
    expect((await screen.findByTestId('changes-patch')).textContent).toContain('+x');
    expect(commitDiff).toHaveBeenCalledWith('/r/swarmai', SHA, 'src/a.ts');
  });

  it('a session whose runs changed nothing says so', async () => {
    await load([]);
    render(<Tab />);
    expect(await screen.findByText(/No pipeline run of this session has changed code/)).toBeTruthy();
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
      onClose: () => {}, pinned: false, onTogglePin: () => {},
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
