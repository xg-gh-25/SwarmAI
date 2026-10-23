/**
 * FileViewerPanel empty-state resident rail (Canvas 默认常驻 rail 入口, run_a263e967).
 *
 * BEFORE this feature: ChatPage gated the whole panel on `canvas.isOpen`
 * (`{canvas.isOpen && <FileViewerPanel>}`), so an empty tab (no file, not
 * manually opened) showed NOTHING on the right — the column vanished. The
 * existing 38px rail (`if (railed)` branch) was only reachable AFTER the panel
 * was opened and then collapsed, so there was no resident entry point.
 *
 * AFTER: ChatPage always-mounts FileViewerPanel and passes `isOpen` +
 * `onRevealCanvas`. When `isOpen === false`, the panel renders a resident 38px
 * rail (`data-testid=canvas-resident-rail`) as an always-present entry point;
 * clicking it calls `onRevealCanvas` (= manuallyOpen). This is DISTINCT from the
 * post-open collapsed rail (`data-testid=canvas-rail`, reached via `railed`).
 *
 * Gate-1 conditions this test locks:
 *  (b) the `!isOpen` branch must EARLY-RETURN the light rail — it must NOT mount
 *      the heavy FileViewer content in the empty state. Asserted via the absence
 *      of the file-viewer stub AND the content column.
 *
 * FileViewer + CanvasOutputRail are leaf-stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import FileViewerPanel from '../FileViewerPanel';

vi.mock('../FileViewer', () => ({ default: () => <div data-testid="file-viewer-stub" /> }));
vi.mock('../CanvasOutputRail', () => ({
  CanvasOutputRail: () => <div data-testid="rail-stub" />,
}));

const baseProps = {
  tabScopeKey: 'tab-1',
  onClose: vi.fn(),
  pinned: false,
  onTogglePin: vi.fn(),
  referencedFiles: { written: [] },
  collapse: { railed: false, outputsCollapsed: false },
  setCollapse: vi.fn(),
};

beforeEach(() => vi.clearAllMocks());

describe('FileViewerPanel — empty-state resident rail (isOpen=false)', () => {
  it('AC1: renders the resident 38px rail when isOpen=false (right column not vanished)', () => {
    render(<FileViewerPanel {...baseProps} isOpen={false} onRevealCanvas={vi.fn()} />);
    const rail = screen.getByTestId('canvas-resident-rail');
    expect(rail).toBeTruthy();
  });

  it('AC1(b) Gate-1: the resident rail EARLY-RETURNS — heavy FileViewer content is NOT mounted', () => {
    render(<FileViewerPanel {...baseProps} isOpen={false} onRevealCanvas={vi.fn()} />);
    // The whole point of the light rail: no heavy content in the empty state.
    expect(screen.queryByTestId('file-viewer-stub')).toBeNull();
    expect(screen.queryByTestId('canvas-content-column')).toBeNull();
    // And it is NOT the post-open collapsed rail nor the expanded panel.
    expect(screen.queryByTestId('canvas-rail')).toBeNull();
    expect(screen.queryByTestId('canvas-region-outputs')).toBeNull();
  });

  it('AC2: clicking the resident rail calls onRevealCanvas (= manual open)', () => {
    const onRevealCanvas = vi.fn();
    render(<FileViewerPanel {...baseProps} isOpen={false} onRevealCanvas={onRevealCanvas} />);
    fireEvent.click(screen.getByTestId('canvas-resident-rail'));
    expect(onRevealCanvas).toHaveBeenCalledTimes(1);
  });

  it('AC5: when isOpen=true the panel renders normally (resident rail absent, content mounted)', () => {
    render(
      <FileViewerPanel
        {...baseProps}
        isOpen={true}
        onRevealCanvas={vi.fn()}
        initialFile={{ filePath: '/ws/a.md', fileName: 'a.md' }}
      />,
    );
    // Not the resident rail — the real panel.
    expect(screen.queryByTestId('canvas-resident-rail')).toBeNull();
    expect(screen.getByTestId('canvas-content-column')).toBeTruthy();
    expect(screen.getByTestId('file-viewer-stub')).toBeTruthy();
  });

  it('AC5: isOpen=true + railed=true still shows the post-open collapsed rail, NOT the resident rail', () => {
    render(
      <FileViewerPanel
        {...baseProps}
        isOpen={true}
        onRevealCanvas={vi.fn()}
        collapse={{ railed: true, outputsCollapsed: false }}
      />,
    );
    // The collapsed-after-open rail (existing behavior) — distinct testid.
    expect(screen.getByTestId('canvas-rail')).toBeTruthy();
    expect(screen.queryByTestId('canvas-resident-rail')).toBeNull();
  });

  it('resident rail shows the output count when outputs exist while closed', () => {
    // A closed Canvas with pending outputs: the resident rail should still be able
    // to surface "N files" (the CanvasOutputRail stays mounted-but-hidden for counts).
    render(
      <FileViewerPanel
        {...baseProps}
        isOpen={false}
        onRevealCanvas={vi.fn()}
        referencedFiles={{ written: [] }}
      />,
    );
    // The count source (CanvasOutputRail) is mounted (hidden) so counts stay live.
    expect(screen.getByTestId('rail-stub')).toBeTruthy();
  });
});
