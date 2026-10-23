/**
 * FileViewerPanel default selection (docs/design/super-canvas.md L3).
 *
 * When the Artifacts view becomes visible on the SAME tab with no open file and a
 * non-empty list, the panel dispatches swarm:open-file for the list's first row
 * (railOrder: newest firstSeen first) with `auto: true` and the tab id. It must not
 * fire on a tab switch (rows can be the previous tab's for one commit), while a
 * file is open, while railed, or when the list is empty.
 *
 * FileViewer + CanvasOutputRail's component are stubbed; railOrder and
 * outputRowOpenDetail are the real ones.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', async (orig) => {
  const real = await orig<typeof import('../CanvasOutputRail')>();
  return { ...real, CanvasOutputRail: () => <div data-testid="rail-stub" /> };
});

const row = (path: string, firstSeen: number, role?: string) => ({
  path, absolutePath: `/ws/${path}`, fileName: path, operation: 'written', firstSeen, count: 1,
  kind: 'content', change: 'new', role,
});
const files = { written: [row('old.md', 1), row('new.md', 3), row('daily.md', 9, 'Activity')] };

const base = {
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  collapse: { railed: false, outputsCollapsed: false },
  setCollapse: vi.fn(),
  onRevealCanvas: vi.fn(),
};

let opens: Array<Record<string, unknown>>;
const onOpen = (e: Event) => opens.push((e as CustomEvent).detail);
beforeEach(() => { opens = []; document.addEventListener('swarm:open-file', onOpen); });
afterEach(() => document.removeEventListener('swarm:open-file', onOpen));

describe('FileViewerPanel — default selection', () => {
  it('a manual open with no file opens the newest non-Activity row, marked auto, stamped with the tab', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { rerender } = render(<FileViewerPanel {...base} referencedFiles={files as any} isOpen={false} />);
    expect(opens).toEqual([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rerender(<FileViewerPanel {...base} referencedFiles={files as any} isOpen />);
    expect(opens).toHaveLength(1);
    expect(opens[0]).toMatchObject({ path: '/ws/new.md', auto: true, tabId: 'tab-1', autoDiff: false });
  });

  it('does not fire on a tab switch, even into an open Canvas with no file', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { rerender } = render(<FileViewerPanel {...base} referencedFiles={files as any} isOpen={false} />);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rerender(<FileViewerPanel {...base} tabScopeKey="tab-2" referencedFiles={files as any} isOpen />);
    expect(opens).toEqual([]);
  });

  it('does not fire when a file is already open, when railed, or when the list is empty', () => {
    const file = { filePath: '/ws/x.md', fileName: 'x.md' };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const a = render(<FileViewerPanel {...base} referencedFiles={files as any} isOpen={false} />);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    a.rerender(<FileViewerPanel {...base} referencedFiles={files as any} initialFile={file as any} isOpen />);
    a.unmount();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const b = render(<FileViewerPanel {...base} referencedFiles={files as any} isOpen={false} />);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    b.rerender(<FileViewerPanel {...base} referencedFiles={files as any} collapse={{ railed: true, outputsCollapsed: false }} isOpen />);
    b.unmount();
    const c = render(<FileViewerPanel {...base} referencedFiles={{ written: [] }} isOpen={false} />);
    c.rerender(<FileViewerPanel {...base} referencedFiles={{ written: [] }} isOpen />);
    expect(opens).toEqual([]);
  });

  it('fires on first mount when Artifacts is already visible with no file', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<FileViewerPanel {...base} referencedFiles={files as any} isOpen />);
    expect(opens).toHaveLength(1);
    expect(opens[0]).toMatchObject({ path: '/ws/new.md', auto: true });
  });

  it('a pinned Canvas never selects by itself', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { rerender } = render(<FileViewerPanel {...base} pinned referencedFiles={files as any} isOpen={false} />);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rerender(<FileViewerPanel {...base} pinned referencedFiles={files as any} isOpen />);
    expect(opens).toEqual([]);
  });

  it('does not fire while the controlled tab is Changes; fires when it switches to Artifacts', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { rerender } = render(<FileViewerPanel {...base} canvasTab="changes" onCanvasTabChange={vi.fn()} referencedFiles={files as any} isOpen />);
    expect(opens).toEqual([]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rerender(<FileViewerPanel {...base} canvasTab="artifacts" onCanvasTabChange={vi.fn()} referencedFiles={files as any} isOpen />);
    expect(opens).toHaveLength(1);
  });
});
