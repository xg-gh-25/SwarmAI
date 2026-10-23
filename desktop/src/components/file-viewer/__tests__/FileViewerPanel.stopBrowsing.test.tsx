/**
 * FileViewerPanel — Super Canvas commit 3 wiring (docs/design/super-canvas.md).
 *
 * - List folded on Artifacts: the header names the shown file (type badge + name)
 *   and shows a dot while FileViewer reports unsaved edits (onDirtyChange).
 * - The Browsing row's × ("stop browsing"): with outputs, opens the first one (the
 *   same open a row click sends, NOT marked auto); with none, bumps FileViewer's
 *   closeRequest so the close runs through FileViewer's unsaved-edits guard.
 *
 * FileViewer and the rail component are stubbed to capture the props the panel
 * passes them; railOrder / outputRowOpenDetail are real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

const viewer: { closeRequest?: number; onDirtyChange?: (d: boolean) => void } = {};
vi.mock('../FileViewer', () => ({
  default: (p: { closeRequest?: number; onDirtyChange?: (d: boolean) => void }) => {
    viewer.closeRequest = p.closeRequest;
    viewer.onDirtyChange = p.onDirtyChange;
    return <div data-testid="file-viewer-stub" />;
  },
}));
const rail: { onStopBrowsing?: () => void } = {};
vi.mock('../CanvasOutputRail', async (orig) => {
  const real = await orig<typeof import('../CanvasOutputRail')>();
  return {
    ...real,
    CanvasOutputRail: (p: { onStopBrowsing?: () => void }) => {
      if (p.onStopBrowsing) rail.onStopBrowsing = p.onStopBrowsing;
      return <div data-testid="rail-stub" />;
    },
  };
});

const row = (path: string, firstSeen: number) => ({
  path, absolutePath: `/ws/${path}`, fileName: path, operation: 'written', firstSeen, count: 1,
  kind: 'content', change: 'new',
});
const browsed = { filePath: '/elsewhere/notes.md', fileName: 'notes.md' };
const base = {
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  setCollapse: vi.fn(),
  onRevealCanvas: vi.fn(),
  isOpen: true,
};

let opens: Array<Record<string, unknown>>;
const onOpen = (e: Event) => opens.push((e as CustomEvent).detail);
beforeEach(() => { opens = []; document.addEventListener('swarm:open-file', onOpen); });
afterEach(() => document.removeEventListener('swarm:open-file', onOpen));

describe('folded list header', () => {
  it('names the shown file and dots it while it has unsaved edits', () => {
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: true }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} />,
    );
    const cur = screen.getByTestId('canvas-current-file');
    expect(cur.textContent).toContain('notes.md');
    expect(cur.querySelector('.file-badge')?.textContent).toBe('MD');
    expect(screen.queryByTestId('canvas-current-dirty')).toBeNull();
    act(() => viewer.onDirtyChange?.(true));
    expect(screen.getByTestId('canvas-current-dirty')).toBeTruthy();
  });

  it('is absent while the list is shown', () => {
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: false }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} />,
    );
    expect(screen.queryByTestId('canvas-current-file')).toBeNull();
  });
});

describe('stop browsing (the Browsing row ×)', () => {
  it('with outputs: opens the first one, as a click would (not auto)', () => {
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: false }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [row('a.md', 1), row('b.md', 2)] } as any} initialFile={browsed as any} />,
    );
    const before = viewer.closeRequest;
    act(() => rail.onStopBrowsing?.());
    expect(opens).toHaveLength(1);
    expect(opens[0]).toMatchObject({ path: '/ws/b.md', tabId: 'tab-1' });
    expect(opens[0].auto).toBeUndefined();
    expect(viewer.closeRequest).toBe(before);
  });

  it('with no output: asks FileViewer to close the file (guarded close request)', () => {
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: false }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} />,
    );
    const before = viewer.closeRequest ?? 0;
    act(() => rail.onStopBrowsing?.());
    expect(opens).toEqual([]);
    expect(viewer.closeRequest).toBe(before + 1);
  });
});

describe('held-back open (unsaved edits, super-canvas-fixing.md C1)', () => {
  it('shows the prompt with both answers and reports the choice', () => {
    const onResolvePending = vi.fn();
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: false }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any}
        pendingOpen={{ filePath: '/ws/b.md', fileName: 'b.md' }} onResolvePending={onResolvePending} />,
    );
    const bar = screen.getByTestId('canvas-pending-open');
    expect(bar.textContent).toContain('notes.md');
    screen.getByTestId('canvas-pending-keep').click();
    screen.getByTestId('canvas-pending-discard').click();
    expect(screen.getByTestId('canvas-pending-discard').textContent).toContain('b.md');
    expect(onResolvePending.mock.calls).toEqual([[false], [true]]);
  });

  it('no prompt without a held-back open', () => {
    render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: false }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} pendingOpen={null} onResolvePending={vi.fn()} />,
    );
    expect(screen.queryByTestId('canvas-pending-open')).toBeNull();
  });

  it('the folded-header dot follows the slice when controlled', () => {
    const { rerender } = render(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: true }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} fileDirty={false} onFileDirtyChange={vi.fn()} />,
    );
    expect(screen.queryByTestId('canvas-current-dirty')).toBeNull();
    rerender(
      <FileViewerPanel {...base} collapse={{ railed: false, outputsCollapsed: true }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        referencedFiles={{ written: [] } as any} initialFile={browsed as any} fileDirty onFileDirtyChange={vi.fn()} />,
    );
    expect(screen.getByTestId('canvas-current-dirty')).toBeTruthy();
  });
});

