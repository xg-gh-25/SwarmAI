/**
 * CanvasChanges — the Canvas Changes tab: what THIS session's transcript says it
 * changed (nav + detail), and its wiring in FileViewerPanel.
 *
 * What is tested: "Files changed" lists each turn's card, newest first, as recorded
 * (status letter, name, directory, +/−), "+N more files" for a card's omitted rows,
 * "N older turns" past the cap, and says when earlier messages were not scanned; a
 * file opens its diff via tree-diff (its row's root + two trees); "Landed" lists each
 * landing (branch → destination, commit count, on / not on) and opens its commits and
 * files, a file's diff via tree-diff with the landing's two trees; what could not be
 * read about landings is said; an empty session says so; the tab reads
 * Changes only; a newly opened file brings the Artifacts tab forward; the Changes
 * tab hides (never unmounts) the file viewer.
 *
 * Methodology: canvasSessionService mocked; the store is pointed at a session and
 * refreshed per test.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { useState } from 'react';

const artifacts = vi.fn();
const changes = vi.fn();
const treeDiff = vi.fn();
vi.mock('../../../services/canvasSession', async (orig) => {
  const real = await orig<typeof import('../../../services/canvasSession')>();
  real.canvasSessionService.artifacts = (s: string) => artifacts(s);
  real.canvasSessionService.changes = (s: string) => changes(s);
  real.canvasSessionService.treeDiff = (r: string, a: string, b: string, p: string) => treeDiff(r, a, b, p);
  return real;
});

import { ChangesCount, ChangesDetail, ChangesNav, ChangesRefresh, diffLineClass, type ChangesSelection } from '../CanvasChanges';
import { refreshCanvasChanges, resetCanvasSession, setActiveCanvasSession, setCanvasStreaming } from '../../../services/canvasSession';

const SHA = 'a'.repeat(40);
const file = (path: string, over: Record<string, unknown> = {}) => ({
  path, absolutePath: `/r/swarmai/${path}`, repo: 'swarmai', status: 'modified', added: 3, deleted: 1,
  binary: false, diffable: true, gitRoot: '/r/swarmai', start: 's1', end: 'e1', ...over,
});
const CHANGES = {
  turns: [
    { id: 'm2:0', at: '2026-10-24T20:31:00Z', files: [file('src/a.ts'), file('src/new.ts', { status: 'added', added: 4, deleted: 0 })], omitted: 2 },
    { id: 'm1:0', at: '2026-10-24T19:05:00Z', files: [file('src/a.ts', { start: 's0', end: 'e0' })], omitted: 0 },
  ],
  olderTurns: 3,
  landed: [
    { repo: 'swarmai', gitRoot: '/r/swarmai', dest: 'main', branch: 'session/abc-fix', turn: 'none',
      onto: SHA, new: 'b'.repeat(40), count: 2, state: 'on',
      subjects: [{ sha: 'bbbbbbb', subject: 'fix: two' }, { sha: 'ccccccc', subject: 'feat: one' }],
      files: [file('src/l.ts', { start: 'T0', end: 'N0' })] },
    { repo: 'swarmai', gitRoot: '/r/swarmai', dest: 'main', branch: 'session/abc-old', turn: 'none',
      onto: 'c'.repeat(40), new: 'd'.repeat(40), count: 1, state: 'off',
      subjects: [{ sha: 'ddddddd', subject: 'old' }], files: [], omitted: 5 },
  ],
  landedTruncated: true,
  unverified: 1,
  unknown: [{ repo: 'other', reason: 'no-reflog' }],
  scannedAll: false,
};

function Tab() {
  const [sel, setSel] = useState<ChangesSelection>(null);
  return <><ChangesCount /><ChangesNav selected={sel} onSelect={setSel} /><ChangesDetail selected={sel} /></>;
}

async function load(c: unknown) {
  changes.mockResolvedValue(c);
  setActiveCanvasSession('s1');
  await act(async () => { await refreshCanvasChanges(); });
}

beforeEach(() => {
  resetCanvasSession();
  [artifacts, changes, treeDiff].forEach((m) => m.mockReset());
});

describe('Changes tab (this session\'s transcript)', () => {
  it('lists each turn\'s card newest first, as recorded, with what was left off', async () => {
    await load(CHANGES);
    render(<Tab />);
    const turns = await screen.findAllByTestId('changes-turn');
    expect(turns).toHaveLength(2);
    const files = screen.getAllByTestId('changes-file');
    expect(files.map((f) => f.textContent)).toEqual([
      expect.stringMatching(/M.*a\.ts.*src.*\+3 −1/),
      expect.stringMatching(/A.*new\.ts.*src.*\+4/),
      expect.stringMatching(/M.*a\.ts/),
    ]);
    expect(files[0].querySelector('[data-testid="changes-row-copy"]')).toBeTruthy();
    expect(turns[0].textContent).toContain('2 more files');
    expect(screen.getByTestId('changes-older').textContent).toContain('3 older turns');
    expect(screen.getByTestId('changes-not-scanned')).toBeTruthy();
    // the badge counts distinct files: a.ts twice + new.ts once
    expect(screen.getByTestId('changes-count').textContent).toBe('2');
    expect(changes).toHaveBeenCalledWith('s1');
    expect(artifacts).not.toHaveBeenCalled();
  });

  it('a file opens its diff via tree-diff with its own row\'s root and trees', async () => {
    await load(CHANGES);
    treeDiff.mockResolvedValue('diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new');
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[2]);
    expect(treeDiff).toHaveBeenCalledWith('/r/swarmai', 's0', 'e0', 'src/a.ts');
    const patch = await screen.findByTestId('changes-patch');
    expect(patch.textContent).toContain('+new');
    expect(patch.textContent).not.toContain('diff --git');
    expect(screen.getAllByTestId('changes-file')[2].className).toContain('canvas-row-sel');
    expect(screen.getAllByTestId('changes-file')[0].className).not.toContain('canvas-row-sel');
  });

  it('a diff no longer there says so', async () => {
    await load(CHANGES);
    treeDiff.mockResolvedValue(null);
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[1]);
    expect(await screen.findByText(/no longer available/)).toBeTruthy();
  });

  it('a binary or file-only row asks for no diff', async () => {
    await load({ ...CHANGES, turns: [{ id: 'm9:0', at: 't', omitted: 0, files: [
      file('logo.png', { binary: true, added: null, deleted: null }), file('huge.json', { diffable: false })] }] });
    render(<Tab />);
    fireEvent.click((await screen.findAllByTestId('changes-file'))[0]);
    expect((await screen.findByTestId('changes-no-diff')).textContent).toContain('binary');
    fireEvent.click(screen.getAllByTestId('changes-file')[1]);
    expect((await screen.findByTestId('changes-no-diff')).textContent).toContain('Too large');
    expect(treeDiff).not.toHaveBeenCalled();
  });

  it('while the turn streams: no Changes request, refresh disabled; at its end exactly one', async () => {
    await load(CHANGES);
    changes.mockClear();
    await act(async () => { await setCanvasStreaming('s1', true); });
    render(<><ChangesRefresh /><Tab /></>);           // opening the tab mid-turn reads nothing
    const btn = screen.getByTestId('canvas-changes-refresh') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    await act(async () => { await refreshCanvasChanges(); await refreshCanvasChanges('s1'); });
    expect(changes).not.toHaveBeenCalled();
    await act(async () => { await setCanvasStreaming('s1', false); });
    expect(changes).toHaveBeenCalledTimes(1);
    expect(btn.disabled).toBe(false);
    await act(async () => { await setCanvasStreaming('s1', false); });   // no edge: nothing
    expect(changes).toHaveBeenCalledTimes(1);
  });

  it('landed: branch → destination, count, on / not on; a landing opens its commits and files', async () => {
    await load(CHANGES);
    treeDiff.mockResolvedValue('@@ -1 +1 @@\n+landed');
    render(<Tab />);
    const rows = await screen.findAllByTestId('changes-landing');
    expect(rows[0].textContent).toMatch(/session\/abc-fix.*main.*2 commits.*on main/);
    expect(rows[1].textContent).toMatch(/session\/abc-old.*1 commit.*not on main/);
    fireEvent.click(rows[0]);
    const detail = await screen.findByTestId('changes-detail');
    expect(detail.textContent).toContain('fix: two');
    // a one-file landing opens its file
    expect(await screen.findByTestId('changes-landing-file')).toBeTruthy();
    expect((await screen.findByTestId('changes-patch')).textContent).toContain('+landed');
    expect(treeDiff).toHaveBeenCalledWith('/r/swarmai', 'T0', 'N0', 'src/l.ts');
    fireEvent.click(rows[1]);
    expect(await screen.findByText(/5 more files/)).toBeTruthy();
  });

  it('what could not be read about landings is said', async () => {
    await load(CHANGES);
    render(<Tab />);
    const notes = (await screen.findAllByTestId('changes-landed-note')).map((n) => n.textContent).join(' ');
    expect(notes).toContain('other');
    expect(notes).toContain('no-reflog');
    expect(notes).toContain('More landings');
    expect(notes).toContain('1 landing record');
  });

  it('a session with only landings is not empty', async () => {
    await load({ turns: [], olderTurns: 0, scannedAll: true, landed: CHANGES.landed.slice(0, 1),
      landedTruncated: false, unverified: 0, unknown: [] });
    render(<Tab />);
    expect(await screen.findByTestId('changes-landing')).toBeTruthy();
    expect(screen.queryByText(/No code changes/)).toBeNull();
  });

  it('a session with no changes says so', async () => {
    await load({ turns: [], olderTurns: 0, scannedAll: true, landed: [], landedTruncated: false, unverified: 0, unknown: [] });
    render(<Tab />);
    expect(await screen.findByText(/No code changes in this session yet/)).toBeTruthy();
    expect(screen.queryByTestId('changes-count')).toBeNull();
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
